"""Append-only review chains with expected predecessor and exact retry semantics."""

from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash
from yta_alpha.model_review import ModelReviewRequest, ModelReviewService, review_decision
from yta_alpha.model_review_v2 import (
    CombinedModelReviewService,
    CombinedReviewRequest,
    combined_review_decision,
)


class ReviewSubmission(ModelReviewRequest):
    expected_previous_review_id: UUID | None = None


class CombinedReviewSubmission(CombinedReviewRequest):
    expected_previous_review_id: UUID | None = None


def record_payload(decision, previous_id, revision):
    result = {
        **decision,
        "schema_version": "model-review-record-v2"
        if decision["schema_version"] == "model-review-decision-v2"
        else "model-review-record-v1",
        "previous_review_id": str(previous_id) if previous_id is not None else None,
        "revision": revision,
    }
    result.pop("content_hash")
    result["content_hash"] = content_hash(result)
    return result


def verify_review(row, proposal, assessment, previous, gate=None):
    from yta_alpha.models import DataMode

    version = row["payload"]["schema_version"]
    combined = version == "model-review-record-v2"
    if (
        version not in ("model-review-record-v1", "model-review-record-v2")
        or bool(row.get("quality_gate_id")) != combined
    ):
        raise DomainError("MODEL_REVIEW_INTEGRITY_ERROR", "Review evidence version differs")
    fields = {
        "proposal_id": row["proposal_id"],
        "action": row["payload"]["action"],
        "reason": row["payload"]["reason"],
        "expected_previous_review_id": row["previous_review_id"],
    }
    if combined:
        if gate is None or row["assessment_id"] != gate["price_assessment_id"]:
            raise DomainError("MODEL_REVIEW_INTEGRITY_ERROR", "Review combined evidence differs")
        submission = CombinedReviewSubmission(**fields, quality_gate_id=row["quality_gate_id"])
        request = CombinedReviewRequest.model_validate(
            submission.model_dump(exclude={"expected_previous_review_id"})
        )
        decision = combined_review_decision(
            row["reviewer_id"],
            row["request_id"],
            DataMode(row["data_mode"]),
            request,
            proposal,
            gate,
        )
    else:
        submission = ReviewSubmission(**fields, assessment_id=row["assessment_id"])
        request = ModelReviewRequest.model_validate(
            submission.model_dump(exclude={"expected_previous_review_id"})
        )
        decision = review_decision(
            row["reviewer_id"],
            row["request_id"],
            DataMode(row["data_mode"]),
            request,
            proposal,
            assessment,
        )
    valid_chain = (
        (row["revision"] == 1 and row["previous_review_id"] is None)
        if previous is None
        else (
            row["previous_review_id"] == previous["id"]
            and row["proposal_id"] == previous["proposal_id"]
            and row["data_mode"] == previous["data_mode"]
            and row["revision"] == previous["revision"] + 1
            and previous["payload"]["disposition"] != "REJECTED"
        )
    )
    expected = record_payload(decision, row["previous_review_id"], row["revision"])
    if (
        not valid_chain
        or expected != row["payload"]
        or row["content_hash"] != expected["content_hash"]
        or row["request_hash"] != content_hash(submission.model_dump(mode="json"))
    ):
        raise DomainError("MODEL_REVIEW_INTEGRITY_ERROR", "Review chain or evidence differs")
    return {"id": str(row["id"]), "created_at": row["created_at"].isoformat(), "review": expected}


class ModelReviewRegistry:
    def __init__(self, url, calendar):
        self.service = ModelReviewService(url, calendar)
        self.combined_service = CombinedModelReviewService(url, calendar)

    def _connect(self):
        return self.service.proposals._connect()

    @staticmethod
    def _existing(connection, reviewer, mode, key):
        return connection.execute(
            "SELECT * FROM yta.model_reviews WHERE reviewer_id=%s "
            "AND data_mode=%s AND request_id=%s",
            (reviewer, mode.value, key),
        ).fetchone()

    @staticmethod
    def _check_retry(row, digest):
        if row["request_hash"] != digest:
            raise DomainError(
                "IDEMPOTENCY_CONFLICT", "Review key binds another request", status=409
            )

    def create(self, reviewer_id, request_id, mode, submission):
        return self._create(reviewer_id, request_id, mode, submission, combined=False)

    def create_combined(self, reviewer_id, request_id, mode, submission):
        return self._create(reviewer_id, request_id, mode, submission, combined=True)

    def _create(self, reviewer_id, request_id, mode, submission, *, combined):
        submission_type = CombinedReviewSubmission if combined else ReviewSubmission
        reviewer, key = UUID(str(reviewer_id)), UUID(str(request_id))
        submission = submission_type.model_validate(
            submission.model_dump(mode="json")
            if isinstance(submission, (ReviewSubmission, CombinedReviewSubmission))
            else submission
        )
        digest = content_hash(submission.model_dump(mode="json"))
        with self._connect() as connection:
            existing = self._existing(connection, reviewer, mode, key)
        if existing:
            self._check_retry(existing, digest)
            return self.get(existing["id"], mode)
        request_type = CombinedReviewRequest if combined else ModelReviewRequest
        request = request_type.model_validate(
            submission.model_dump(exclude={"expected_previous_review_id"})
        )
        service = self.combined_service if combined else self.service
        decision = service.evaluate(reviewer, key, mode, request)
        with self._connect() as connection:
            # Serialize only this proposal. Facts remain INSERT-only; no row updates/locks required.
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                ("model-review:" + str(submission.proposal_id),),
            )
            existing = self._existing(connection, reviewer, mode, key)
            if existing:
                self._check_retry(existing, digest)
                identity = existing["id"]
            else:
                previous = connection.execute(
                    "SELECT * FROM yta.model_reviews WHERE proposal_id=%s "
                    "ORDER BY revision DESC LIMIT 1",
                    (submission.proposal_id,),
                ).fetchone()
                if (previous["id"] if previous else None) != submission.expected_previous_review_id:
                    raise DomainError(
                        "MODEL_REVIEW_STALE",
                        "Review state changed; reload before deciding",
                        status=409,
                    )
                if previous:
                    self.get(previous["id"], mode)
                if previous and previous["payload"]["disposition"] == "REJECTED":
                    raise DomainError(
                        "MODEL_REVIEW_CLOSED",
                        "Rejected proposal requires a new proposal",
                        status=409,
                    )
                revision = previous["revision"] + 1 if previous else 1
                payload = record_payload(decision, submission.expected_previous_review_id, revision)
                identity = uuid4()
                inserted = connection.execute(
                    "INSERT INTO yta.model_reviews(id,proposal_id,reviewer_id,request_id,data_mode,"
                    "assessment_id,previous_review_id,revision,request_hash,content_hash,payload,"
                    "quality_gate_id) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT DO NOTHING RETURNING id",
                    (
                        identity,
                        submission.proposal_id,
                        reviewer,
                        key,
                        mode.value,
                        decision["price_assessment_id"] if combined else submission.assessment_id,
                        submission.expected_previous_review_id,
                        revision,
                        digest,
                        payload["content_hash"],
                        Jsonb(payload),
                        submission.quality_gate_id if combined else None,
                    ),
                ).fetchone()
                if inserted:
                    connection.execute(
                        "INSERT INTO yta.audit_events(actor_id,action,resource_id,payload) "
                        "VALUES(%s,%s,%s,%s)",
                        (
                            reviewer,
                            "model.review_recorded",
                            str(identity),
                            Jsonb(
                                {
                                    "proposal_id": str(submission.proposal_id),
                                    "revision": revision,
                                    "disposition": payload["disposition"],
                                    "promotion_allowed": False,
                                }
                            ),
                        ),
                    )
                else:
                    existing = self._existing(connection, reviewer, mode, key)
                    if existing is None:
                        raise DomainError("MODEL_REVIEW_STALE", "Review state changed", status=409)
                    self._check_retry(existing, digest)
                    identity = existing["id"]
        return self.get(identity, mode)

    def get(self, identity, mode):
        return self._verified_chain(identity, mode)[-1]

    def _verified_chain(self, identity, mode):
        with self._connect() as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            row = connection.execute(
                "SELECT * FROM yta.model_reviews WHERE id=%s AND data_mode=%s",
                (UUID(str(identity)), mode.value),
            ).fetchone()
            if row is None:
                raise DomainError("MODEL_REVIEW_NOT_FOUND", "Review not found", status=404)
            chain, seen = [row], {row["id"]}
            while chain[-1]["previous_review_id"] is not None:
                previous = connection.execute(
                    "SELECT * FROM yta.model_reviews WHERE id=%s",
                    (chain[-1]["previous_review_id"],),
                ).fetchone()
                if previous is None or previous["id"] in seen:
                    raise DomainError("MODEL_REVIEW_INTEGRITY_ERROR", "Review chain is invalid")
                seen.add(previous["id"])
                chain.append(previous)
            owner = connection.execute(
                "SELECT owner_id FROM yta.model_proposals WHERE id=%s", (row["proposal_id"],)
            ).fetchone()["owner_id"]
        proposal = self.service.proposals.get(owner, mode, row["proposal_id"])
        previous, assessments, gates, results = None, {}, {}, []
        for entry in reversed(chain):
            assessment_id = entry["assessment_id"]
            if assessment_id not in assessments:
                assessments[assessment_id] = self.service.quality.get(assessment_id, data_mode=mode)
            gate = None
            if entry.get("quality_gate_id"):
                gate_id = entry["quality_gate_id"]
                if gate_id not in gates:
                    gates[gate_id] = self.combined_service.gates.get(owner, mode, gate_id)
                gate = gates[gate_id]
            result = verify_review(entry, proposal, assessments[assessment_id], previous, gate)
            results.append(result)
            previous = entry
        return results

    def history(self, proposal_id, mode, *, cursor=None, limit=20):
        """Newest first; cursors bind a proposal and mode, never a mutable offset."""
        proposal_id = UUID(str(proposal_id))
        if type(limit) is not int or not 1 <= limit <= 50:
            raise DomainError("INVALID_PAGE", "Limit must be between 1 and 50")
        with self._connect() as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            proposal = connection.execute(
                "SELECT owner_id FROM yta.model_proposals WHERE id=%s AND data_mode=%s",
                (proposal_id, mode.value),
            ).fetchone()
            if proposal is None:
                raise DomainError("MODEL_PROPOSAL_NOT_FOUND", "Proposal not found", status=404)
            if cursor is not None:
                anchor = connection.execute(
                    "SELECT previous_review_id FROM yta.model_reviews "
                    "WHERE id=%s AND proposal_id=%s AND data_mode=%s",
                    (UUID(str(cursor)), proposal_id, mode.value),
                ).fetchone()
                if anchor is None:
                    raise DomainError("INVALID_CURSOR", "Cursor is outside proposal scope")
                # Verify the cursor itself, including its predecessor binding.
                identity = UUID(str(cursor))
            else:
                head = connection.execute(
                    "SELECT id FROM yta.model_reviews WHERE proposal_id=%s AND data_mode=%s "
                    "ORDER BY revision DESC LIMIT 1",
                    (proposal_id, mode.value),
                ).fetchone()
                identity = head["id"] if head else None
        if identity is None:
            self.service.proposals.get(proposal["owner_id"], mode, proposal_id)
            return {"items": [], "next_cursor": None}
        chain = self._verified_chain(identity, mode)
        if cursor is not None:
            chain = chain[:-1]
        rows = list(reversed(chain))
        items = rows[:limit]
        return {"items": items, "next_cursor": items[-1]["id"] if len(rows) > limit else None}
