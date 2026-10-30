from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, StrictStr, model_validator
from yta_alpha.models import SHA256, Contract, DataMode

WireDecimal = Annotated[str, Field(strict=True, pattern=r"^-?\d+(?:\.\d+)?$")]


class ChartBar(Contract):
    session: date
    open: WireDecimal
    high: WireDecimal
    low: WireDecimal
    close: WireDecimal
    volume: WireDecimal
    split_factor: WireDecimal


class IndicatorResult(Contract):
    symbol: Literal["NVDA"]
    provider: Annotated[StrictStr, Field(min_length=1)]
    as_of: AwareDatetime
    data_mode: DataMode
    adjustment: Literal["SPLIT_ONLY_AS_OF"]
    schema_version: Literal["indicators-v3"]
    share_basis_session: date
    data_snapshot_hash: SHA256
    bars: Annotated[list[ChartBar], Field(min_length=1)]
    definitions: dict[str, StrictStr]
    warmup_policy: StrictStr
    sessions: Annotated[list[date], Field(min_length=1)]
    series: Annotated[dict[str, list[WireDecimal | None]], Field(min_length=1)]

    @model_validator(mode="after")
    def alignment(self):
        if self.sessions != [bar.session for bar in self.bars] or any(
            len(values) != len(self.sessions) for values in self.series.values()
        ):
            raise ValueError("Indicator arrays and chart sessions do not align")
        return self


class ScopedResult(Contract):
    schema_version: Literal["research-tool-result-v1"]
    tool_call_id: UUID
    context_id: UUID
    tool: Literal["market_snapshot", "indicators"]
    data_mode: DataMode
    as_of: AwareDatetime
    data_snapshot_hash: SHA256
    source_id: Annotated[StrictStr, Field(min_length=1)]
    available_at: AwareDatetime
    result: dict

    @model_validator(mode="after")
    def available(self):
        if self.available_at > self.as_of:
            raise ValueError("Evidence is after the research cutoff")
        return self
