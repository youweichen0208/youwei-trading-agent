"""Mode-scoped report discovery; full semantic replay remains on detail reads."""

import re
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field

from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash
from yta_alpha.model_evaluation_contracts import ExperimentalPriceReport, ModelId, Timestamp
from yta_alpha.model_evaluation_registry import PREFIX, ModelEvaluationRegistry
from yta_alpha.model_registry import PREFIX as MODEL_PREFIX
from yta_alpha.models import SHA256, Contract, DataMode


class EvaluationSummary(Contract):
    id: str
    model_id: ModelId
    content_hash: SHA256
    cohort_hash: SHA256
    data_mode: DataMode
    horizon_sessions: Literal[1, 3, 5, 10, 20]
    registered_at: AwareDatetime
    cutoff: Timestamp
    sample_count: Annotated[int, Field(ge=1)]
    qualification: Literal["UNVERIFIED"] = "UNVERIFIED"


class EvaluationPage(Contract):
    items: tuple[EvaluationSummary, ...]
    next_cursor: str | None


def report_page(
    registry: ModelEvaluationRegistry, model_id, mode: DataMode, *, cursor=None, limit=20
):
    if type(limit) is not int or not 1 <= limit <= 50:
        raise DomainError("INVALID_PAGE", "Limit must be between 1 and 50")
    if not re.fullmatch(MODEL_PREFIX + r"[a-f0-9]{64}", model_id):
        raise DomainError("MODEL_NOT_FOUND", "Model not found", status=404)
    if cursor is not None and not re.fullmatch(PREFIX + r"[a-f0-9]{64}", cursor):
        raise DomainError("INVALID_CURSOR", "Invalid evaluation cursor")
    items = []
    with registry.calibrations.models._connect() as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        if not connection.execute(
            "SELECT id FROM yta.model_versions WHERE id=%s AND model_kind='TRAINED' "
            "AND config->'artifact'->>'data_mode'=%s",
            (model_id, mode.value),
        ).fetchone():
            raise DomainError("MODEL_NOT_FOUND", "Model not found", status=404)
        anchor = None
        if cursor is not None:
            anchor = connection.execute(
                "SELECT created_at,id FROM yta.model_evaluation_records "
                "WHERE id=%s AND model_version_id=%s AND data_mode=%s",
                (cursor, model_id, mode.value),
            ).fetchone()
            if anchor is None:
                raise DomainError("INVALID_CURSOR", "Cursor is outside the report scope")
        query = (
            "SELECT id,content_hash,payload,created_at FROM yta.model_evaluation_records "
            "WHERE model_version_id=%s AND data_mode=%s"
        )
        params = [model_id, mode.value]
        if anchor:
            query += " AND (created_at,id)<(%s,%s)"
            params.extend((anchor["created_at"], anchor["id"]))
        query += " ORDER BY created_at DESC,id DESC LIMIT %s"
        params.append(limit + 1)
        with connection.cursor(name="experimental_evaluation_catalog") as stream:
            stream.itersize = 1
            stream.execute(query, params)
            for row in stream:
                if len(items) == limit:
                    return EvaluationPage(items=tuple(items), next_cursor=items[-1].id)
                payload = row["payload"]
                report = ExperimentalPriceReport.model_validate(payload)
                unhashed = {key: value for key, value in payload.items() if key != "content_hash"}
                if (
                    report.model_id != model_id
                    or report.cohort.data_mode != mode
                    or report.content_hash != row["content_hash"]
                    or row["id"] != PREFIX + report.content_hash
                    or content_hash(unhashed) != report.content_hash
                    or content_hash(payload["cohort"]) != report.cohort_hash
                ):
                    raise DomainError("MODEL_EVALUATION_INTEGRITY_ERROR", "Report identity differs")
                items.append(
                    EvaluationSummary(
                        id=row["id"],
                        model_id=model_id,
                        content_hash=report.content_hash,
                        cohort_hash=report.cohort_hash,
                        data_mode=mode,
                        horizon_sessions=report.cohort.horizon_sessions,
                        registered_at=row["created_at"],
                        cutoff=report.cohort.cutoff,
                        sample_count=len(report.manifest),
                    )
                )
    return EvaluationPage(items=tuple(items), next_cursor=None)
