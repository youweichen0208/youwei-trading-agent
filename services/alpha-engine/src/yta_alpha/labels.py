"""Versioned offline labels; these results cannot authorize or create a forecast."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime

from yta_alpha.calendar import TradingCalendar
from yta_alpha.errors import DomainError
from yta_alpha.forecast import direction, price_return, snapshot_hash, validate_snapshot
from yta_alpha.models import SHA256, Contract, DataMode, Direction, MarketSnapshot, Price

HORIZONS = (1, 3, 5, 10, 20)


class PriceLabel(Contract):
    schema_version: Literal["price-label-v1"] = "price-label-v1"
    protocol_version: Literal["eod-price-v1"] = "eod-price-v1"
    return_basis: Literal["split-only-price-return"] = "split-only-price-return"
    symbol: Literal["NVDA"] = "NVDA"
    data_mode: DataMode
    horizon_sessions: Literal[1, 3, 5, 10, 20]
    base_session: date
    target_session: date
    target_close_at: AwareDatetime
    feature_as_of: AwareDatetime
    # Conservatively use the whole observation cutoff, never backdate revised labels.
    label_available_at: AwareDatetime
    base_snapshot_hash: SHA256
    observation_snapshot_hash: SHA256
    base_close: Price
    target_close: Price
    base_revision: str
    target_revision: str
    split_factor: Price
    actual_return: Decimal
    actual_direction: Direction
    calendar_version: str


def build_label(
    base: MarketSnapshot,
    observation: MarketSnapshot,
    *,
    horizon_sessions: int,
    cutoff: datetime,
    calendar: TradingCalendar,
) -> PriceLabel:
    """Build one fixed-horizon label from separately frozen input and outcome evidence.

    Missing target data raises WAITING_DATA, never replaces a target session or price.
    Callers may collect results/errors per horizon; no partial success is hidden here.
    This does not verify a provider's authorization or a training split's eligibility.
    """
    if type(horizon_sessions) is not int or horizon_sessions not in HORIZONS:
        raise DomainError("UNSUPPORTED_HORIZON", "Expected 1, 3, 5, 10 or 20 sessions")
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise DomainError("INVALID_TIME", "Label cutoff must include a timezone")
    # Revalidate instances too: model_copy is intentionally not a validation boundary.
    base = MarketSnapshot.model_validate(base.model_dump(mode="json"))
    observation = MarketSnapshot.model_validate(observation.model_dump(mode="json"))
    if base.symbol != observation.symbol or base.data_mode != observation.data_mode:
        raise DomainError("COHORT_MISMATCH", "Input and label evidence must share a cohort")
    validate_snapshot(base, calendar)
    validate_snapshot(observation, calendar)
    if base.as_of > cutoff or observation.as_of > cutoff:
        raise DomainError("FUTURE_INFORMATION", "Label evidence exceeds experiment cutoff")
    first = base.bars[-1]
    if base.as_of >= calendar.open_at(calendar.after(first.session, 1)):
        raise DomainError("FEATURE_WINDOW_CLOSED", "Input snapshot is outside its EOD window")
    target_session = calendar.after(first.session, horizon_sessions)
    target_close_at = calendar.close_at(target_session)
    if cutoff < target_close_at:
        raise DomainError("NOT_DUE", "Label target session has not closed", status=409)
    target = next((bar for bar in observation.bars if bar.session == target_session), None)
    if target is None:
        raise DomainError("WAITING_DATA", "Exact target close is unavailable", retryable=True)
    actual, factor = price_return(
        first.close, target.close, first.session, target_session, observation.splits
    )
    return PriceLabel(
        data_mode=base.data_mode,
        horizon_sessions=horizon_sessions,
        base_session=first.session,
        target_session=target_session,
        target_close_at=target_close_at,
        feature_as_of=base.as_of,
        label_available_at=observation.as_of,
        base_snapshot_hash=snapshot_hash(base),
        observation_snapshot_hash=snapshot_hash(observation),
        base_close=first.close,
        target_close=target.close,
        base_revision=first.revision,
        target_revision=target.revision,
        split_factor=factor,
        actual_return=actual,
        actual_direction=direction(actual),
        calendar_version=calendar.version,
    )
