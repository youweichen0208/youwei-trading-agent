from decimal import Decimal

import pytest
from yta_alpha.calibration import (
    calibrate_and_evaluate,
    calibrated_probability,
    fit_isotonic,
    reliability,
)
from yta_alpha.errors import DomainError
from yta_alpha.labels import build_label
from yta_alpha.training import TrainConfig, train_fold


def test_pava_exact_weighted_ties_and_interpolation():
    d = Decimal
    curve = fit_isotonic(tuple(map(d, (0, 1, 2, 3, 4))), (0, 1, 0, 1, 1))
    assert curve == ((d(0), d(0)), (d(1), d(".5")), (d(2), d(".5")), (d(3), d(1)), (d(4), d(1)))
    assert calibrated_probability(curve, d(".5")) == d(".25")
    assert calibrated_probability(curve, d("-100")) == 0
    assert calibrated_probability(curve, d("100")) == 1
    weighted = fit_isotonic((d(0), d(1), d(1), d(1)), (1, 0, 0, 0))
    assert weighted == ((d(0), d(".25")), (d(1), d(".25")))
    assert fit_isotonic((d(1), d(1)), (0, 1)) == ((d(1), d(".5")),)


def test_brier_ece_edges_and_empty_bins():
    result = reliability((Decimal(0), Decimal(".5"), Decimal(1)), (0, 1, 1), bins=2)
    assert result["brier"] == "0.083333333333"
    assert result["ece"] == "0.166666666667"
    assert [b["count"] for b in result["bins"]] == [1, 2]
    empty = reliability((), ())
    assert empty["brier"] is None and empty["ece"] is None
    assert all(b["mean_probability"] is None and b["up_frequency"] is None for b in empty["bins"])
    assert reliability((Decimal(0), Decimal(1)), (0, 1))["brier"] == "0.000000000000"


@pytest.mark.parametrize(
    "scores,targets",
    [
        ((0.5, Decimal(1)), (0, 1)),
        ((Decimal("NaN"), Decimal(1)), (0, 1)),
        ((Decimal(0), Decimal(1)), (False, True)),
        ((Decimal(0),), (0, 1)),
        ((Decimal(0), Decimal(1)), (0, 2)),
    ],
)
def test_invalid_samples(scores, targets):
    with pytest.raises(DomainError):
        fit_isotonic(scores, targets)


@pytest.mark.parametrize("targets", [(), (0,), (1, 1), (0, 0)])
def test_single_class_calibration_rejected(targets):
    with pytest.raises(DomainError) as error:
        fit_isotonic(tuple(Decimal(i) for i in range(len(targets))), targets)
    assert error.value.code == "INSUFFICIENT_CALIBRATION_DATA"


@pytest.fixture(scope="module")
def experiment():
    from test_training import experiment as base_experiment
    from yta_alpha.calendar import TradingCalendar

    calendar = TradingCalendar()
    _, inputs, outcomes, split = base_experiment(calendar, 5)

    def revise(snapshot):
        bars = []
        for bar in snapshot.bars:
            price = Decimal(100 + (bar.session.toordinal() % 13) * 2)
            bars.append(
                bar.model_copy(update={name: price for name in ("open", "high", "low", "close")})
            )
        return snapshot.model_copy(update={"bars": tuple(bars)})

    inputs = tuple(map(revise, inputs))
    outcomes = tuple(map(revise, outcomes))
    labels = tuple(
        build_label(base, outcome, horizon_sessions=5, cutoff=outcome.as_of, calendar=calendar)
        for base, outcome in zip(inputs, outcomes, strict=True)
    )
    artifact = train_fold(
        labels,
        inputs,
        outcomes,
        split_config=split,
        config=TrainConfig(rounds=10, min_data_in_leaf=2, num_leaves=4),
        fold_index=0,
        cutoff=outcomes[-1].as_of,
        calendar=calendar,
    )
    return artifact, labels, inputs, outcomes, calendar


def run(experiment, evaluation_indices=range(55, 60), embargo=0):
    artifact, labels, inputs, outcomes, calendar = experiment
    indices = (*range(45, 50), *evaluation_indices)
    return calibrate_and_evaluate(
        artifact,
        labels[45:50],
        tuple(labels[i] for i in evaluation_indices),
        tuple(inputs[i] for i in indices),
        tuple(outcomes[i] for i in indices),
        cutoff=outcomes[-1].as_of,
        calendar=calendar,
        embargo_sessions=embargo,
    )


def test_actual_model_independent_calibration_and_later_evaluation(experiment):
    report = run(experiment)
    assert report["status"] == "EXPERIMENTAL" and report["qualification"] == "UNVERIFIED"
    assert len(report["calibration_ids"]) == len(report["evaluation_ids"]) == 5
    assert not set(report["calibration_ids"]).intersection(report["evaluation_ids"])
    assert report["reliability"]["sample_count"] == 5
    assert all(0 <= Decimal(p) <= 1 for p in report["probabilities"])
    assert report == run(experiment)


def test_evaluation_outcomes_cannot_fit_the_curve(experiment):
    artifact, labels, inputs, outcomes, calendar = experiment
    before = run(experiment)
    outcomes = list(outcomes)
    labels = list(labels)
    for i in range(55, 60):
        bar = (
            outcomes[i]
            .bars[0]
            .model_copy(update={name: Decimal(1000) for name in ("open", "high", "low", "close")})
        )
        outcomes[i] = outcomes[i].model_copy(update={"bars": (bar,)})
        labels[i] = build_label(
            inputs[i], outcomes[i], horizon_sessions=5, cutoff=outcomes[i].as_of, calendar=calendar
        )
    after = run((artifact, tuple(labels), inputs, tuple(outcomes), calendar))
    assert before["knots"] == after["knots"]
    assert before["probabilities"] == after["probabilities"]
    assert before["evaluation_ids"] != after["evaluation_ids"]
    assert before["reliability"] != after["reliability"]


@pytest.mark.parametrize(
    "indices,embargo", [(range(49, 54), 0), (range(54, 59), 0), (range(55, 60), 1)]
)
def test_overlap_and_embargo_rejected(experiment, indices, embargo):
    with pytest.raises(DomainError) as error:
        run(experiment, indices, embargo)
    assert error.value.code == "CALIBRATION_OVERLAP"


def test_training_fold_cannot_be_used_for_calibration(experiment):
    artifact, labels, inputs, outcomes, calendar = experiment
    with pytest.raises(DomainError) as error:
        calibrate_and_evaluate(
            artifact,
            labels[:5],
            labels[55:60],
            (),
            (),
            cutoff=outcomes[-1].as_of,
            calendar=calendar,
        )
    assert error.value.code == "CALIBRATION_COHORT_MISMATCH"


def test_delayed_calibration_label_and_missing_evaluation_session(experiment):
    from datetime import timedelta

    from yta_alpha.walk_forward import label_id

    artifact, labels, inputs, outcomes, calendar = experiment
    labels = list(labels)
    labels[49] = labels[49].model_copy(
        update={"label_available_at": labels[55].feature_as_of + timedelta(days=1)}
    )
    fold = artifact.fold.model_copy(update={"test": tuple(map(label_id, labels[45:50]))})
    changed = artifact.model_copy(update={"fold": fold})
    with pytest.raises(DomainError) as error:
        run((changed, tuple(labels), inputs, outcomes, calendar))
    assert error.value.code == "CALIBRATION_OVERLAP"
    with pytest.raises(DomainError) as error:
        run(experiment, (55, 57, 58, 59))
    assert error.value.code == "INCOMPLETE_CALIBRATION_WINDOW"


@pytest.mark.parametrize("mutation", ["missing", "tampered", "future"])
def test_source_evidence_and_cutoff_are_verified(experiment, mutation):
    artifact, labels, inputs, outcomes, calendar = experiment
    selected = (*range(45, 50), *range(55, 60))
    snapshots = tuple(inputs[i] for i in selected)
    observations = tuple(outcomes[i] for i in selected)
    evaluation = labels[55:60]
    cutoff = outcomes[-1].as_of
    if mutation == "missing":
        observations = observations[1:]
    elif mutation == "tampered":
        evaluation = (
            evaluation[0].model_copy(update={"actual_return": Decimal("0.123")}),
            *evaluation[1:],
        )
    else:
        cutoff = inputs[55].as_of
    with pytest.raises(DomainError) as error:
        calibrate_and_evaluate(
            artifact,
            labels[45:50],
            evaluation,
            snapshots,
            observations,
            cutoff=cutoff,
            calendar=calendar,
        )
    assert error.value.code == (
        "FUTURE_INFORMATION" if mutation == "future" else "CALIBRATION_EVIDENCE_MISMATCH"
    )
