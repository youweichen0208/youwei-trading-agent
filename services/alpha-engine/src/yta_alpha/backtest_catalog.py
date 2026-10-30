"""Bounded report discovery; native inference replay happens on fixed detail reads."""

import re
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field

from yta_alpha.backtest_contracts import BacktestId, BacktestReport, Count, HistoricalMode
from yta_alpha.backtest_registry import PREFIX, verify_backtest
from yta_alpha.errors import DomainError
from yta_alpha.model_evaluation_contracts import ModelId, Timestamp
from yta_alpha.models import SHA256, Contract, DataMode


class BacktestSummary(Contract):
    id: BacktestId
    content_hash: SHA256
    report_hash: SHA256
    data_mode: HistoricalMode
    registered_at: AwareDatetime
    cutoff: Timestamp
    model_ids: Annotated[tuple[ModelId, ...], Field(min_length=1, max_length=500)]
    status: Literal["COMPLETE", "INCOMPLETE"]
    signal_count: Annotated[int, Field(ge=1, le=500)]
    computed_count: Count
    evidence_unavailable_count: Count
    overlap_count: Count
    qualification: Literal["UNVERIFIED"] = "UNVERIFIED"
    verification: Literal["stored-integrity-checked-native-replay-on-detail"] = (
        "stored-integrity-checked-native-replay-on-detail"
    )


class BacktestPage(Contract):
    items: Annotated[tuple[BacktestSummary, ...], Field(max_length=50)]
    next_cursor: BacktestId | None


def summary(row, references):
    try:
        report = BacktestReport.model_validate(row["payload"]["report"])
        verify_backtest(row, report.model_dump(mode="json"), references)
        return BacktestSummary(
            id=row["id"],
            content_hash=row["content_hash"],
            report_hash=report.content_hash,
            data_mode=report.data_mode,
            registered_at=row["created_at"],
            cutoff=report.cutoff,
            model_ids=tuple(sorted(references)),
            status=report.status,
            signal_count=report.signal_count,
            computed_count=report.computed_count,
            evidence_unavailable_count=report.evidence_unavailable_count,
            overlap_count=report.overlap_count,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise DomainError(
            "BACKTEST_INTEGRITY_ERROR", "Stored catalog evidence is invalid"
        ) from error


def backtest_page(registry, mode, *, model_id=None, status=None, cursor=None, limit=20):
    mode = DataMode(mode)
    if mode == DataMode.LIVE_RESEARCH:
        raise DomainError("BACKTEST_COHORT_MISMATCH", "Backtests require a historical mode")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise DomainError("INVALID_PAGE", "Limit must be between 1 and 50")
    if model_id is not None and not re.fullmatch(r"lightgbm-experimental-[a-f0-9]{64}", model_id):
        raise DomainError("INVALID_PAGE", "Invalid model filter")
    if status not in (None, "COMPLETE", "INCOMPLETE"):
        raise DomainError("INVALID_PAGE", "Invalid status filter")
    if cursor is not None and not re.fullmatch(PREFIX + r"[a-f0-9]{64}", cursor):
        raise DomainError("INVALID_CURSOR", "Invalid backtest cursor")
    scope = (
        "b.data_mode=%s AND (%s::text IS NULL OR b.payload->'report'->>'status'=%s) "
        "AND (%s::text IS NULL OR EXISTS(SELECT 1 FROM yta.backtest_report_models m "
        "WHERE m.report_id=b.id AND m.model_id=%s))"
    )
    params = [mode.value, status, status, model_id, model_id]
    items = []
    with registry._connect() as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        anchor = None
        if cursor is not None:
            anchor = connection.execute(
                "SELECT b.id,b.created_at FROM yta.backtest_reports b WHERE b.id=%s AND " + scope,
                [cursor, *params],
            ).fetchone()
            if anchor is None:
                raise DomainError("INVALID_CURSOR", "Cursor is outside the backtest scope")
        query = "SELECT b.* FROM yta.backtest_reports b WHERE " + scope
        if anchor:
            query += " AND (b.created_at,b.id)<(%s,%s)"
            params.extend((anchor["created_at"], anchor["id"]))
        query += " ORDER BY b.created_at DESC,b.id DESC LIMIT %s"
        params.append(limit + 1)
        with connection.cursor(name="backtest_catalog") as stream:
            stream.itersize = 1
            stream.execute(query, params)
            for row in stream:
                if len(items) == limit:
                    return BacktestPage(items=tuple(items), next_cursor=items[-1].id)
                references = connection.execute(
                    "SELECT model_id FROM yta.backtest_report_models WHERE report_id=%s",
                    (row["id"],),
                ).fetchall()
                items.append(summary(row, [r["model_id"] for r in references]))
    return BacktestPage(items=tuple(items), next_cursor=None)
