from datetime import timedelta

import pytest
from pydantic import ValidationError
from yta_alpha.errors import DomainError
from yta_alpha.labels import build_label
from yta_alpha.models import DataMode
from yta_alpha.walk_forward import SplitConfig, label_id, walk_forward
from yta_alpha.walk_forward import TestWindow as Window


def make_labels(snapshot, calendar, horizon=5):
    rows = []
    for index in range(40):
        base_day = calendar.after(snapshot.bars[-1].session, index)
        target_day = calendar.after(base_day, horizon)
        base_at = calendar.close_at(base_day) + timedelta(minutes=10)
        target_at = calendar.close_at(target_day) + timedelta(minutes=10)
        base_bar = snapshot.bars[-1].model_copy(
            update={
                "session": base_day,
                "available_at": base_at,
                "retrieved_at": base_at,
            }
        )
        base = snapshot.model_copy(
            update={
                "bars": (base_bar,),
                "as_of": base_at,
                "actions_verified_through": base_day,
            }
        )
        target_bar = base_bar.model_copy(
            update={
                "session": target_day,
                "available_at": target_at,
                "retrieved_at": target_at,
            }
        )
        observed = base.model_copy(
            update={
                "bars": (target_bar,),
                "as_of": target_at,
                "actions_verified_through": target_day,
            }
        )
        rows.append(
            build_label(
                base, observed, horizon_sessions=horizon, cutoff=target_at, calendar=calendar
            )
        )
    return tuple(rows)


@pytest.fixture
def labels(snapshot, calendar):
    return make_labels(snapshot, calendar)


def config_for(labels, embargo=0):
    return SplitConfig(
        test_windows=(
            Window(start=labels[10].base_session, end=labels[11].base_session),
            Window(start=labels[30].base_session, end=labels[31].base_session),
        ),
        embargo_sessions=embargo,
    )


def test_expanding_window_purges_boundary_and_never_trains_on_future(labels, calendar):
    manifest = walk_forward(labels, config=config_for(labels), calendar=calendar)
    first, second = manifest.folds
    assert first.train == tuple(map(label_id, labels[:5]))
    assert first.purged == tuple(map(label_id, labels[5:10]))
    assert first.test == tuple(map(label_id, labels[10:12]))
    assert second.train == tuple(map(label_id, labels[:25]))
    assert second.purged == tuple(map(label_id, labels[25:30]))
    lookup = {label_id(row): row for row in labels}
    for fold in manifest.folds:
        assert not set(fold.train).intersection(fold.test)
        for identity in fold.train:
            row = lookup[identity]
            assert row.target_session < fold.test_window.start
            assert row.label_available_at <= fold.training_cutoff


def test_embargo_counts_exchange_sessions_after_prior_test_targets(labels, calendar):
    manifest = walk_forward(labels, config=config_for(labels, embargo=2), calendar=calendar)
    first, second = manifest.folds
    assert not first.embargoed
    # First test's last base index 11 has target index 16; exclude bases 17 and 18.
    assert second.embargoed == tuple(map(label_id, labels[17:19]))
    assert label_id(labels[16]) in second.train
    assert label_id(labels[19]) in second.train
    assert label_id(labels[10]) in second.train  # old test may become eligible past data
    assert second.test == tuple(map(label_id, labels[30:32]))


def test_delayed_label_is_unavailable_then_eligible(labels, calendar):
    delayed = labels[0].model_copy(update={"label_available_at": labels[11].feature_as_of})
    rows = (delayed, *labels[1:])
    manifest = walk_forward(rows, config=config_for(labels), calendar=calendar)
    assert manifest.folds[0].unavailable == (label_id(delayed),)
    assert label_id(delayed) not in manifest.folds[0].train
    assert label_id(delayed) in manifest.folds[1].train
    boundary = delayed.model_copy(update={"label_available_at": labels[10].feature_as_of})
    manifest = walk_forward((boundary, *labels[1:]), config=config_for(labels), calendar=calendar)
    assert label_id(boundary) in manifest.folds[0].train


def test_manifest_is_deterministic_and_version_sensitive(labels, calendar):
    config = config_for(labels)
    first = walk_forward(labels, config=config, calendar=calendar)
    reordered = walk_forward(tuple(reversed(labels)), config=config, calendar=calendar)
    assert first.model_dump_json() == reordered.model_dump_json()
    revised = labels[0].model_copy(update={"target_revision": "synthetic-new-version"})
    other = walk_forward((revised, *labels[1:]), config=config, calendar=calendar)
    assert first.dataset_hash != other.dataset_hash
    assert first.folds[0].train != other.folds[0].train


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("empty", "EMPTY_DATASET"),
        ("duplicate", "AMBIGUOUS_LABEL_VERSION"),
        ("mode", "COHORT_MISMATCH"),
        ("horizon", "COHORT_MISMATCH"),
        ("calendar", "CALENDAR_MISMATCH"),
        ("target", "INVALID_LABEL_WINDOW"),
        ("close", "INVALID_LABEL_WINDOW"),
        ("early_label", "INVALID_LABEL_WINDOW"),
        ("early_feature", "INVALID_LABEL_WINDOW"),
        ("late_feature", "INVALID_LABEL_WINDOW"),
        ("missing_test", "INCOMPLETE_TEST_WINDOW"),
        ("short_train", "INSUFFICIENT_TRAINING_DATA"),
    ],
)
def test_invalid_or_incomplete_experiments_rejected(labels, calendar, mutation, code):
    config = config_for(labels)
    rows = labels
    change = {}
    if mutation == "empty":
        rows = ()
    elif mutation == "duplicate":
        rows = (*labels, labels[0])
    elif mutation == "mode":
        change = {"data_mode": DataMode.LIVE_RESEARCH}
    elif mutation == "horizon":
        change = {"horizon_sessions": 1}
    elif mutation == "calendar":
        rows = tuple(row.model_copy(update={"calendar_version": "other"}) for row in labels)
    elif mutation == "target":
        change = {"target_session": calendar.after(labels[0].target_session, 1)}
    elif mutation == "close":
        change = {"target_close_at": labels[0].target_close_at + timedelta(hours=1)}
    elif mutation == "early_label":
        change = {"label_available_at": labels[0].target_close_at - timedelta(seconds=1)}
    elif mutation == "early_feature":
        change = {"feature_as_of": calendar.close_at(labels[0].base_session) - timedelta(seconds=1)}
    elif mutation == "late_feature":
        change = {"feature_as_of": calendar.open_at(labels[1].base_session)}
    elif mutation == "missing_test":
        rows = (*labels[:10], *labels[11:])
    elif mutation == "short_train":
        config = config.model_copy(update={"min_train_samples": 6})
    if change:
        rows = (labels[0].model_copy(update=change), *labels[1:])
    with pytest.raises(DomainError) as error:
        walk_forward(rows, config=config, calendar=calendar)
    assert error.value.code == code


@pytest.mark.parametrize("kind", ["overlap", "reversed", "unordered"])
def test_invalid_test_windows(labels, calendar, kind):
    first, second = config_for(labels).test_windows
    windows = (
        (first, first)
        if kind == "overlap"
        else (
            (Window(start=first.end, end=first.start),) if kind == "reversed" else (second, first)
        )
    )
    with pytest.raises(DomainError) as error:
        walk_forward(labels, config=SplitConfig(test_windows=windows), calendar=calendar)
    assert error.value.code == "INVALID_TEST_WINDOWS"


@pytest.mark.parametrize("value", [-1, True, 1.0, 253])
def test_invalid_embargo_configuration(labels, value):
    with pytest.raises(ValidationError):
        config_for(labels, embargo=value)


@pytest.mark.parametrize("horizon", [1, 3, 5, 10, 20])
def test_every_horizon_has_strictly_past_training_labels(snapshot, calendar, horizon):
    rows = make_labels(snapshot, calendar, horizon)
    config = SplitConfig(
        test_windows=(Window(start=rows[30].base_session, end=rows[31].base_session),)
    )
    fold = walk_forward(rows, config=config, calendar=calendar).folds[0]
    assert fold.train == tuple(map(label_id, rows[: 30 - horizon]))
    assert fold.purged == tuple(map(label_id, rows[30 - horizon : 30]))
    assert len(fold.purged) == horizon
