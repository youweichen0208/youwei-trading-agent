"""Bounded, mode-scoped metadata pages. Native model text never leaves this module."""

import re
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field

from yta_alpha.errors import DomainError
from yta_alpha.model_registry import PREFIX, ModelRegistry, verify_registration
from yta_alpha.models import SHA256, Contract, DataMode


class ModelSummary(Contract):
    id: str
    status: Literal["EXPERIMENTAL"] = "EXPERIMENTAL"
    qualification: Literal["UNVERIFIED"] = "UNVERIFIED"
    data_mode: DataMode
    horizon_sessions: Literal[1, 3, 5, 10, 20]
    registered_at: AwareDatetime
    training_cutoff: AwareDatetime
    model_sha256: SHA256
    registration_sha256: SHA256
    training_samples: int
    reserved_window_samples: int
    reserved_window_mae: Annotated[str, Field(pattern=r"^[0-9]+(?:\.[0-9]+)?$")]
    reserved_window_zero_return_mae: Annotated[str, Field(pattern=r"^[0-9]+(?:\.[0-9]+)?$")]
    metric_scope: Literal["RESERVED_WINDOW_NOT_QUALIFIED_FINAL_EVALUATION"] = (
        "RESERVED_WINDOW_NOT_QUALIFIED_FINAL_EVALUATION"
    )


class ModelPage(Contract):
    items: tuple[ModelSummary, ...]
    next_cursor: str | None


def model_page(
    registry: ModelRegistry,
    mode: DataMode,
    *,
    horizon: int | None = None,
    cursor: str | None = None,
    limit: int = 20,
) -> ModelPage:
    if type(limit) is not int or not 1 <= limit <= 50:
        raise DomainError("INVALID_PAGE", "Limit must be between 1 and 50")
    if horizon is not None and (type(horizon) is not int or horizon not in (1, 3, 5, 10, 20)):
        raise DomainError("UNSUPPORTED_HORIZON", "Unsupported model horizon")
    if cursor is not None and not re.fullmatch(PREFIX + r"[a-f0-9]{64}", cursor):
        raise DomainError("INVALID_CURSOR", "Invalid model cursor")
    scope = (mode.value, str(horizon) if horizon is not None else None)
    where = (
        "model_kind='TRAINED' AND config->'artifact'->>'data_mode'=%s "
        "AND (%s::text IS NULL OR config->'artifact'->>'horizon_sessions'=%s)"
    )
    rows = []
    with registry._connect() as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        anchor = None
        if cursor is not None:
            anchor = connection.execute(
                "SELECT created_at,id FROM yta.model_versions WHERE id=%s AND " + where,
                (cursor, scope[0], scope[1], scope[1]),
            ).fetchone()
            if anchor is None:
                raise DomainError("INVALID_CURSOR", "Model cursor does not belong to this scope")
        query = "SELECT id,sha256,config,created_at FROM yta.model_versions WHERE " + where
        params = [scope[0], scope[1], scope[1]]
        if anchor:
            query += " AND (created_at,id)<(%s,%s)"
            params.extend((anchor["created_at"], anchor["id"]))
        query += " ORDER BY created_at DESC,id DESC LIMIT %s"
        params.append(limit + 1)
        # Fetch one complete immutable artifact at a time so a page does not retain
        # dozens of native model strings in memory. Only the allowlisted DTO survives.
        with connection.cursor(name="experimental_model_catalog") as stream:
            stream.itersize = 1
            stream.execute(query, params)
            for row in stream:
                if len(rows) == limit:
                    return ModelPage(items=tuple(rows), next_cursor=rows[-1].id)
                artifact = verify_registration(row["id"], row["sha256"], row["config"]).artifact
                rows.append(
                    ModelSummary(
                        id=row["id"],
                        data_mode=artifact.data_mode,
                        horizon_sessions=artifact.horizon_sessions,
                        registered_at=row["created_at"],
                        training_cutoff=artifact.training_cutoff,
                        model_sha256=artifact.model_sha256,
                        registration_sha256=row["sha256"],
                        training_samples=len(artifact.fold.train),
                        reserved_window_samples=len(artifact.fold.test),
                        reserved_window_mae=format(artifact.test_mae, "f"),
                        reserved_window_zero_return_mae=format(artifact.zero_return_mae, "f"),
                    )
                )
    return ModelPage(items=tuple(rows), next_cursor=None)
