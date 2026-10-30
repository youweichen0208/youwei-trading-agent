"""Immutable model review submissions; no approval or champion authority."""

from typing import Annotated, Literal
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb
from pydantic import Field

from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash
from yta_alpha.model_evaluation_contracts import EvaluationId, ModelId, Timestamp
from yta_alpha.model_evaluation_registry import ModelEvaluationRegistry
from yta_alpha.models import SHA256, Contract, DataMode


class ProposalRequest(Contract):
    candidate_evaluation_id: EvaluationId
    reference_evaluation_id: EvaluationId | None = None
    rationale: Annotated[str, Field(min_length=1, max_length=2000, pattern=r"\S")]


class ProposalPayload(ProposalRequest):
    schema_version: Literal["model-proposal-v1"]
    status: Literal["SUBMITTED"]
    qualification: Literal["UNVERIFIED"]
    owner_id: UUID
    request_id: UUID
    data_mode: DataMode
    candidate_model_id: ModelId
    candidate_report_hash: SHA256
    reference_model_id: ModelId | None
    reference_report_hash: SHA256 | None
    cohort_hash: SHA256


class ProposalResponse(Contract):
    id: UUID
    created_at: Timestamp
    content_hash: SHA256
    proposal: ProposalPayload


def proposal_payload(owner_id, request_id, mode, request, candidate, reference):
    if candidate["cohort"]["data_mode"] != mode.value:
        raise DomainError("MODEL_EVALUATION_NOT_FOUND", "Evaluation not found", status=404)
    if reference is not None and (
        reference["cohort"] != candidate["cohort"]
        or reference["cohort_hash"] != candidate["cohort_hash"]
        or reference["protocol_version"] != candidate["protocol_version"]
        or reference["return_basis"] != candidate["return_basis"]
    ):
        raise DomainError("EVALUATION_COHORT_MISMATCH", "Proposal evidence must share a cohort")
    if reference is not None and reference["model_id"] == candidate["model_id"]:
        raise DomainError("INVALID_PROPOSAL", "Reference must be a different model")
    return {
        "schema_version": "model-proposal-v1",
        "status": "SUBMITTED",
        "qualification": "UNVERIFIED",
        "owner_id": str(owner_id),
        "request_id": str(request_id),
        "data_mode": mode.value,
        **request.model_dump(mode="json"),
        "candidate_model_id": candidate["model_id"],
        "candidate_report_hash": candidate["content_hash"],
        "reference_model_id": reference["model_id"] if reference else None,
        "reference_report_hash": reference["content_hash"] if reference else None,
        "cohort_hash": candidate["cohort_hash"],
    }


def verify_proposal(row, candidate, reference):
    payload = row["payload"]
    request = ProposalRequest(
        candidate_evaluation_id=row["candidate_evaluation_id"],
        reference_evaluation_id=row["reference_evaluation_id"],
        rationale=payload["rationale"],
    )
    if (
        "model-evaluation-experimental-" + candidate["content_hash"]
        != request.candidate_evaluation_id
        or (reference is None) != (request.reference_evaluation_id is None)
        or (
            reference is not None
            and "model-evaluation-experimental-" + reference["content_hash"]
            != request.reference_evaluation_id
        )
        or content_hash(request.model_dump(mode="json")) != row["request_hash"]
        or content_hash(payload) != row["content_hash"]
        or proposal_payload(
            row["owner_id"],
            row["request_id"],
            DataMode(row["data_mode"]),
            request,
            candidate,
            reference,
        )
        != payload
    ):
        raise DomainError("MODEL_PROPOSAL_INTEGRITY_ERROR", "Proposal evidence differs")
    return {
        "id": str(row["id"]),
        "created_at": row["created_at"].isoformat(),
        "content_hash": row["content_hash"],
        "proposal": payload,
    }


class ModelProposalRegistry:
    def __init__(self, url, calendar):
        self.reports = ModelEvaluationRegistry(url, calendar)

    def _connect(self):
        return self.reports.calibrations.models._connect()

    def create(self, owner_id: UUID, request_id: UUID, mode: DataMode, request: ProposalRequest):
        owner_id, request_id = UUID(str(owner_id)), UUID(str(request_id))
        digest = content_hash(request.model_dump(mode="json"))
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT id,request_hash FROM yta.model_proposals "
                "WHERE owner_id=%s AND data_mode=%s AND request_id=%s",
                (owner_id, mode.value, request_id),
            ).fetchone()
        if existing:
            if existing["request_hash"] != digest:
                raise DomainError("IDEMPOTENCY_CONFLICT", "Proposal request differs", status=409)
            return self.get(owner_id, mode, existing["id"])
        candidate = self.reports.get(request.candidate_evaluation_id, data_mode=mode)
        reference = (
            self.reports.get(request.reference_evaluation_id, data_mode=mode)
            if request.reference_evaluation_id
            else None
        )
        payload = proposal_payload(owner_id, request_id, mode, request, candidate, reference)
        with self._connect() as connection:
            inserted = connection.execute(
                "INSERT INTO yta.model_proposals(id,owner_id,request_id,data_mode,"
                "candidate_evaluation_id,reference_evaluation_id,"
                "request_hash,content_hash,payload) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id",
                (
                    uuid4(),
                    owner_id,
                    request_id,
                    mode.value,
                    request.candidate_evaluation_id,
                    request.reference_evaluation_id,
                    digest,
                    content_hash(payload),
                    Jsonb(payload),
                ),
            ).fetchone()
            row = connection.execute(
                "SELECT * FROM yta.model_proposals "
                "WHERE owner_id=%s AND data_mode=%s AND request_id=%s",
                (owner_id, mode.value, request_id),
            ).fetchone()
            if row["request_hash"] != digest:
                raise DomainError("IDEMPOTENCY_CONFLICT", "Proposal request differs", status=409)
            result = verify_proposal(row, candidate, reference)
            if inserted:
                connection.execute(
                    "INSERT INTO yta.audit_events(actor_id,action,resource_id,payload) "
                    "VALUES(%s,'model.proposal_submitted',%s,%s)",
                    (
                        owner_id,
                        str(row["id"]),
                        Jsonb({"content_hash": row["content_hash"], "qualification": "UNVERIFIED"}),
                    ),
                )
            return result

    def get(self, owner_id: UUID, mode: DataMode, identity: UUID):
        with self._connect() as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            row = connection.execute(
                "SELECT * FROM yta.model_proposals WHERE id=%s AND owner_id=%s AND data_mode=%s",
                (identity, owner_id, mode.value),
            ).fetchone()
        if row is None:
            raise DomainError("MODEL_PROPOSAL_NOT_FOUND", "Proposal not found", status=404)
        candidate = self.reports.get(row["candidate_evaluation_id"], data_mode=mode)
        reference = (
            self.reports.get(row["reference_evaluation_id"], data_mode=mode)
            if row["reference_evaluation_id"]
            else None
        )
        return verify_proposal(row, candidate, reference)
