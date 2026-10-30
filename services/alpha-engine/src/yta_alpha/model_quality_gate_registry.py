"""Immutable combined screening facts with owner-scoped replay and transactional audit."""

from uuid import UUID

from psycopg.types.json import Jsonb

from yta_alpha.errors import DomainError
from yta_alpha.model_quality_gate import ModelQualityGateRequest, ModelQualityGateService
from yta_alpha.models import DataMode

PREFIX = "model-quality-gate-"


def verify_gate(row, expected):
    if (
        row["payload"] != expected
        or row["content_hash"] != expected["content_hash"]
        or row["id"] != PREFIX + expected["content_hash"]
        or str(row["proposal_id"]) != expected["proposal_id"]
        or row["price_assessment_id"] != expected["price_assessment_id"]
        or row["probability_assessment_id"] != expected["probability_assessment_id"]
        or row["data_mode"] != expected["data_mode"]
    ):
        raise DomainError(
            "MODEL_QUALITY_GATE_INTEGRITY_ERROR", "Combined gate cannot be reproduced"
        )
    return expected


class ModelQualityGateRegistry:
    def __init__(self, url, calendar):
        self.service = ModelQualityGateService(url, calendar)

    def _connect(self):
        return self.service.price._connect()

    def create(self, owner_id, mode, request):
        return self._create(owner_id, owner_id, mode, request)

    def _create(self, owner_id, actor_id, mode, request):
        owner_id, actor_id, mode = UUID(str(owner_id)), UUID(str(actor_id)), DataMode(mode)
        expected = self.service.evaluate(owner_id, mode, request)
        identity = PREFIX + expected["content_hash"]
        with self._connect() as connection:
            inserted = connection.execute(
                "INSERT INTO yta.model_quality_gates "
                "(id,proposal_id,price_assessment_id,probability_assessment_id,"
                "data_mode,content_hash,payload) VALUES(%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT DO NOTHING RETURNING id",
                (
                    identity,
                    expected["proposal_id"],
                    expected["price_assessment_id"],
                    expected["probability_assessment_id"],
                    mode.value,
                    expected["content_hash"],
                    Jsonb(expected),
                ),
            ).fetchone()
            row = connection.execute(
                "SELECT * FROM yta.model_quality_gates WHERE id=%s", (identity,)
            ).fetchone()
            if row is None:
                raise DomainError("MODEL_QUALITY_GATE_INTEGRITY_ERROR", "Combined gate missing")
            verify_gate(row, expected)
            if inserted:
                connection.execute(
                    "INSERT INTO yta.audit_events(actor_id,action,resource_id,payload) "
                    "VALUES(%s,'model.quality_gate_registered',%s,%s)",
                    (
                        actor_id,
                        identity,
                        Jsonb(
                            {
                                "proposal_id": expected["proposal_id"],
                                "numeric_pass": expected["numeric_pass"],
                                "promotion_allowed": False,
                            }
                        ),
                    ),
                )
        return identity

    def get(self, owner_id, mode, identity):
        owner_id, mode = UUID(str(owner_id)), DataMode(mode)
        with self._connect() as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            row = connection.execute(
                "SELECT g.* FROM yta.model_quality_gates g "
                "JOIN yta.model_proposals p ON p.id=g.proposal_id "
                "WHERE g.id=%s AND g.data_mode=%s AND p.data_mode=%s AND p.owner_id=%s",
                (identity, mode.value, mode.value, owner_id),
            ).fetchone()
        if row is None:
            raise DomainError("MODEL_QUALITY_GATE_NOT_FOUND", "Combined gate not found", status=404)
        request = ModelQualityGateRequest(
            proposal_id=row["proposal_id"],
            price_assessment_id=row["price_assessment_id"],
            probability_assessment_id=row["probability_assessment_id"],
        )
        return verify_gate(row, self.service.evaluate(owner_id, mode, request))

    def _proposal_owner(self, proposal_id, mode):
        with self._connect() as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            row = connection.execute(
                "SELECT owner_id FROM yta.model_proposals WHERE id=%s AND data_mode=%s",
                (UUID(str(proposal_id)), DataMode(mode).value),
            ).fetchone()
        if row is None:
            raise DomainError("MODEL_PROPOSAL_NOT_FOUND", "Proposal not found", status=404)
        return row["owner_id"]

    def create_for_review(self, reviewer_id, mode, request):
        """Trusted BFF must authorize reviewer; never accept an owner override."""
        request = ModelQualityGateRequest.model_validate(
            request.model_dump(mode="json")
            if isinstance(request, ModelQualityGateRequest)
            else request
        )
        owner = self._proposal_owner(request.proposal_id, mode)
        return self._create(owner, reviewer_id, mode, request)

    def get_for_review(self, mode, identity):
        """Trusted BFF must authorize reviewer before cross-owner evidence access."""
        with self._connect() as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            row = connection.execute(
                "SELECT proposal_id FROM yta.model_quality_gates WHERE id=%s AND data_mode=%s",
                (identity, DataMode(mode).value),
            ).fetchone()
        if row is None:
            raise DomainError("MODEL_QUALITY_GATE_NOT_FOUND", "Combined gate not found", status=404)
        return self.get(self._proposal_owner(row["proposal_id"], mode), mode, identity)
