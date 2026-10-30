"""Owner/mode-scoped proposal discovery without repeating native inference."""

from uuid import UUID

from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash
from yta_alpha.model_proposals import ProposalPayload, ProposalRequest, ProposalResponse
from yta_alpha.models import Contract


class ProposalPage(Contract):
    items: tuple[ProposalResponse, ...]
    next_cursor: UUID | None


def proposal_metadata(row):
    payload = row["payload"]
    parsed = ProposalPayload.model_validate(payload)
    request = ProposalRequest(
        candidate_evaluation_id=row["candidate_evaluation_id"],
        reference_evaluation_id=row["reference_evaluation_id"],
        rationale=parsed.rationale,
    )
    reference_valid = (
        parsed.reference_model_id is None and parsed.reference_report_hash is None
        if parsed.reference_evaluation_id is None
        else parsed.reference_model_id is not None
        and parsed.reference_model_id != parsed.candidate_model_id
        and parsed.reference_evaluation_id
        == "model-evaluation-experimental-" + str(parsed.reference_report_hash)
    )
    if (
        parsed.model_dump(mode="json") != payload
        or parsed.owner_id != row["owner_id"]
        or parsed.request_id != row["request_id"]
        or parsed.data_mode.value != row["data_mode"]
        or parsed.candidate_evaluation_id != row["candidate_evaluation_id"]
        or parsed.reference_evaluation_id != row["reference_evaluation_id"]
        or parsed.candidate_evaluation_id
        != "model-evaluation-experimental-" + parsed.candidate_report_hash
        or not reference_valid
        or content_hash(payload) != row["content_hash"]
        or content_hash(request.model_dump(mode="json")) != row["request_hash"]
    ):
        raise DomainError("MODEL_PROPOSAL_INTEGRITY_ERROR", "Proposal identity differs")
    return ProposalResponse(
        id=row["id"],
        created_at=row["created_at"].isoformat(),
        content_hash=row["content_hash"],
        proposal=parsed,
    )


def proposal_page(registry, owner_id, mode, *, cursor=None, limit=20):
    return _page(registry, mode, UUID(str(owner_id)), cursor=cursor, limit=limit)


def review_proposal_page(registry, mode, *, cursor=None, limit=20):
    """Privileged discovery: trusted BFF must authorize its reviewer session."""
    return _page(registry, mode, None, cursor=cursor, limit=limit)


def review_proposal(registry, mode, identity):
    with registry._connect() as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        row = connection.execute(
            "SELECT owner_id FROM yta.model_proposals WHERE id=%s AND data_mode=%s",
            (UUID(str(identity)), mode.value),
        ).fetchone()
    if row is None:
        raise DomainError("MODEL_PROPOSAL_NOT_FOUND", "Proposal not found", status=404)
    return registry.get(row["owner_id"], mode, identity)


def _page(registry, mode, owner_id, *, cursor, limit):
    if type(limit) is not int or not 1 <= limit <= 50:
        raise DomainError("INVALID_PAGE", "Limit must be between 1 and 50")
    if cursor is not None:
        try:
            cursor = UUID(str(cursor))
        except ValueError as error:
            raise DomainError("INVALID_CURSOR", "Invalid proposal cursor") from error
    with registry._connect() as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        scope = "data_mode=%s"
        scope_params = [mode.value]
        if owner_id is not None:
            scope += " AND owner_id=%s"
            scope_params.append(owner_id)
        anchor = None
        if cursor is not None:
            anchor = connection.execute(
                "SELECT id,created_at FROM yta.model_proposals WHERE id=%s AND " + scope,
                [cursor, *scope_params],
            ).fetchone()
            if anchor is None:
                raise DomainError("INVALID_CURSOR", "Cursor is outside proposal scope")
        query = "SELECT * FROM yta.model_proposals WHERE " + scope
        params = list(scope_params)
        if anchor:
            query += " AND (created_at,id)<(%s,%s)"
            params.extend((anchor["created_at"], anchor["id"]))
        query += " ORDER BY created_at DESC,id DESC LIMIT %s"
        params.append(limit + 1)
        rows = connection.execute(query, params).fetchall()
        items = tuple(proposal_metadata(row) for row in rows[:limit])
        return ProposalPage(items=items, next_cursor=items[-1].id if len(rows) > limit else None)
