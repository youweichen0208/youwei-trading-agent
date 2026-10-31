"""Append-only, evidence-derived Massive snapshots shared by research and Jobs.

This reader never calls the network. Publication requires retained daily-summary
responses, complete acquisition pagination, a bounded Nasdaq calendar check, and
explicit evidence of account, usage and corporate-action qualification.
"""

from datetime import date, datetime
from decimal import Decimal, localcontext
from typing import Literal

from psycopg.types.json import Jsonb
from pydantic import ValidationError

from yta_alpha.acquisition_quality import assess
from yta_alpha.acquisition_store import AcquisitionStore, StoredAcquisition, acquisition_connection
from yta_alpha.calendar import TradingCalendar
from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash, validate_snapshot
from yta_alpha.massive import RawPage, aggregate_day
from yta_alpha.models import SHA256, Contract, DataMode, Forecast, MarketBar, MarketSnapshot, Split
from yta_alpha.nasdaq_qualification import verify

POLICY = "massive-nvda-raw-personal-v2"
REVISION_POLICY = "append-only-reacquisition-v1"


class QualificationApproval(Contract):
    version: Literal["massive-local-approval-v1"] = "massive-local-approval-v1"
    account_evidence_hash: SHA256
    usage_permission_hash: SHA256
    entitlement_start: date
    entitlement_end: date
    actions_start: date
    actions_end: date
    revision_policy: Literal[REVISION_POLICY] = REVISION_POLICY


def validate_approval(approval: QualificationApproval, stored: StoredAcquisition):
    if (
        approval.entitlement_start > stored.start
        or approval.entitlement_end < stored.end
        or approval.actions_start > stored.start
        or approval.actions_end < stored.end
    ):
        raise ValueError("Qualification approval does not cover the acquisition window")
    if approval.revision_policy != REVISION_POLICY:
        raise ValueError("Unsupported market revision policy")


def qualify(
    stored: StoredAcquisition,
    digest: str,
    summaries: tuple[RawPage, ...],
    calendar: TradingCalendar,
    approval: QualificationApproval,
) -> tuple[MarketSnapshot, dict]:
    acquisition = stored.acquisition
    validate_approval(approval, stored)
    quality = assess(stored, calendar)
    if quality["status"] != "CHECKS_PASSED" or quality["not_yet_due_sessions"]:
        raise ValueError("Acquisition has gaps or incomplete sessions")
    rows = [
        (aggregate_day(row["t"]), row, page)
        for page in acquisition.bars
        for row in page.payload().get("results", [])
    ]
    if len(rows) < 21:
        raise ValueError("Qualification requires at least 21 sessions")
    # Verify the forecast horizon as well as every historical session used.
    calendar_policy = verify(calendar, stored.start, calendar.after(rows[-1][0], 5))
    if len(summaries) != 21:
        raise ValueError("Exactly 21 current daily-summary responses required")
    proofs = []
    for (day, row, _), summary in zip(rows[-21:], summaries, strict=True):
        body = summary.payload()
        if (
            summary.path != f"/v1/open-close/NVDA/{day}"
            or summary.query != (("adjusted", "false"),)
            or body.get("status") != "OK"
            or body.get("symbol") != "NVDA"
            or body.get("from") != day.isoformat()
            or body.get("close") != row["c"]
            or summary.retrieved_at.tzinfo is None
            or summary.retrieved_at < calendar.close_at(day)
        ):
            raise ValueError("Daily summary scope, finality or close mismatch")
        proofs.append(
            {
                "path": summary.path,
                "query": [list(q) for q in summary.query],
                "retrieved_at": summary.retrieved_at.isoformat(),
                "body": summary.body.decode("utf-8"),
                "hash": summary.content_hash,
            }
        )
    # AcquisitionStore.load has already checked original query bounds, all page
    # links, terminal pages and normalized actions. Empty lists are accepted only
    # as part of that complete scoped corporate-action response chain.
    if not acquisition.splits or acquisition.splits[-1].payload().get("next_url"):
        raise ValueError("Corporate-action pagination incomplete")
    as_of = max(p.retrieved_at for p in (*acquisition.bars, *acquisition.splits, *summaries))
    bars = []
    for day, row, page in rows:
        volume = Decimal(row["v"])
        bars.append(
            MarketBar(
                session=day,
                open=row["o"],
                high=row["h"],
                low=row["l"],
                close=row["c"],
                volume=volume,
                available_at=page.retrieved_at,
                retrieved_at=page.retrieved_at,
                revision=page.content_hash,
            )
        )
    with localcontext() as context:
        context.prec = 50
        splits = tuple(
            Split(
                effective_session=e.effective_date,
                new_shares_per_old=e.split_to / e.split_from,
                available_at=e.available_at,
                source_id=f"massive:{e.raw_page_hash}:{e.provider_id}",
            )
            for e in acquisition.split_events
        )
    evidence = {
        "policy": POLICY,
        "approval": approval.model_dump(mode="json"),
        "calendar_policy": calendar_policy,
        "calendar_version": calendar.version,
        "quality": quality,
        "daily_summaries": proofs,
        "acquisition_hash": digest,
        "actions_coverage": {
            "start": stored.start.isoformat(),
            "end": stored.end.isoformat(),
            "page_hashes": [p.content_hash for p in acquisition.splits],
        },
    }
    snapshot = MarketSnapshot(
        symbol="NVDA",
        provider="massive",
        source_id=f"massive:{digest}:{content_hash(evidence)}",
        data_mode=stored.data_mode,
        adjustment="RAW",
        as_of=as_of,
        bars=tuple(bars),
        splits=splits,
        actions_verified_through=stored.end,
    )
    validate_snapshot(snapshot, calendar)
    return snapshot, evidence


class QualifiedMarket:
    def __init__(self, database_url: str, calendar: TradingCalendar, mode: DataMode):
        self.url, self.calendar, self.mode = database_url, calendar, mode

    def publish(
        self,
        digest: str,
        summaries: tuple[RawPage, ...],
        approval: QualificationApproval,
    ) -> str:
        raw = AcquisitionStore(self.url).load(digest, data_mode=self.mode)
        snapshot, evidence = qualify(raw, digest, summaries, self.calendar, approval)
        payload = snapshot.model_dump(mode="json")
        identity = content_hash({"snapshot": payload, "evidence": evidence})
        with acquisition_connection(self.url) as connection:
            inserted = connection.execute(
                "INSERT INTO yta.market_qualifications "
                "(hash,acquisition_hash,data_mode,as_of,evidence,snapshot) "
                "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING hash",
                (
                    identity,
                    digest,
                    self.mode.value,
                    snapshot.as_of,
                    Jsonb(evidence),
                    Jsonb(payload),
                ),
            ).fetchone()
            if inserted:
                connection.execute(
                    "INSERT INTO yta.audit_events(action,resource_id,payload) "
                    "VALUES ('market.qualified',%s,%s)",
                    (identity, Jsonb({"acquisition_hash": digest, "data_mode": self.mode.value})),
                )
        return identity

    def latest(self, now: datetime) -> MarketSnapshot:
        with acquisition_connection(self.url) as connection:
            row = connection.execute(
                "SELECT hash,acquisition_hash,evidence,snapshot FROM yta.market_qualifications "
                "WHERE data_mode=%s AND as_of<=%s ORDER BY as_of DESC,hash DESC LIMIT 1",
                (self.mode.value, now),
            ).fetchone()
        if row is None:
            raise DomainError("DATA_UNAVAILABLE", "No qualified Massive snapshot", status=503)
        identity, digest, evidence, payload = row
        if content_hash({"snapshot": payload, "evidence": evidence}) != identity:
            raise ValueError("Qualified snapshot integrity failed")
        summaries = tuple(
            RawPage(
                p["path"],
                tuple(tuple(q) for q in p["query"]),
                datetime.fromisoformat(p["retrieved_at"]),
                p["body"].encode(),
            )
            for p in evidence["daily_summaries"]
        )
        raw = AcquisitionStore(self.url).load(digest, data_mode=self.mode)
        try:
            approval = QualificationApproval.model_validate(evidence.get("approval"))
            validate_approval(approval, raw)
        except (ValidationError, ValueError):
            raise DomainError(
                "DATA_UNAVAILABLE",
                "Massive qualification approval is missing or invalid",
                status=503,
            ) from None
        snapshot, checked = qualify(raw, digest, summaries, self.calendar, approval)
        if (
            checked != evidence
            or snapshot.model_dump(mode="json") != payload
            or snapshot.as_of > now
        ):
            raise ValueError("Qualified evidence cannot be reproduced")
        return snapshot

    def observation(self, prediction: Forecast, now: datetime) -> MarketSnapshot | None:
        try:
            snapshot = self.latest(now)
        except DomainError as error:
            if error.code == "DATA_UNAVAILABLE":
                return None
            raise
        if (
            prediction.data_mode != self.mode
            or snapshot.bars[0].session > prediction.base_session
            or not any(b.session == prediction.target_session for b in snapshot.bars)
        ):
            return None
        return snapshot
