"""Read-only probability policy discovery; listing does not activate a rule."""

import re

from yta_alpha.errors import DomainError
from yta_alpha.models import Contract
from yta_alpha.probability_quality_contracts import ProbabilityPolicyId, ProbabilityPolicyResponse
from yta_alpha.probability_quality_registry import verify_policy


class ProbabilityPolicyPage(Contract):
    items: tuple[ProbabilityPolicyResponse, ...]
    next_cursor: ProbabilityPolicyId | None


def policy_page(registry, mode, *, horizon_sessions=None, cursor=None, limit=20):
    if type(limit) is not int or not 1 <= limit <= 50:
        raise DomainError("INVALID_PAGE", "Limit must be between 1 and 50")
    if horizon_sessions is not None and (
        type(horizon_sessions) is not int or horizon_sessions not in (1, 3, 5, 10, 20)
    ):
        raise DomainError("INVALID_HORIZON", "Unsupported policy horizon")
    if cursor is not None and (
        not isinstance(cursor, str)
        or not re.fullmatch(r"probability-quality-policy-[a-f0-9]{64}", cursor)
    ):
        raise DomainError("INVALID_CURSOR", "Invalid policy cursor")
    scope = "data_mode=%s"
    params = [mode.value]
    if horizon_sessions is not None:
        scope += " AND horizon_sessions=%s"
        params.append(horizon_sessions)
    with registry._connect() as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        anchor = None
        if cursor is not None:
            anchor = connection.execute(
                "SELECT id,created_at FROM yta.probability_quality_policies WHERE "
                + scope
                + " AND id=%s",
                (*params, cursor),
            ).fetchone()
            if anchor is None:
                raise DomainError("INVALID_CURSOR", "Cursor is outside policy scope")
        query = "SELECT * FROM yta.probability_quality_policies WHERE " + scope
        if anchor:
            query += " AND (created_at,id)<(%s,%s)"
            params.extend((anchor["created_at"], anchor["id"]))
        query += " ORDER BY created_at DESC,id DESC LIMIT %s"
        params.append(limit + 1)
        rows = connection.execute(query, params).fetchall()
        items = tuple(
            ProbabilityPolicyResponse(
                id=row["id"], content_hash=row["content_hash"], policy=verify_policy(row)
            )
            for row in rows[:limit]
        )
    return ProbabilityPolicyPage(
        items=items, next_cursor=items[-1].id if len(rows) > limit else None
    )
