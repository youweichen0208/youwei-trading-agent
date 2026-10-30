"""Immutable price-forecast evaluation with an explicit outcome-version manifest."""

import argparse
import json
from collections import Counter
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb
from pydantic import AwareDatetime, model_validator

from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash
from yta_alpha.models import Contract, DataMode
from yta_alpha.settings import Settings
from yta_alpha.store import ResearchStore


class EvaluationRequest(Contract):
    data_mode: DataMode
    base_from: date
    base_to: date
    cutoff: AwareDatetime

    @model_validator(mode="after")
    def ordered(self):
        if self.base_from > self.base_to:
            raise ValueError("base_from must not exceed base_to")
        return self


def summarize(rows: list[dict]) -> dict:
    """All arithmetic is Decimal; missing scores stay null, never zero-filled."""
    counts = Counter(row["status"] for row in rows)
    scored = [row for row in rows if row["status"] == "SETTLED"]
    with localcontext() as context:
        context.prec = 50

        def mean(values):
            if not values:
                return None
            value = sum(values, Decimal(0)) / Decimal(len(values))
            return str(value.quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN))

        metrics = {
            "direction_accuracy": mean(
                [Decimal(int(row["result"]["direction_correct"])) for row in scored]
            ),
            "mae": mean([Decimal(row["result"]["absolute_error"]) for row in scored]),
            "zero_return_mae": mean(
                [Decimal(row["result"]["zero_return_absolute_error"]) for row in scored]
            ),
        }
    return {
        "sample_count": len(rows),
        "scored_count": len(scored),
        "counts": {
            name: counts[name]
            for name in (
                "SETTLED",
                "UNSCORABLE",
                "WAITING_TARGET",
                "WAITING_DATA",
                "SETTLEMENT_FAILED",
            )
        },
        "base_session_counts": dict(sorted(Counter(r["base_session"] for r in rows).items())),
        **metrics,
    }


def create_report(store: ResearchStore, owner: UUID, request: EvaluationRequest) -> dict:
    if request.cutoff > datetime.now(UTC):
        raise DomainError("INVALID_TIME", "Evaluation cutoff cannot be in the future")
    # One SELECT gives a single MVCC snapshot for forecasts, outcomes and lifecycle events.
    # No arbitrary ID subset: every frozen forecast in the declared cohort is included.
    with store._connect() as connection:
        rows = connection.execute(
            "SELECT p.id, p.base_session, p.supersedes_prediction_id, p.forecast, "
            "o.id AS outcome_id, "
            "o.settlement_version, o.status AS outcome_status, o.result, e.type AS event_type "
            "FROM yta.predictions p LEFT JOIN LATERAL ("
            " SELECT * FROM yta.prediction_outcomes WHERE prediction_id=p.id "
            " AND observed_at <= %s ORDER BY settlement_version DESC LIMIT 1"
            ") o ON true LEFT JOIN LATERAL ("
            " SELECT type FROM yta.prediction_events WHERE prediction_id=p.id "
            " AND occurred_at <= %s ORDER BY sequence DESC LIMIT 1"
            ") e ON true WHERE p.owner_id=%s AND p.data_mode=%s "
            "AND p.base_session BETWEEN %s AND %s AND p.created_at <= %s "
            "AND p.model_version='momentum20-v1' "
            "AND p.forecast->>'protocol_version'='eod-price-v1' ORDER BY p.id",
            (
                request.cutoff,
                request.cutoff,
                owner,
                request.data_mode.value,
                request.base_from,
                request.base_to,
                request.cutoff,
            ),
        ).fetchall()
        manifest = []
        for row in rows:
            status = row["outcome_status"]
            if not status:
                if row["event_type"] == "SETTLEMENT_FAILED":
                    status = "SETTLEMENT_FAILED"
                elif datetime.fromisoformat(row["forecast"]["target_close_at"]) > request.cutoff:
                    status = "WAITING_TARGET"
                else:
                    status = "WAITING_DATA"
            manifest.append(
                {
                    "prediction_id": str(row["id"]),
                    "supersedes_prediction_id": str(row["supersedes_prediction_id"])
                    if row["supersedes_prediction_id"]
                    else None,
                    "base_session": row["base_session"].isoformat(),
                    "forecast_hash": content_hash(row["forecast"]),
                    "outcome_id": str(row["outcome_id"]) if row["outcome_id"] else None,
                    "settlement_version": row["settlement_version"],
                    "status": status,
                    "result": row["result"],
                }
            )
        payload = {
            **request.model_dump(mode="json"),
            "evaluation_version": "price-evaluation-v2",
            "correction_count": sum(
                row["supersedes_prediction_id"] is not None for row in manifest
            ),
            "protocol_version": "eod-price-v1",
            "model_version": "momentum20-v1",
            "git_sha": store.git_sha,
            "return_basis": "split-only-price-return",
            "cohort_policy": "all-owner-forecasts-in-base-range",
            "manifest": manifest,
            **summarize(manifest),
        }
        digest = content_hash(payload)
        inserted = connection.execute(
            "INSERT INTO yta.evaluation_reports(id,owner_id,content_hash,data_mode,payload) "
            "VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id",
            (uuid4(), owner, digest, request.data_mode.value, Jsonb(payload)),
        ).fetchone()
        row = connection.execute(
            "SELECT id,payload FROM yta.evaluation_reports WHERE owner_id=%s AND content_hash=%s",
            (owner, digest),
        ).fetchone()
        if inserted:
            connection.execute(
                "INSERT INTO yta.audit_events(actor_id,action,resource_id,payload) "
                "VALUES (%s,'evaluation.create',%s,%s)",
                (owner, str(row["id"]), Jsonb({"content_hash": digest})),
            )
        return {"report_id": str(row["id"]), "content_hash": digest, **row["payload"]}


def get_report(store: ResearchStore, owner: UUID, report_id: UUID) -> dict:
    with store._connect() as connection:
        row = connection.execute(
            "SELECT id,content_hash,payload FROM yta.evaluation_reports "
            "WHERE id=%s AND owner_id=%s",
            (report_id, owner),
        ).fetchone()
        if not row:
            raise DomainError("NOT_FOUND", "Evaluation report not found", status=404)
        return {"report_id": str(row["id"]), "content_hash": row["content_hash"], **row["payload"]}


def list_reports(
    store: ResearchStore,
    owner: UUID,
    data_mode: DataMode,
    *,
    cursor: UUID | None = None,
    limit: int = 25,
) -> dict:
    """Newest-first keyset pagination; cursor lookup shares the owner/mode scope."""
    if not 1 <= limit <= 100:
        raise DomainError("INVALID_REQUEST", "Page size must be between 1 and 100", status=422)
    with store._connect() as connection:
        boundary = None
        if cursor is not None:
            boundary = connection.execute(
                "SELECT created_at,id FROM yta.evaluation_reports "
                "WHERE id=%s AND owner_id=%s AND data_mode=%s",
                (cursor, owner, data_mode.value),
            ).fetchone()
            if boundary is None:
                raise DomainError("NOT_FOUND", "Report cursor not found", status=404)
        params = [owner, data_mode.value]
        clause = ""
        if boundary:
            clause = " AND (created_at,id) < (%s,%s)"
            params.extend([boundary["created_at"], boundary["id"]])
        params.append(limit + 1)
        rows = connection.execute(
            "SELECT id,content_hash,created_at,payload - 'manifest' - 'base_session_counts' "
            "AS summary FROM yta.evaluation_reports WHERE owner_id=%s AND data_mode=%s"
            + clause
            + " ORDER BY created_at DESC,id DESC LIMIT %s",
            params,
        ).fetchall()
    return {
        "items": [
            {
                "report_id": str(row["id"]),
                "content_hash": row["content_hash"],
                "created_at": row["created_at"].isoformat(),
                **row["summary"],
            }
            for row in rows[:limit]
        ],
        "next_cursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
    }


def main():
    from yta_alpha.calendar import TradingCalendar

    parser = argparse.ArgumentParser(description="Create an immutable price-forecast report")
    parser.add_argument("--owner", type=UUID, required=True)
    parser.add_argument("--base-from", type=date.fromisoformat, required=True)
    parser.add_argument("--base-to", type=date.fromisoformat, required=True)
    parser.add_argument("--cutoff", type=datetime.fromisoformat, required=True)
    args = parser.parse_args()
    settings = Settings()
    if settings.database_url is None:
        raise SystemExit("Database configuration required")
    store = ResearchStore(
        settings.database_url.get_secret_value(), TradingCalendar(), settings.git_sha
    )
    request = EvaluationRequest(
        data_mode=settings.data_mode,
        base_from=args.base_from,
        base_to=args.base_to,
        cutoff=args.cutoff,
    )
    print(json.dumps(create_report(store, args.owner, request), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
