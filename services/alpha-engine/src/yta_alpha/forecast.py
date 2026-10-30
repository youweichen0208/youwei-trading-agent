import hashlib
import json
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from yta_alpha.calendar import TradingCalendar
from yta_alpha.errors import DomainError
from yta_alpha.models import Direction, Forecast, MarketSnapshot, Outcome, Split

QUANTUM = Decimal("0.000000000001")
MODEL_CONFIG = {
    "version": "momentum20-v1",
    "window_returns": 20,
    "horizon_sessions": 5,
    "precision": 50,
    "return_decimal_places": 12,
    "rounding": "ROUND_HALF_EVEN",
    "formula": "5 * mean(20 split-comparable daily simple returns)",
}


def content_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def snapshot_hash(snapshot: MarketSnapshot) -> str:
    return content_hash(snapshot.model_dump(mode="json"))


def rounded(value: Decimal) -> Decimal:
    return value.quantize(QUANTUM, rounding=ROUND_HALF_EVEN)


def direction(value: Decimal) -> Direction:
    return Direction.UP if value > 0 else Direction.DOWN if value < 0 else Direction.FLAT


def price_return(
    base_close: Decimal,
    target_close: Decimal,
    base_session: date,
    target_session: date,
    splits: tuple[Split, ...],
) -> tuple[Decimal, Decimal]:
    """Protocol arithmetic on validated prices/actions; shared by outcomes and labels."""
    with localcontext() as context:
        context.prec = 50
        factor = Decimal(1)
        for split in splits:
            if base_session < split.effective_session <= target_session:
                factor *= split.new_shares_per_old
        return rounded(target_close * factor / base_close - 1), factor


def validate_snapshot(snapshot: MarketSnapshot, calendar: TradingCalendar) -> None:
    for bar in snapshot.bars:
        if bar.available_at < calendar.close_at(bar.session):
            raise DomainError("BAR_NOT_FINAL", "EOD bar was available before session close")
        if bar.available_at > snapshot.as_of or bar.retrieved_at > snapshot.as_of:
            raise DomainError("FUTURE_INFORMATION", "Snapshot includes information after as_of")
    if any(a.available_at > snapshot.as_of for a in snapshot.splits):
        raise DomainError("FUTURE_INFORMATION", "Split evidence is newer than snapshot")
    if snapshot.actions_verified_through < snapshot.bars[-1].session:
        raise DomainError("ACTIONS_UNVERIFIED", "Corporate action coverage is incomplete")


def adjusted_closes(snapshot: MarketSnapshot) -> tuple[Decimal, ...]:
    """Express historical raw closes in the last bar's share basis, using known splits."""
    last_session = snapshot.bars[-1].session
    with localcontext() as context:
        context.prec = 50
        values = []
        for bar in snapshot.bars:
            factor = Decimal(1)
            for split in snapshot.splits:
                if bar.session < split.effective_session <= last_session:
                    factor *= split.new_shares_per_old
            values.append(bar.close / factor)
        return tuple(values)


def forecast(
    snapshot: MarketSnapshot, *, now: datetime, calendar: TradingCalendar, git_sha: str
) -> Forecast:
    validate_snapshot(snapshot, calendar)
    if now.tzinfo is None or now < snapshot.as_of:
        raise DomainError("INVALID_TIME", "Server clock precedes information cutoff")
    base = snapshot.bars[-1]
    if calendar.latest_closed(now) != base.session:
        raise DomainError("STALE_DATA", "Latest completed session has no verified bar")
    expires_at = calendar.open_at(calendar.after(base.session, 1))
    if now >= expires_at:
        raise DomainError("FREEZE_WINDOW_CLOSED", "Research again after the next EOD bar")
    if len(snapshot.bars) < 21:
        raise DomainError("INSUFFICIENT_HISTORY", "Baseline requires 21 consecutive closes")
    sample = snapshot.bars[-21:]
    if tuple(b.session for b in sample) != calendar.sessions(sample[0].session, base.session):
        raise DomainError("MISSING_SESSION", "Baseline window contains a missing trading session")
    with localcontext() as context:
        context.prec = 50
        prices = adjusted_closes(snapshot)[-21:]
        changes = [right / left - 1 for left, right in zip(prices, prices[1:], strict=False)]
        expected = rounded(5 * sum(changes) / 20)
    if expected < -1:
        raise DomainError("INVALID_ESTIMATE", "Baseline estimate is below the price-return domain")
    target = calendar.after(base.session, 5)
    return Forecast(
        data_mode=snapshot.data_mode,
        as_of=snapshot.as_of,
        expires_at=expires_at,
        base_session=base.session,
        base_close=base.close,
        base_revision=base.revision,
        target_session=target,
        target_close_at=calendar.close_at(target),
        expected_return=expected,
        direction=direction(expected),
        model_sha256=content_hash(MODEL_CONFIG),
        data_snapshot_hash=snapshot_hash(snapshot),
        calendar_version=calendar.version,
        git_sha=git_sha,
    )


def settle(
    prediction: Forecast,
    observation: MarketSnapshot,
    *,
    now: datetime,
    calendar: TradingCalendar,
    base_revision: MarketSnapshot | None = None,
) -> Outcome:
    if now.tzinfo is None:
        raise DomainError("INVALID_TIME", "Clock must include a timezone")
    if now < prediction.target_close_at:
        raise DomainError("NOT_DUE", "Target session has not closed", status=409)
    if observation.symbol != prediction.symbol or observation.data_mode != prediction.data_mode:
        raise DomainError("COHORT_MISMATCH", "Prediction and observed data must share a cohort")
    validate_snapshot(observation, calendar)
    if observation.as_of > now:
        raise DomainError("FUTURE_INFORMATION", "Observation is newer than the server clock")
    target = next((b for b in observation.bars if b.session == prediction.target_session), None)
    if target is None:
        raise DomainError("WAITING_DATA", "Verified target close is not available", retryable=True)
    base_close = prediction.base_close
    base_revision_id = prediction.base_revision
    if base_revision is not None:
        base_revision = MarketSnapshot.model_validate(base_revision.model_dump(mode="json"))
        if (
            base_revision.symbol != prediction.symbol
            or base_revision.data_mode != prediction.data_mode
        ):
            raise DomainError("COHORT_MISMATCH", "Revised base must share forecast cohort")
        validate_snapshot(base_revision, calendar)
        if base_revision.as_of > now:
            raise DomainError("FUTURE_INFORMATION", "Base revision is newer than server clock")
        if len(base_revision.bars) != 1 or base_revision.bars[0].session != prediction.base_session:
            raise DomainError(
                "INVALID_BASE_REVISION", "Revision must identify the exact base session"
            )
        revised_bar = base_revision.bars[0]
        if (
            revised_bar.revision == prediction.base_revision
            or revised_bar.available_at <= prediction.as_of
        ):
            raise DomainError(
                "INVALID_BASE_REVISION", "Revision needs a new post-freeze source version"
            )
        base_close, base_revision_id = revised_bar.close, revised_bar.revision
    with localcontext() as context:
        context.prec = 50
        actual, factor = price_return(
            base_close,
            target.close,
            prediction.base_session,
            prediction.target_session,
            observation.splits,
        )
        return Outcome(
            actual_return=actual,
            actual_direction=direction(actual),
            absolute_error=rounded(abs(prediction.expected_return - actual)),
            zero_return_absolute_error=rounded(abs(actual)),
            direction_correct=prediction.direction == direction(actual),
            base_close=base_close,
            frozen_base_close=prediction.base_close,
            base_price_difference=base_close - prediction.base_close,
            base_revision=base_revision_id,
            base_revision_snapshot_hash=snapshot_hash(base_revision) if base_revision else None,
            base_revision_provider=base_revision.provider if base_revision else None,
            base_revision_source_id=base_revision.source_id if base_revision else None,
            base_revision_as_of=base_revision.as_of if base_revision else None,
            calculation_version="settlement-price-v2" if base_revision else "settlement-price-v1",
            target_close=target.close,
            split_factor=factor,
            data_snapshot_hash=snapshot_hash(observation),
            source_revision=target.revision,
        )
