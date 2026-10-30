"""Evidence-backed unscorable outcomes; only qualified internal ingestion calls this module.

Schema validation is not provider qualification. Browser/model input has no ingestion route.
A provider must retain its source document and prove that the target session cannot be scored.
"""

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb
from pydantic import AwareDatetime, Field, model_validator

from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash
from yta_alpha.models import SHA256, Contract, DataMode, Forecast
from yta_alpha.store import ResearchStore


class UnscorableEvidence(Contract):
    symbol: Literal["NVDA"] = "NVDA"
    data_mode: DataMode
    target_session: date
    reason: Literal["TARGET_HALTED_NO_CLOSE", "DELISTED_NO_PRICE", "UNRESOLVED_CORPORATE_ACTION"]
    provider: Annotated[str, Field(min_length=1, max_length=100)]
    source_id: Annotated[str, Field(min_length=1, max_length=500)]
    source_document_sha256: SHA256
    statement: Annotated[str, Field(min_length=20, max_length=10000)]
    published_at: AwareDatetime
    available_at: AwareDatetime
    retrieved_at: AwareDatetime

    @model_validator(mode="after")
    def chronology(self):
        if not self.published_at <= self.available_at <= self.retrieved_at:
            raise ValueError("Evidence timestamps must preserve availability order")
        return self


def record_unscorable(
    store: ResearchStore,
    owner: UUID,
    prediction_id: UUID,
    evidence: UnscorableEvidence,
    *,
    now: datetime,
    supersedes_version: int | None = None,
) -> dict:
    # Revalidate even when an internal caller used model_copy without validation.
    evidence = UnscorableEvidence.model_validate(evidence.model_dump(mode="json"))
    with store._connect() as connection:
        store._lock(connection, f"settle:{prediction_id}")
        saved = store._prediction(connection, owner, prediction_id)
        forecast = Forecast.model_validate(saved["forecast"])
        if now.tzinfo is None:
            raise DomainError("INVALID_TIME", "Clock must include timezone")
        if now < forecast.target_close_at:
            raise DomainError("NOT_DUE", "Target session has not closed", status=409)
        if (
            evidence.symbol != forecast.symbol
            or evidence.data_mode != forecast.data_mode
            or evidence.target_session != forecast.target_session
        ):
            raise DomainError("COHORT_MISMATCH", "Evidence must identify the exact forecast target")
        if evidence.retrieved_at > now:
            raise DomainError("FUTURE_INFORMATION", "Evidence has not yet been retrieved")
        if evidence.available_at < forecast.target_close_at:
            raise DomainError(
                "INCOMPLETE_EVIDENCE", "Target-session conclusion must follow its close"
            )
        payload = evidence.model_dump(mode="json")
        evidence_hash = content_hash(payload)
        input_hash = content_hash(
            {
                "prediction_id": str(prediction_id),
                "evidence_hash": evidence_hash,
                "calculation_version": "unscorable-evidence-v1",
            }
        )
        if connection.execute(
            "SELECT 1 FROM yta.prediction_outcomes WHERE prediction_id=%s AND input_hash=%s",
            (prediction_id, input_hash),
        ).fetchone():
            return saved
        latest = saved["outcomes"][-1] if saved["outcomes"] else None
        if supersedes_version != (latest["settlement_version"] if latest else None):
            raise DomainError(
                "OUTCOME_VERSION_CONFLICT", "Reclassification must cite latest version", status=409
            )
        if latest and now < datetime.fromisoformat(latest["observed_at"]):
            raise DomainError("INVALID_TIME", "Outcome revisions cannot move observation time back")
        version = len(saved["outcomes"]) + 1
        outcome_id = uuid4()
        result = {
            "status": "UNSCORABLE",
            "reason": evidence.reason,
            "evidence_hash": evidence_hash,
            "evidence": payload,
            "data_snapshot_hash": forecast.data_snapshot_hash,
            "calculation_version": "unscorable-evidence-v1",
            "supersedes_settlement_version": supersedes_version,
            "actual_return": None,
            "actual_direction": None,
            "absolute_error": None,
            "direction_correct": None,
            "zero_return_absolute_error": None,
        }
        connection.execute(
            "INSERT INTO yta.settlement_evidence(hash,data_mode,payload) "
            "VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
            (evidence_hash, evidence.data_mode.value, Jsonb(payload)),
        )
        connection.execute(
            "INSERT INTO yta.prediction_outcomes "
            "(id,prediction_id,settlement_version,input_hash,data_snapshot_hash,"
            "status,result,observed_at) "
            "VALUES (%s,%s,%s,%s,%s,'UNSCORABLE',%s,%s)",
            (
                outcome_id,
                prediction_id,
                version,
                input_hash,
                forecast.data_snapshot_hash,
                Jsonb(result),
                now,
            ),
        )
        connection.execute(
            "INSERT INTO yta.outcome_evidence(outcome_id,evidence_hash) VALUES (%s,%s)",
            (outcome_id, evidence_hash),
        )
        store._event(
            connection,
            prediction_id,
            "UNSCORABLE",
            input_hash,
            {
                "settlement_version": version,
                "reason": evidence.reason,
                "evidence_hash": evidence_hash,
            },
            now,
        )
        connection.execute(
            "INSERT INTO yta.audit_events(actor_id,action,resource_id,payload) "
            "VALUES (%s,'prediction.unscorable',%s,%s)",
            (
                owner,
                str(prediction_id),
                Jsonb({"settlement_version": version, "evidence_hash": evidence_hash}),
            ),
        )
        return store._prediction(connection, owner, prediction_id)
