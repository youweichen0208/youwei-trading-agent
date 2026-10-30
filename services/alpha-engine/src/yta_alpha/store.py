"""Durable research facts. Internal service callers supply an authenticated owner.

The public BFF must derive owner_id from its login session, never from browser JSON.
Each write is a transaction. Advisory locks serialize request/draft/prediction identities
without granting UPDATE rights on append-only tables.
"""

from datetime import datetime
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from yta_alpha.calendar import TradingCalendar
from yta_alpha.errors import DomainError
from yta_alpha.evidence import ensure_index, metadata, read_index
from yta_alpha.forecast import MODEL_CONFIG, content_hash, forecast, settle, snapshot_hash
from yta_alpha.models import DataMode, Forecast, MarketSnapshot, ToolMarketSnapshot


class ResearchStore:
    def __init__(self, url: str, calendar: TradingCalendar, git_sha: str):
        self.url = url
        self.calendar = calendar
        self.git_sha = git_sha

    def _connect(self):
        return psycopg.connect(self.url, row_factory=dict_row, connect_timeout=5)

    @staticmethod
    def _lock(connection, identity: str) -> None:
        connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (identity,))

    @staticmethod
    def _save_snapshot(connection, snapshot: MarketSnapshot) -> str:
        digest = snapshot_hash(snapshot)
        connection.execute(
            "INSERT INTO yta.data_snapshots(hash, data_mode, provider, payload) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
            (
                digest,
                snapshot.data_mode.value,
                snapshot.provider,
                Jsonb(snapshot.model_dump(mode="json")),
            ),
        )
        # Both the canonical fact and its index must agree on an exact-key retry.
        saved = connection.execute(
            "SELECT payload FROM yta.data_snapshots WHERE hash=%s", (digest,)
        ).fetchone()
        if saved["payload"] != snapshot.model_dump(mode="json"):
            raise DomainError("EVIDENCE_CONFLICT", "Stored snapshot conflicts with content hash")
        ensure_index(connection, digest, snapshot)
        return digest

    def create_draft(
        self, owner: UUID, snapshot: MarketSnapshot, now: datetime, request_id: UUID | None = None
    ) -> dict:
        draft_id = uuid4()
        with self._connect() as connection:
            if request_id is not None:
                self._lock(connection, f"draft-request:{owner}:{request_id}")
                prior = connection.execute(
                    "SELECT d.id, d.forecast FROM yta.draft_requests r "
                    "JOIN yta.prediction_drafts d ON d.id=r.draft_id "
                    "WHERE r.owner_id=%s AND r.request_id=%s",
                    (owner, request_id),
                ).fetchone()
                if prior:
                    return {"draft_id": str(prior["id"]), "forecast": prior["forecast"]}
            result = forecast(snapshot, now=now, calendar=self.calendar, git_sha=self.git_sha)
            self._save_snapshot(connection, snapshot)
            connection.execute(
                "INSERT INTO yta.model_versions(id, model_kind, sha256, config) "
                "VALUES (%s, 'BASELINE', %s, %s) ON CONFLICT DO NOTHING",
                (result.model_version, result.model_sha256, Jsonb(MODEL_CONFIG)),
            )
            registered = connection.execute(
                "SELECT sha256 FROM yta.model_versions WHERE id = %s", (result.model_version,)
            ).fetchone()
            if registered["sha256"] != result.model_sha256:
                raise DomainError(
                    "MODEL_VERSION_CONFLICT", "Model config changed without a new version"
                )
            connection.execute(
                "INSERT INTO yta.prediction_drafts "
                "(id, owner_id, data_snapshot_hash, model_version, forecast, expires_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    draft_id,
                    owner,
                    result.data_snapshot_hash,
                    result.model_version,
                    Jsonb(result.model_dump(mode="json")),
                    result.expires_at,
                ),
            )
            if request_id is not None:
                connection.execute(
                    "INSERT INTO yta.draft_requests(owner_id, request_id, draft_id) "
                    "VALUES (%s,%s,%s)",
                    (owner, request_id, draft_id),
                )
        return {"draft_id": str(draft_id), "forecast": result.model_dump(mode="json")}

    def get_draft(self, owner: UUID, draft_id: UUID) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, forecast FROM yta.prediction_drafts WHERE owner_id=%s AND id=%s",
                (owner, draft_id),
            ).fetchone()
            if not row:
                raise DomainError("NOT_FOUND", "Draft not found", status=404)
            return {"draft_id": str(row["id"]), "forecast": row["forecast"]}

    def draft_evidence(self, owner: UUID, draft_id: UUID, mode: DataMode) -> ToolMarketSnapshot:
        """Read immutable historical inputs, including after the draft expires."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT d.data_snapshot_hash, d.forecast, s.payload, s.provider, s.data_mode "
                "FROM yta.prediction_drafts d JOIN yta.data_snapshots s "
                "ON s.hash=d.data_snapshot_hash "
                "WHERE d.id=%s AND d.owner_id=%s AND d.forecast->>'data_mode'=%s",
                (draft_id, owner, mode.value),
            ).fetchone()
            indexed = read_index(connection, row["data_snapshot_hash"]) if row else None
        if not row:
            raise DomainError("NOT_FOUND", "Draft evidence not found", status=404)
        try:
            snapshot = MarketSnapshot.model_validate(row["payload"])
            saved = Forecast.model_validate(row["forecast"])
            valid = (
                snapshot_hash(snapshot) == row["data_snapshot_hash"] == saved.data_snapshot_hash
                and snapshot.data_mode == mode == saved.data_mode
                and row["data_mode"] == mode.value
                and snapshot.provider == row["provider"]
                and snapshot.as_of == saved.as_of
                and (indexed is None or indexed == metadata(snapshot))
            )
        except ValidationError:
            valid = False
        if not valid:
            raise DomainError("EVIDENCE_INVALID", "Stored evidence failed verification", status=503)
        return ToolMarketSnapshot(snapshot=snapshot, data_snapshot_hash=row["data_snapshot_hash"])

    def freeze(
        self,
        owner: UUID,
        draft_id: UUID,
        key: str,
        *,
        confirmed: bool,
        now: datetime,
        supersedes_prediction_id: UUID | None = None,
    ) -> dict:
        if not key or len(key) > 128:
            raise DomainError("INVALID_IDEMPOTENCY_KEY", "Key must contain 1–128 characters")
        request = {"draft_id": str(draft_id), "confirmed": confirmed}
        if supersedes_prediction_id is not None:
            request["supersedes_prediction_id"] = str(supersedes_prediction_id)
        digest = content_hash(request)
        with self._connect() as connection:
            self._lock(connection, f"freeze:{owner}:{key}")
            prior = connection.execute(
                "SELECT request_hash, prediction_id FROM yta.idempotency_records "
                "WHERE owner_id = %s AND operation = 'freeze' AND key = %s",
                (owner, key),
            ).fetchone()
            if prior:
                if prior["request_hash"] != digest:
                    raise DomainError(
                        "IDEMPOTENCY_CONFLICT", "Key was used for another request", status=409
                    )
                return self._prediction(connection, owner, prior["prediction_id"])
            if confirmed is not True:
                raise DomainError("CONFIRMATION_REQUIRED", "Explicit user confirmation is required")
            self._lock(connection, f"draft:{draft_id}")
            draft = connection.execute(
                "SELECT * FROM yta.prediction_drafts WHERE id = %s AND owner_id = %s",
                (draft_id, owner),
            ).fetchone()
            if not draft:
                raise DomainError("NOT_FOUND", "Draft not found", status=404)
            existing = connection.execute(
                "SELECT id,supersedes_prediction_id FROM yta.predictions "
                "WHERE draft_id = %s AND owner_id = %s",
                (draft_id, owner),
            ).fetchone()
            if existing:
                if existing["supersedes_prediction_id"] != supersedes_prediction_id:
                    raise DomainError(
                        "IDEMPOTENCY_CONFLICT",
                        "Draft already frozen with another correction reference",
                        status=409,
                    )
                prediction_id = existing["id"]
            else:
                prediction = Forecast.model_validate(draft["forecast"])
                if now.tzinfo is None or now < prediction.as_of:
                    raise DomainError("INVALID_TIME", "Clock precedes research cutoff")
                if now >= prediction.expires_at:
                    raise DomainError(
                        "DRAFT_EXPIRED", "Research again after the next EOD bar", status=409
                    )
                if self.calendar.latest_closed(now) != prediction.base_session:
                    raise DomainError(
                        "STALE_DATA", "Draft no longer uses the latest closed session"
                    )
                if supersedes_prediction_id is not None:
                    original = self._prediction(connection, owner, supersedes_prediction_id)
                    source = Forecast.model_validate(original["forecast"])
                    comparable = (
                        "data_mode",
                        "symbol",
                        "horizon_sessions",
                        "base_session",
                        "target_session",
                        "protocol_version",
                    )
                    if any(
                        getattr(source, field) != getattr(prediction, field) for field in comparable
                    ):
                        raise DomainError(
                            "INVALID_CORRECTION",
                            "Correction must describe the same prediction window",
                            status=409,
                        )
                    if (
                        now >= source.expires_at
                        or now < datetime.fromisoformat(original["created_at"])
                        or prediction.as_of < source.as_of
                    ):
                        raise DomainError(
                            "INVALID_CORRECTION",
                            "Correction is outside the original research window",
                            status=409,
                        )
                prediction_id = uuid4()
                connection.execute(
                    "INSERT INTO yta.predictions "
                    "(id, owner_id, draft_id, data_mode, data_snapshot_hash, model_version, "
                    "as_of, created_at, base_session, target_session, forecast, "
                    "supersedes_prediction_id) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        prediction_id,
                        owner,
                        draft_id,
                        prediction.data_mode.value,
                        prediction.data_snapshot_hash,
                        prediction.model_version,
                        prediction.as_of,
                        now,
                        prediction.base_session,
                        prediction.target_session,
                        Jsonb(draft["forecast"]),
                        supersedes_prediction_id,
                    ),
                )
                self._event(
                    connection,
                    prediction_id,
                    "FROZEN",
                    "freeze",
                    {"supersedes_prediction_id": str(supersedes_prediction_id)}
                    if supersedes_prediction_id
                    else {},
                    now,
                )
                connection.execute(
                    "INSERT INTO yta.audit_events(actor_id, action, resource_id, payload) "
                    "VALUES (%s, 'prediction.freeze', %s, %s)",
                    (
                        owner,
                        str(prediction_id),
                        Jsonb(
                            {
                                "snapshot_hash": prediction.data_snapshot_hash,
                                "supersedes_prediction_id": str(supersedes_prediction_id)
                                if supersedes_prediction_id
                                else None,
                            }
                        ),
                    ),
                )
            connection.execute(
                "INSERT INTO yta.idempotency_records "
                "(owner_id, operation, key, request_hash, prediction_id) "
                "VALUES (%s, 'freeze', %s, %s, %s)",
                (owner, key, digest, prediction_id),
            )
            return self._prediction(connection, owner, prediction_id)

    @staticmethod
    def _event(connection, prediction_id, kind, key, payload, now):
        connection.execute(
            "INSERT INTO yta.prediction_events "
            "(prediction_id, type, dedupe_key, payload, occurred_at) VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT DO NOTHING",
            (prediction_id, kind, key, Jsonb(payload), now),
        )

    @staticmethod
    def _prediction(connection, owner, prediction_id) -> dict:
        row = connection.execute(
            "SELECT * FROM yta.predictions WHERE id = %s AND owner_id = %s",
            (prediction_id, owner),
        ).fetchone()
        if not row:
            raise DomainError("NOT_FOUND", "Prediction not found", status=404)
        outcomes = connection.execute(
            "SELECT settlement_version, status, result, observed_at FROM yta.prediction_outcomes "
            "WHERE prediction_id = %s ORDER BY settlement_version",
            (prediction_id,),
        ).fetchall()
        events = connection.execute(
            "SELECT sequence, type, payload, occurred_at FROM yta.prediction_events "
            "WHERE prediction_id = %s ORDER BY sequence",
            (prediction_id,),
        ).fetchall()
        return {
            "prediction_id": str(row["id"]),
            "draft_id": str(row["draft_id"]),
            "supersedes_prediction_id": str(row["supersedes_prediction_id"])
            if row["supersedes_prediction_id"]
            else None,
            "created_at": row["created_at"].isoformat(),
            "forecast": row["forecast"],
            "status": events[-1]["type"] if events else "FROZEN",
            "outcomes": [{**o, "observed_at": o["observed_at"].isoformat()} for o in outcomes],
            "events": [{**e, "occurred_at": e["occurred_at"].isoformat()} for e in events],
        }

    def get_prediction(self, owner: UUID, prediction_id: UUID) -> dict:
        with self._connect() as connection:
            return self._prediction(connection, owner, prediction_id)

    def list_predictions(
        self, owner: UUID, *, limit: int = 100, data_mode: str | None = None
    ) -> list[dict]:
        if not 1 <= limit <= 100:
            raise DomainError("INVALID_LIMIT", "Limit must be between 1 and 100")
        with self._connect() as connection:
            ids = connection.execute(
                "SELECT id FROM yta.predictions WHERE owner_id = %s "
                "AND (%s::text IS NULL OR data_mode=%s) "
                "ORDER BY created_at DESC, id DESC LIMIT %s",
                (owner, data_mode, data_mode, limit),
            ).fetchall()
            return [self._prediction(connection, owner, p["id"]) for p in ids]

    def prediction_page(
        self,
        owner: UUID,
        *,
        data_mode: str,
        cursor: UUID | None = None,
        limit: int = 25,
        status: str | None = None,
    ) -> dict:
        if not 1 <= limit <= 100:
            raise DomainError("INVALID_LIMIT", "Limit must be between 1 and 100", status=422)
        if status not in (
            None,
            "FROZEN",
            "SETTLED",
            "UNSCORABLE",
            "WAITING_DATA",
            "SETTLEMENT_FAILED",
        ):
            raise DomainError("INVALID_REQUEST", "Unknown prediction status", status=422)
        with self._connect() as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            if (
                cursor
                and not connection.execute(
                    "SELECT 1 FROM yta.predictions WHERE id=%s AND owner_id=%s AND data_mode=%s",
                    (cursor, owner, data_mode),
                ).fetchone()
            ):
                raise DomainError("NOT_FOUND", "Prediction cursor not found", status=404)
            rows = connection.execute(
                "SELECT p.id FROM yta.predictions p LEFT JOIN LATERAL ("
                "SELECT type FROM yta.prediction_events WHERE prediction_id=p.id "
                "ORDER BY sequence DESC LIMIT 1) e ON true "
                "WHERE p.owner_id=%s AND p.data_mode=%s "
                "AND (%s::uuid IS NULL OR (p.created_at,p.id) < "
                "(SELECT created_at,id FROM yta.predictions WHERE id=%s)) "
                "AND (%s::text IS NULL OR COALESCE(e.type,'FROZEN')=%s) "
                "ORDER BY p.created_at DESC,p.id DESC LIMIT %s",
                (owner, data_mode, cursor, cursor, status, status, limit + 1),
            ).fetchall()
            return {
                "items": [self._prediction(connection, owner, row["id"]) for row in rows[:limit]],
                "next_cursor": str(rows[limit - 1]["id"]) if len(rows) > limit else None,
            }

    def settle_prediction(
        self,
        owner: UUID,
        prediction_id: UUID,
        observation: MarketSnapshot,
        *,
        now: datetime,
        base_revision: MarketSnapshot | None = None,
        supersedes_version: int | None = None,
    ) -> dict:
        with self._connect() as connection:
            self._lock(connection, f"settle:{prediction_id}")
            saved = self._prediction(connection, owner, prediction_id)
            prediction = Forecast.model_validate(saved["forecast"])
            try:
                result = settle(
                    prediction,
                    observation,
                    now=now,
                    calendar=self.calendar,
                    base_revision=base_revision,
                )
            except DomainError as error:
                if error.code != "WAITING_DATA":
                    raise
                # A retry after a successful result cannot erase the settled state.
                if not saved["outcomes"]:
                    self._event(connection, prediction_id, "WAITING_DATA", "waiting-data", {}, now)
                return self._prediction(connection, owner, prediction_id)
            revision_hash = None
            if base_revision is not None:
                original = connection.execute(
                    "SELECT provider FROM yta.data_snapshots WHERE hash=%s",
                    (prediction.data_snapshot_hash,),
                ).fetchone()
                if original["provider"] != base_revision.provider:
                    raise DomainError(
                        "COHORT_MISMATCH", "Base revision must cite original provider"
                    )
                revision_hash = self._save_snapshot(connection, base_revision)
            snapshot_id = self._save_snapshot(connection, observation)
            input_hash = content_hash(
                {
                    "prediction_id": str(prediction_id),
                    "snapshot_hash": snapshot_id,
                    "calculation_version": result.calculation_version,
                    **({"base_revision_snapshot_hash": revision_hash} if revision_hash else {}),
                }
            )
            prior = connection.execute(
                "SELECT id FROM yta.prediction_outcomes "
                "WHERE prediction_id = %s AND input_hash = %s",
                (prediction_id, input_hash),
            ).fetchone()
            if not prior:
                latest = saved["outcomes"][-1] if saved["outcomes"] else None
                if (
                    any(o["result"].get("base_revision_snapshot_hash") for o in saved["outcomes"])
                    and base_revision is None
                ):
                    raise DomainError(
                        "BASE_REVISION_REQUIRED", "Carry the revised base into new settlements"
                    )
                if base_revision is not None and supersedes_version != (
                    latest["settlement_version"] if latest else None
                ):
                    raise DomainError(
                        "OUTCOME_VERSION_CONFLICT",
                        "Base revision must cite latest outcome",
                        status=409,
                    )
                if latest and now < datetime.fromisoformat(latest["observed_at"]):
                    raise DomainError("INVALID_TIME", "Outcome revision time cannot move backwards")
                next_version = len(saved["outcomes"]) + 1
                outcome_id = uuid4()
                connection.execute(
                    "INSERT INTO yta.prediction_outcomes "
                    "(id, prediction_id, settlement_version, input_hash, data_snapshot_hash, "
                    "status, result, observed_at) VALUES (%s,%s,%s,%s,%s,'SETTLED',%s,%s)",
                    (
                        outcome_id,
                        prediction_id,
                        next_version,
                        input_hash,
                        snapshot_id,
                        Jsonb(result.model_dump(mode="json")),
                        now,
                    ),
                )
                if revision_hash:
                    connection.execute(
                        "INSERT INTO yta.outcome_base_revisions(outcome_id,snapshot_hash) "
                        "VALUES (%s,%s)",
                        (outcome_id, revision_hash),
                    )
                self._event(
                    connection,
                    prediction_id,
                    "SETTLED",
                    input_hash,
                    {"settlement_version": next_version},
                    now,
                )
                connection.execute(
                    "INSERT INTO yta.audit_events(actor_id, action, resource_id, payload) "
                    "VALUES (%s, 'prediction.settle', %s, %s)",
                    (owner, str(prediction_id), Jsonb({"settlement_version": next_version})),
                )
            return self._prediction(connection, owner, prediction_id)
