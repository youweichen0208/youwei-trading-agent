"""Immutable, replayable historical quality rules and assessments, never approvals."""

from psycopg.types.json import Jsonb

from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash
from yta_alpha.model_evaluation_registry import ModelEvaluationRegistry
from yta_alpha.model_quality import HistoricalQualityPolicy, assess_historical_quality

POLICY_PREFIX = "historical-quality-policy-"
ASSESSMENT_PREFIX = "historical-quality-assessment-"


def verify_policy(row):
    policy = HistoricalQualityPolicy.model_validate(row["payload"])
    payload = policy.model_dump(mode="json")
    digest = content_hash(payload)
    if (
        payload != row["payload"]
        or row["content_hash"] != digest
        or row["id"] != POLICY_PREFIX + digest
        or row["policy_version"] != policy.policy_version
        or row["data_mode"] != policy.data_mode.value
        or row["horizon_sessions"] != policy.horizon_sessions
    ):
        raise DomainError("QUALITY_INTEGRITY_ERROR", "Policy identity differs")
    return policy


def verify_assessment(row, policy, candidate, reference):
    expected = assess_historical_quality(candidate, policy, reference)
    if (
        row["payload"] != expected
        or row["content_hash"] != expected["content_hash"]
        or row["id"] != ASSESSMENT_PREFIX + expected["content_hash"]
        or row["policy_id"] != POLICY_PREFIX + expected["policy_hash"]
        or row["data_mode"] != policy.data_mode.value
        or row["candidate_evaluation_id"] != expected["candidate_evaluation_id"]
        or row["reference_evaluation_id"] != expected["reference_evaluation_id"]
    ):
        raise DomainError("QUALITY_INTEGRITY_ERROR", "Assessment cannot be reproduced")
    return expected


class ModelQualityRegistry:
    def __init__(self, url, calendar):
        self.reports = ModelEvaluationRegistry(url, calendar)

    def _connect(self):
        return self.reports.calibrations.models._connect()

    def register_policy(self, value):
        policy = HistoricalQualityPolicy.model_validate(value)
        payload = policy.model_dump(mode="json")
        digest = content_hash(payload)
        identity = POLICY_PREFIX + digest
        with self._connect() as connection:
            inserted = connection.execute(
                "INSERT INTO yta.model_quality_policies "
                "(id,policy_version,data_mode,horizon_sessions,content_hash,payload) "
                "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id",
                (
                    identity,
                    policy.policy_version,
                    policy.data_mode.value,
                    policy.horizon_sessions,
                    digest,
                    Jsonb(payload),
                ),
            ).fetchone()
            row = connection.execute(
                "SELECT * FROM yta.model_quality_policies "
                "WHERE policy_version=%s AND data_mode=%s AND horizon_sessions=%s",
                (policy.policy_version, policy.data_mode.value, policy.horizon_sessions),
            ).fetchone()
            if row is None or verify_policy(row) != policy:
                raise DomainError(
                    "QUALITY_POLICY_CONFLICT",
                    "Policy version already binds other rules",
                    status=409,
                )
            if inserted:
                self._audit(
                    connection,
                    "model.quality_policy_registered",
                    identity,
                    {"policy_hash": digest, "data_mode": policy.data_mode.value},
                )
        return identity

    def get_policy(self, identity, *, data_mode=None):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM yta.model_quality_policies WHERE id=%s", (identity,)
            ).fetchone()
        if row is None or (data_mode is not None and row["data_mode"] != data_mode.value):
            raise DomainError("QUALITY_POLICY_NOT_FOUND", "Policy not found", status=404)
        return verify_policy(row)

    @staticmethod
    def _audit(connection, action, identity, payload):
        connection.execute(
            "INSERT INTO yta.audit_events(actor_id,action,resource_id,payload) "
            "VALUES(NULL,%s,%s,%s)",
            (action, identity, Jsonb(payload)),
        )

    def create(self, policy_id, candidate_id, *, reference_id=None):
        policy = self.get_policy(policy_id)
        candidate = self.reports.get(candidate_id, data_mode=policy.data_mode)
        reference = (
            self.reports.get(reference_id, data_mode=policy.data_mode) if reference_id else None
        )
        result = assess_historical_quality(candidate, policy, reference)
        identity = ASSESSMENT_PREFIX + result["content_hash"]
        with self._connect() as connection:
            inserted = connection.execute(
                "INSERT INTO yta.model_quality_assessments "
                "(id,policy_id,candidate_evaluation_id,reference_evaluation_id,"
                "data_mode,content_hash,payload) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id",
                (
                    identity,
                    policy_id,
                    candidate_id,
                    reference_id,
                    policy.data_mode.value,
                    result["content_hash"],
                    Jsonb(result),
                ),
            ).fetchone()
            row = connection.execute(
                "SELECT * FROM yta.model_quality_assessments WHERE id=%s", (identity,)
            ).fetchone()
            if row is None:
                raise DomainError("QUALITY_INTEGRITY_ERROR", "Assessment registration missing")
            verify_assessment(row, policy, candidate, reference)
            if inserted:
                self._audit(
                    connection,
                    "model.quality_assessment_registered",
                    identity,
                    {
                        "policy_id": policy_id,
                        "numeric_pass": result["numeric_pass"],
                        "promotion_allowed": False,
                    },
                )
        return identity

    def get(self, identity, *, data_mode=None):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM yta.model_quality_assessments WHERE id=%s", (identity,)
            ).fetchone()
        if row is None or (data_mode is not None and row["data_mode"] != data_mode.value):
            raise DomainError("QUALITY_ASSESSMENT_NOT_FOUND", "Assessment not found", status=404)
        policy = self.get_policy(row["policy_id"], data_mode=data_mode)
        candidate = self.reports.get(row["candidate_evaluation_id"], data_mode=policy.data_mode)
        reference = (
            self.reports.get(row["reference_evaluation_id"], data_mode=policy.data_mode)
            if row["reference_evaluation_id"]
            else None
        )
        return verify_assessment(row, policy, candidate, reference)
