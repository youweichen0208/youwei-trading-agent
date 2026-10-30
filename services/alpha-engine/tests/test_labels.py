from datetime import date, timedelta
from decimal import Decimal, localcontext

import pytest
from yta_alpha.errors import DomainError
from yta_alpha.fixtures import replay_observation
from yta_alpha.forecast import forecast, settle, snapshot_hash
from yta_alpha.labels import build_label
from yta_alpha.models import DataMode, MarketBar, Split


def observed(calendar, snapshot, horizon, price="102"):
    session = calendar.after(snapshot.bars[-1].session, horizon)
    at = calendar.close_at(session) + timedelta(minutes=10)
    bar = MarketBar(
        session=session,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=1,
        available_at=at,
        retrieved_at=at,
        revision="synthetic-target-v1",
    )
    return snapshot.model_copy(
        update={
            "bars": (bar,),
            "as_of": at,
            "splits": (),
            "actions_verified_through": session,
            "source_id": "synthetic-label",
        }
    )


@pytest.mark.parametrize(
    "horizon,target",
    [
        (1, date(2024, 11, 25)),
        (3, date(2024, 11, 27)),
        (5, date(2024, 12, 2)),
        (10, date(2024, 12, 9)),
        (20, date(2024, 12, 23)),
    ],
)
def test_five_horizons_skip_holidays_and_preserve_identity(snapshot, calendar, horizon, target):
    observation = observed(calendar, snapshot, horizon)
    label = build_label(
        snapshot, observation, horizon_sessions=horizon, cutoff=observation.as_of, calendar=calendar
    )
    assert label.target_session == target
    assert label.horizon_sessions == horizon
    assert label.actual_return == Decimal("0.020000000000")
    assert label.actual_direction == "UP"
    assert label.base_snapshot_hash == snapshot_hash(snapshot)
    assert label.observation_snapshot_hash == snapshot_hash(observation)
    assert label.feature_as_of < label.target_close_at <= label.label_available_at
    assert label.model_dump(mode="json")["actual_return"] == "0.020000000000"


@pytest.mark.parametrize(
    "price,direction",
    [
        ("100.00000000005", "FLAT"),
        ("100.00000000015", "UP"),
        ("99.99999999985", "DOWN"),
        ("102", "UP"),
    ],
)
def test_five_day_label_exactly_matches_settlement(snapshot, calendar, price, direction):
    observation = replay_observation(calendar, price)
    prediction = forecast(snapshot, now=snapshot.as_of, calendar=calendar, git_sha="a" * 40)
    outcome = settle(prediction, observation, now=observation.as_of, calendar=calendar)
    with localcontext() as context:
        context.prec = 6  # callers' ambient precision must not affect protocol arithmetic
        label = build_label(
            snapshot, observation, horizon_sessions=5, cutoff=observation.as_of, calendar=calendar
        )
    assert label.actual_return == outcome.actual_return
    assert label.actual_direction == outcome.actual_direction == direction
    assert label.split_factor == outcome.split_factor


def test_split_boundaries_and_reverse_split_compound(snapshot, calendar):
    observation = observed(calendar, snapshot, 5, "55")
    dates = [
        snapshot.bars[-1].session,
        calendar.after(snapshot.bars[-1].session, 1),
        observation.bars[-1].session,
        calendar.after(observation.bars[-1].session, 1),
    ]
    splits = tuple(
        Split(
            effective_session=day,
            new_shares_per_old=ratio,
            available_at=snapshot.as_of,
            source_id=f"synthetic-{i}",
        )
        for i, (day, ratio) in enumerate(zip(dates, ["100", "4", "0.5", "100"], strict=True))
    )
    observation = observation.model_copy(update={"splits": splits})
    label = build_label(
        snapshot, observation, horizon_sessions=5, cutoff=observation.as_of, calendar=calendar
    )
    assert label.split_factor == Decimal(2)
    assert label.actual_return == Decimal("0.1")


@pytest.mark.parametrize("horizon", [0, 2, 21, True, 5.0, "5"])
def test_unsupported_horizon_rejected(snapshot, calendar, horizon):
    observation = replay_observation(calendar)
    with pytest.raises(DomainError) as error:
        build_label(
            snapshot,
            observation,
            horizon_sessions=horizon,
            cutoff=observation.as_of,
            calendar=calendar,
        )
    assert error.value.code == "UNSUPPORTED_HORIZON"


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("mode", "COHORT_MISMATCH"),
        ("future", "FUTURE_INFORMATION"),
        ("actions", "ACTIONS_UNVERIFIED"),
        ("features", "FEATURE_WINDOW_CLOSED"),
        ("naive", "INVALID_TIME"),
        ("missing", "WAITING_DATA"),
        ("early", "NOT_DUE"),
    ],
)
def test_temporal_and_evidence_boundaries(snapshot, calendar, mutation, code):
    observation = replay_observation(calendar)
    cutoff = observation.as_of
    if mutation == "mode":
        observation = observation.model_copy(update={"data_mode": DataMode.LIVE_RESEARCH})
    elif mutation == "future":
        cutoff -= timedelta(seconds=1)
    elif mutation == "actions":
        observation = observation.model_copy(
            update={"actions_verified_through": snapshot.bars[-1].session}
        )
    elif mutation == "features":
        snapshot = snapshot.model_copy(update={"as_of": calendar.open_at(date(2024, 11, 25))})
    elif mutation == "naive":
        cutoff = cutoff.replace(tzinfo=None)
    elif mutation == "missing":
        observation = observed(calendar, snapshot, 4)  # never carry prior close forward
    elif mutation == "early":
        observation = snapshot
        cutoff = snapshot.as_of
    with pytest.raises(DomainError) as error:
        build_label(snapshot, observation, horizon_sessions=5, cutoff=cutoff, calendar=calendar)
    assert error.value.code == code


def test_revision_retains_original_base_and_changes_label_identity(snapshot, calendar):
    observation = replay_observation(calendar)
    original = build_label(
        snapshot, observation, horizon_sessions=5, cutoff=observation.as_of, calendar=calendar
    )
    later = observation.as_of + timedelta(days=30)
    bar = observation.bars[0].model_copy(
        update={"revision": "synthetic-v2", "available_at": later, "retrieved_at": later}
    )
    revised = observation.model_copy(update={"bars": (bar,), "as_of": later})
    result = build_label(snapshot, revised, horizon_sessions=5, cutoff=later, calendar=calendar)
    assert result.base_snapshot_hash == original.base_snapshot_hash
    assert result.observation_snapshot_hash != original.observation_snapshot_hash
    assert result.label_available_at == later
    assert original.label_available_at == observation.as_of
    with pytest.raises(DomainError, match="exceeds experiment cutoff"):
        build_label(
            snapshot, revised, horizon_sessions=5, cutoff=observation.as_of, calendar=calendar
        )


@pytest.mark.parametrize(
    "base_day,target_day,close_hour",
    [
        (date(2024, 11, 27), date(2024, 11, 29), 18),
        (date(2024, 3, 8), date(2024, 3, 11), 20),
    ],
)
def test_half_day_and_dst_use_exchange_close(snapshot, calendar, base_day, target_day, close_hour):
    at = calendar.close_at(base_day) + timedelta(minutes=10)
    bar = snapshot.bars[-1].model_copy(
        update={
            "session": base_day,
            "available_at": at,
            "retrieved_at": at,
        }
    )
    snapshot = snapshot.model_copy(
        update={
            "bars": (bar,),
            "as_of": at,
            "actions_verified_through": base_day,
        }
    )
    observation = observed(calendar, snapshot, 1)
    label = build_label(
        snapshot, observation, horizon_sessions=1, cutoff=observation.as_of, calendar=calendar
    )
    assert label.target_session == target_day
    assert label.target_close_at.hour == close_hour


def test_later_resumption_close_does_not_replace_missing_target(snapshot, calendar):
    observation = observed(calendar, snapshot, 6)
    with pytest.raises(DomainError) as error:
        build_label(
            snapshot, observation, horizon_sessions=5, cutoff=observation.as_of, calendar=calendar
        )
    assert error.value.code == "WAITING_DATA"
