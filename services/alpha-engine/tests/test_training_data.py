from datetime import timedelta
from decimal import Decimal

import pytest
from yta_alpha.errors import DomainError
from yta_alpha.forecast import snapshot_hash
from yta_alpha.indicators import indicators
from yta_alpha.labels import build_label
from yta_alpha.training_data import FEATURE_NAMES, prepare_dataset, prepared_dataset_hash
from yta_alpha.walk_forward import SplitConfig
from yta_alpha.walk_forward import TestWindow as Window


@pytest.fixture
def experiment(snapshot, calendar):
    snapshots, labels, observations = [], [], []
    for index in range(12):
        bars = []
        for bar in snapshot.bars:
            day = calendar.after(bar.session, index)
            at = calendar.close_at(day) + timedelta(minutes=10)
            bars.append(
                bar.model_copy(
                    update={
                        "session": day,
                        "available_at": at,
                        "retrieved_at": at,
                    }
                )
            )
        base = snapshot.model_copy(
            update={
                "bars": tuple(bars),
                "as_of": bars[-1].available_at,
                "actions_verified_through": bars[-1].session,
            }
        )
        target = calendar.after(bars[-1].session, 5)
        at = calendar.close_at(target) + timedelta(minutes=10)
        observation = base.model_copy(
            update={
                "bars": (
                    bars[-1].model_copy(
                        update={
                            "session": target,
                            "available_at": at,
                            "retrieved_at": at,
                        }
                    ),
                ),
                "as_of": at,
                "actions_verified_through": target,
            }
        )
        labels.append(
            build_label(base, observation, horizon_sessions=5, cutoff=at, calendar=calendar)
        )
        snapshots.append(base)
        observations.append(observation)
    config = SplitConfig(
        test_windows=(
            Window(
                start=labels[10].base_session,
                end=labels[11].base_session,
            ),
        )
    )
    return tuple(labels), tuple(snapshots), tuple(observations), config


def test_feature_columns_are_exact_point_in_time_indicators(experiment, calendar):
    labels, snapshots, _, config = experiment
    result = prepare_dataset(labels, snapshots, config=config, calendar=calendar)
    assert result.feature_names == FEATURE_NAMES
    assert len(result.features) == len(labels) == 12
    for label, row, snapshot in zip(result.labels, result.features, snapshots, strict=True):
        expected = indicators(snapshot, calendar)
        assert row.values == tuple(
            Decimal(expected["series"][name][-1])
            if expected["series"][name][-1] is not None
            else None
            for name in FEATURE_NAMES
        )
        assert row.snapshot_hash == label.base_snapshot_hash == snapshot_hash(snapshot)
        assert row.as_of == label.feature_as_of
        assert row.base_session == label.base_session
        assert row.values[FEATURE_NAMES.index("SMA50")] is None
        assert row.values[FEATURE_NAMES.index("MACD_SIGNAL")] is None
        assert isinstance(row.model_dump(mode="json")["values"][0], str)
    assert len(result.splits.folds[0].train) == 5
    assert len(result.splits.folds[0].purged) == 5
    assert len(result.splits.folds[0].test) == 2


def test_future_result_changes_labels_but_never_input_features(experiment, calendar):
    labels, snapshots, observations, config = experiment
    before = prepare_dataset(labels, snapshots, config=config, calendar=calendar)
    observed = observations[0]
    bar = observed.bars[0].model_copy(
        update={
            "open": Decimal(200),
            "high": Decimal(200),
            "low": Decimal(200),
            "close": Decimal(200),
            "revision": "synthetic-different-future",
        }
    )
    observed = observed.model_copy(update={"bars": (bar,)})
    revised = build_label(
        snapshots[0], observed, horizon_sessions=5, cutoff=observed.as_of, calendar=calendar
    )
    after = prepare_dataset((revised, *labels[1:]), snapshots, config=config, calendar=calendar)
    assert before.features == after.features
    assert before.labels[0].actual_return != after.labels[0].actual_return
    assert prepared_dataset_hash(before) != prepared_dataset_hash(after)


def test_input_reordering_does_not_change_artifact(experiment, calendar):
    labels, snapshots, _, config = experiment
    before = prepare_dataset(labels, snapshots, config=config, calendar=calendar)
    after = prepare_dataset(
        tuple(reversed(labels)), tuple(reversed(snapshots)), config=config, calendar=calendar
    )
    assert before.model_dump_json() == after.model_dump_json()
    assert prepared_dataset_hash(before) == prepared_dataset_hash(after)


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("missing", "MISSING_FEATURE_SNAPSHOT"),
        ("duplicate", "DUPLICATE_SNAPSHOT"),
        ("outcome", "UNEXPECTED_SNAPSHOT"),
        ("modified", "UNEXPECTED_SNAPSHOT"),
        ("price", "FEATURE_LABEL_MISMATCH"),
        ("revision", "FEATURE_LABEL_MISMATCH"),
        ("asof", "FEATURE_LABEL_MISMATCH"),
        ("gap", "MISSING_SESSION"),
        ("future", "FUTURE_INFORMATION"),
    ],
)
def test_input_provenance_and_time_checks(experiment, calendar, mutation, code):
    labels, snapshots, observations, config = experiment
    if mutation == "missing":
        snapshots = snapshots[1:]
    elif mutation == "duplicate":
        snapshots = (*snapshots, snapshots[0])
    elif mutation == "outcome":
        snapshots = (*snapshots, observations[0])
    elif mutation == "modified":
        snapshots = (snapshots[0].model_copy(update={"source_id": "other"}), *snapshots[1:])
    elif mutation in {"price", "revision", "asof"}:
        changes = {
            "price": {"base_close": Decimal(101)},
            "revision": {"base_revision": "other"},
            "asof": {"feature_as_of": labels[0].feature_as_of + timedelta(seconds=1)},
        }
        labels = (labels[0].model_copy(update=changes[mutation]), *labels[1:])
    else:
        base = snapshots[0]
        if mutation == "gap":
            base = base.model_copy(update={"bars": (base.bars[0], *base.bars[2:])})
        else:
            future = base.bars[0].model_copy(
                update={"retrieved_at": base.as_of + timedelta(seconds=1)}
            )
            base = base.model_copy(update={"bars": (future, *base.bars[1:])})
        snapshots = (base, *snapshots[1:])
        labels = (
            labels[0].model_copy(update={"base_snapshot_hash": snapshot_hash(base)}),
            *labels[1:],
        )
    with pytest.raises(DomainError) as error:
        prepare_dataset(labels, snapshots, config=config, calendar=calendar)
    assert error.value.code == code
