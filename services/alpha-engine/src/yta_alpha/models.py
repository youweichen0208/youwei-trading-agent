from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

PROTOCOL_VERSION = "eod-price-v1"
MODEL_VERSION = "momentum20-v1"
FEATURE_SCHEMA = "daily-price-v1"
CALCULATION_VERSION = "settlement-price-v1"
SHA256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Price = Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]


class DataMode(StrEnum):
    LIVE_RESEARCH = "LIVE_RESEARCH"
    REPLAY = "REPLAY"
    DEMO = "DEMO"


class Direction(StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    FLAT = "FLAT"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MarketBar(Contract):
    session: date
    open: Price
    high: Price
    low: Price
    close: Price
    volume: Annotated[StrictInt, Field(ge=0)]
    available_at: AwareDatetime
    retrieved_at: AwareDatetime
    revision: Annotated[str, Field(min_length=1, max_length=100)]

    @field_validator("open", "high", "low", "close", mode="before")
    @classmethod
    def decimal_transport(cls, value: object) -> object:
        # Binary JSON floats cannot represent provider decimal precision faithfully.
        if isinstance(value, (float, bool)):
            raise ValueError("Prices must be decimal strings, never binary floats")
        return value

    @model_validator(mode="after")
    def ohlc_and_time(self) -> "MarketBar":
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError("Invalid OHLC price ordering")
        if self.retrieved_at < self.available_at:
            raise ValueError("retrieved_at precedes available_at")
        return self


class Split(Contract):
    effective_session: date
    new_shares_per_old: Price
    available_at: AwareDatetime
    source_id: Annotated[str, Field(min_length=1)]

    @field_validator("new_shares_per_old", mode="before")
    @classmethod
    def ratio_transport(cls, value: object) -> object:
        if isinstance(value, (float, bool)):
            raise ValueError("Split ratios must be decimal strings")
        return value


class MarketSnapshot(Contract):
    symbol: Literal["NVDA"]
    provider: Annotated[str, Field(min_length=1, max_length=100)]
    source_id: Annotated[str, Field(min_length=1)]
    data_mode: DataMode
    adjustment: Literal["RAW"]
    as_of: AwareDatetime
    bars: Annotated[tuple[MarketBar, ...], Field(min_length=1, max_length=10000)]
    splits: tuple[Split, ...] = ()
    # Provider must attest coverage; an empty list alone cannot prove there were no actions.
    actions_verified_through: date

    @model_validator(mode="after")
    def ordered(self) -> "MarketSnapshot":
        sessions = [b.session for b in self.bars]
        if sessions != sorted(set(sessions)):
            raise ValueError("Bars must have unique ascending sessions")
        split_dates = [a.effective_session for a in self.splits]
        if split_dates != sorted(set(split_dates)):
            raise ValueError("Splits must have unique ascending effective sessions")
        return self


class ToolMarketSnapshot(Contract):
    snapshot: MarketSnapshot
    data_snapshot_hash: SHA256


class Forecast(Contract):
    symbol: Literal["NVDA"] = "NVDA"
    horizon_sessions: Literal[5] = 5
    data_mode: DataMode
    as_of: AwareDatetime
    expires_at: AwareDatetime
    base_session: date
    base_close: Price
    base_revision: str
    target_session: date
    target_close_at: AwareDatetime
    expected_return: Annotated[Decimal, Field(ge=-1, allow_inf_nan=False)]
    direction: Direction
    probability_up: None = None
    model_kind: Literal["BASELINE"] = "BASELINE"
    model_version: Literal["momentum20-v1"] = MODEL_VERSION
    model_sha256: SHA256
    data_snapshot_hash: SHA256
    feature_schema_version: Literal["daily-price-v1"] = FEATURE_SCHEMA
    protocol_version: Literal["eod-price-v1"] = PROTOCOL_VERSION
    return_basis: Literal["split-only-price-return"] = "split-only-price-return"
    direction_rule: Literal["sign-zero-v1"] = "sign-zero-v1"
    calendar_version: str
    git_sha: Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]


class ForecastRequest(Contract):
    snapshot: MarketSnapshot
    horizon_sessions: Literal[5] = 5


class FreezeRequest(Contract):
    draft_id: UUID
    confirmed: StrictBool


class DraftRequest(Contract):
    request_id: UUID
    symbol: Literal["NVDA"] = "NVDA"


class Outcome(Contract):
    status: Literal["SETTLED"] = "SETTLED"
    actual_return: Decimal
    actual_direction: Direction
    absolute_error: Decimal
    zero_return_absolute_error: Decimal
    direction_correct: bool
    base_close: Price
    target_close: Price
    split_factor: Price
    frozen_base_close: Price
    base_price_difference: Decimal
    base_revision: str
    base_revision_snapshot_hash: SHA256 | None = None
    base_revision_provider: str | None = None
    base_revision_source_id: str | None = None
    base_revision_as_of: AwareDatetime | None = None
    calculation_version: Literal["settlement-price-v1", "settlement-price-v2"] = CALCULATION_VERSION
    data_snapshot_hash: SHA256
    source_revision: str
