from datetime import date, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError
from yta_alpha.errors import DomainError
from yta_alpha.fixtures import replay_observation
from yta_alpha.forecast import forecast, settle, snapshot_hash
from yta_alpha.models import DataMode, MarketBar, Split

SHA = "a" * 40


def test_documented_baseline_and_outcome(snapshot, calendar):
    prediction = forecast(snapshot, now=snapshot.as_of, calendar=calendar, git_sha=SHA)
    assert prediction.expected_return == Decimal("0.005")
    assert prediction.direction == "UP"
    assert prediction.probability_up is None
    assert prediction.target_session == date(2024, 12, 2)  # skips Thanksgiving
    actual = replay_observation(calendar)
    outcome = settle(prediction, actual, now=actual.as_of, calendar=calendar)
    assert outcome.actual_return == Decimal("0.02")
    assert outcome.absolute_error == Decimal("0.015")
    assert outcome.zero_return_absolute_error == Decimal("0.02")
    assert outcome.direction_correct


def test_price_changes_cannot_rewrite_snapshot_identity(snapshot):
    bar = snapshot.bars[-1]
    changed = bar.model_copy(update={"close": bar.close + Decimal("0.01")})
    other = snapshot.model_copy(update={"bars": (*snapshot.bars[:-1], changed)})
    assert snapshot_hash(snapshot) == snapshot_hash(snapshot.model_copy())
    assert snapshot_hash(snapshot) != snapshot_hash(other)


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("short", "INSUFFICIENT_HISTORY"),
        ("gap", "MISSING_SESSION"),
        ("future", "FUTURE_INFORMATION"),
        ("unverified", "ACTIONS_UNVERIFIED"),
    ],
)
def test_invalid_evidence_is_rejected(snapshot, calendar, mutation, code):
    if mutation == "short":
        snapshot = snapshot.model_copy(update={"bars": snapshot.bars[1:]})
    elif mutation == "gap":
        # Extend the expected date range while keeping 21 rows.
        first = snapshot.bars[0].model_copy(
            update={
                "session": calendar.after(snapshot.bars[0].session, -1),
            }
        )
        snapshot = snapshot.model_copy(update={"bars": (first, *snapshot.bars[1:])})
    elif mutation == "future":
        last = snapshot.bars[-1].model_copy(
            update={"retrieved_at": snapshot.as_of + timedelta(seconds=1)}
        )
        snapshot = snapshot.model_copy(update={"bars": (*snapshot.bars[:-1], last)})
    else:
        snapshot = snapshot.model_copy(update={"actions_verified_through": date(2024, 11, 21)})
    with pytest.raises(DomainError) as error:
        forecast(snapshot, now=snapshot.as_of, calendar=calendar, git_sha=SHA)
    assert error.value.code == code


def test_freeze_window_is_exclusive_at_next_open(snapshot, calendar):
    opening = calendar.open_at(date(2024, 11, 25))
    forecast(snapshot, now=opening - timedelta(microseconds=1), calendar=calendar, git_sha=SHA)
    with pytest.raises(DomainError, match="Research again"):
        forecast(snapshot, now=opening, calendar=calendar, git_sha=SHA)


def test_newly_closed_session_makes_old_data_stale(snapshot, calendar):
    with pytest.raises(DomainError) as error:
        forecast(
            snapshot, now=calendar.close_at(date(2024, 11, 25)), calendar=calendar, git_sha=SHA
        )
    assert error.value.code == "STALE_DATA"


def test_no_early_settlement_or_cross_mode_outcomes(snapshot, calendar):
    prediction = forecast(snapshot, now=snapshot.as_of, calendar=calendar, git_sha=SHA)
    actual = replay_observation(calendar)
    with pytest.raises(DomainError) as error:
        settle(
            prediction,
            actual,
            now=prediction.target_close_at - timedelta(seconds=1),
            calendar=calendar,
        )
    assert error.value.code == "NOT_DUE"
    actual = actual.model_copy(update={"data_mode": DataMode.LIVE_RESEARCH})
    with pytest.raises(DomainError) as error:
        settle(prediction, actual, now=actual.as_of, calendar=calendar)
    assert error.value.code == "COHORT_MISMATCH"


def test_split_settlement_uses_original_share_basis(snapshot, calendar):
    prediction = forecast(snapshot, now=snapshot.as_of, calendar=calendar, git_sha=SHA)
    actual = replay_observation(calendar, "55")
    actual = actual.model_copy(
        update={
            "splits": (
                Split(
                    effective_session=date(2024, 11, 25),
                    new_shares_per_old=Decimal(2),
                    available_at=snapshot.as_of,
                    source_id="synthetic-split-2-for-1",
                ),
            )
        }
    )
    outcome = settle(prediction, actual, now=actual.as_of, calendar=calendar)
    assert outcome.actual_return == Decimal("0.1")
    assert outcome.split_factor == 2
    assert outcome.base_close == 100


def test_suspension_does_not_shift_target(snapshot, calendar):
    prediction = forecast(snapshot, now=snapshot.as_of, calendar=calendar, git_sha=SHA)
    actual = replay_observation(calendar)
    previous = actual.bars[0].model_copy(update={"session": date(2024, 11, 29)})
    actual = actual.model_copy(update={"bars": (previous,)})
    with pytest.raises(DomainError) as error:
        settle(prediction, actual, now=actual.as_of, calendar=calendar)
    assert error.value.code == "WAITING_DATA"


@pytest.mark.parametrize("field,value", [("close", 100.1), ("close", "NaN"), ("volume", True)])
def test_transport_rejects_imprecise_or_nonfinite_values(snapshot, field, value):
    data = snapshot.bars[0].model_dump(mode="json")
    data[field] = value
    with pytest.raises(ValidationError):
        MarketBar.model_validate(data)


def test_base_revision_recalculates_without_mutating_frozen_forecast(snapshot, calendar):
    from yta_alpha.fixtures import replay_base_revision

    prediction = forecast(snapshot, now=snapshot.as_of, calendar=calendar, git_sha=SHA)
    before = prediction.model_dump()
    observed = replay_observation(calendar)
    revision = replay_base_revision(calendar)
    outcome = settle(
        prediction, observed, now=revision.as_of, calendar=calendar, base_revision=revision
    )
    assert outcome.actual_return == Decimal("0.009900990099")
    assert outcome.absolute_error == Decimal("0.004900990099")
    assert outcome.base_close == Decimal(101)
    assert outcome.frozen_base_close == Decimal(100)
    assert outcome.base_price_difference == Decimal(1)
    assert outcome.base_revision_snapshot_hash == snapshot_hash(revision)
    assert outcome.calculation_version == "settlement-price-v2"
    assert prediction.model_dump() == before


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("mode", "COHORT_MISMATCH"),
        ("date", "INVALID_BASE_REVISION"),
        ("future", "FUTURE_INFORMATION"),
        ("old-version", "INVALID_BASE_REVISION"),
        ("old-information", "INVALID_BASE_REVISION"),
    ],
)
def test_base_revision_boundary(snapshot, calendar, mutation, code):
    from yta_alpha.fixtures import replay_base_revision

    prediction = forecast(snapshot, now=snapshot.as_of, calendar=calendar, git_sha=SHA)
    actual = replay_observation(calendar)
    revision = replay_base_revision(calendar)
    now = revision.as_of
    if mutation == "mode":
        revision = revision.model_copy(update={"data_mode": DataMode.LIVE_RESEARCH})
    elif mutation == "date":
        revision = revision.model_copy(
            update={
                "bars": (
                    revision.bars[0].model_copy(
                        update={"session": calendar.after(prediction.base_session, -1)}
                    ),
                )
            }
        )
    elif mutation == "future":
        revision = revision.model_copy(update={"as_of": now + timedelta(days=1)})
    elif mutation == "old-version":
        revision = revision.model_copy(
            update={
                "bars": (
                    revision.bars[0].model_copy(update={"revision": prediction.base_revision}),
                )
            }
        )
    else:
        revision = revision.model_copy(
            update={
                "bars": (revision.bars[0].model_copy(update={"available_at": prediction.as_of}),)
            }
        )
    with pytest.raises(DomainError) as error:
        settle(prediction, actual, now=now, calendar=calendar, base_revision=revision)
    assert error.value.code == code


def test_base_revision_keeps_split_normalization(snapshot, calendar):
    from yta_alpha.fixtures import replay_base_revision

    prediction = forecast(snapshot, now=snapshot.as_of, calendar=calendar, git_sha=SHA)
    revision = replay_base_revision(calendar)
    observed = replay_observation(calendar, "55")
    observed = observed.model_copy(
        update={
            "splits": (
                Split(
                    effective_session=date(2024, 11, 25),
                    new_shares_per_old=Decimal(2),
                    available_at=snapshot.as_of,
                    source_id="synthetic-split-2-for-1",
                ),
            )
        }
    )
    result = settle(
        prediction, observed, now=revision.as_of, calendar=calendar, base_revision=revision
    )
    assert result.actual_return == Decimal("0.089108910891")
    assert result.split_factor == 2
    assert result.base_close == 101 and result.frozen_base_close == 100
