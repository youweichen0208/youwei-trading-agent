from datetime import date, timedelta
from decimal import Decimal

import pytest
from yta_alpha.errors import DomainError
from yta_alpha.labels import build_label
from yta_alpha.models import DataMode, MarketBar, MarketSnapshot
from yta_alpha.training import (
    TrainConfig,
    load_booster,
    numeric,
    predict,
    prediction_values,
    read_artifact,
    save_artifact,
    train_fold,
)
from yta_alpha.walk_forward import SplitConfig
from yta_alpha.walk_forward import TestWindow as Window


def experiment(calendar, horizon):
    bars = []
    for index in range(150):
        day = calendar.after(date(2023, 1, 3), index)
        at = calendar.close_at(day) + timedelta(minutes=10)
        price = Decimal(100) + Decimal(index) / 10 + Decimal(index % 7) / 20
        bars.append(
            MarketBar(
                session=day,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=1000 + index,
                available_at=at,
                retrieved_at=at,
                revision="synthetic-v1",
            )
        )
    inputs, outcomes, labels = [], [], []
    for index in range(50, 110):
        base = MarketSnapshot(
            symbol="NVDA",
            provider="synthetic-fixture",
            source_id=f"base-{index}",
            data_mode=DataMode.REPLAY,
            adjustment="RAW",
            as_of=bars[index].available_at,
            bars=tuple(bars[index - 50 : index + 1]),
            actions_verified_through=bars[index].session,
        )
        outcome = base.model_copy(
            update={
                "source_id": f"outcome-{index}",
                "bars": (bars[index + horizon],),
                "as_of": bars[index + horizon].available_at,
                "actions_verified_through": bars[index + horizon].session,
            }
        )
        inputs.append(base)
        outcomes.append(outcome)
        labels.append(
            build_label(
                base, outcome, horizon_sessions=horizon, cutoff=outcome.as_of, calendar=calendar
            )
        )
    split = SplitConfig(
        test_windows=(Window(start=labels[45].base_session, end=labels[49].base_session),)
    )
    return tuple(labels), tuple(inputs), tuple(outcomes), split


def fit(bundle, calendar):
    labels, inputs, outcomes, split = bundle
    return train_fold(
        labels,
        inputs,
        outcomes,
        split_config=split,
        config=TrainConfig(rounds=10, min_data_in_leaf=2, num_leaves=4),
        fold_index=0,
        cutoff=outcomes[-1].as_of,
        calendar=calendar,
    )


@pytest.mark.parametrize("horizon", [1, 3, 5, 10, 20])
def test_real_cpu_training_reload_and_inference(calendar, tmp_path, horizon):
    bundle = experiment(calendar, horizon)
    model = fit(bundle, calendar)
    assert model.status == "EXPERIMENTAL"
    assert model.horizon_sessions == horizon
    assert len(model.fold.train) == 45 - horizon
    assert len(model.fold.test) == len(model.test_predictions) == 5
    assert model.probability_up is None
    assert load_booster(model).num_trees() > 1  # actually learned splits, not just a constant mock
    path = tmp_path / f"h{horizon}.json"
    digest = save_artifact(model, path)
    assert path.stat().st_mode & 0o777 == 0o600
    loaded = read_artifact(path, digest)
    result = predict(loaded, bundle[1][45:50], calendar=calendar)
    assert tuple(Decimal(row["expected_return"]) for row in result) == model.test_predictions
    assert all(row["probability_up"] is None for row in result)
    assert model == loaded
    with pytest.raises(FileExistsError):
        save_artifact(model, path)
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(DomainError) as error:
        read_artifact(path, digest)
    assert error.value.code == "MODEL_INTEGRITY_ERROR"


def test_determinism_and_test_targets_never_fit_the_model(calendar):
    bundle = experiment(calendar, 5)
    model = fit(bundle, calendar)
    assert fit(bundle, calendar).model_sha256 == model.model_sha256
    labels, inputs, outcomes, split = bundle
    outcome = outcomes[45]
    bar = outcome.bars[0].model_copy(
        update={name: Decimal(200) for name in ("open", "high", "low", "close")}
    )
    outcome = outcome.model_copy(update={"bars": (bar,)})
    label = build_label(
        inputs[45], outcome, horizon_sessions=5, cutoff=outcome.as_of, calendar=calendar
    )
    changed = fit(
        (
            (*labels[:45], label, *labels[46:]),
            inputs,
            (*outcomes[:45], outcome, *outcomes[46:]),
            split,
        ),
        calendar,
    )
    assert changed.model_sha256 == model.model_sha256
    assert changed.test_predictions == model.test_predictions
    assert changed.test_mae != model.test_mae
    assert changed.dataset_hash != model.dataset_hash


def test_tampered_label_is_recomputed_before_training(calendar):
    labels, inputs, outcomes, split = experiment(calendar, 5)
    labels = (labels[0].model_copy(update={"actual_return": Decimal("0.9")}), *labels[1:])
    with pytest.raises(DomainError) as error:
        fit((labels, inputs, outcomes, split), calendar)
    assert error.value.code == "LABEL_EVIDENCE_MISMATCH"


def test_model_and_inference_boundaries(calendar):
    bundle = experiment(calendar, 5)
    model = fit(bundle, calendar)
    with pytest.raises(DomainError) as error:
        load_booster(model.model_copy(update={"model_text": model.model_text + "changed"}))
    assert error.value.code == "MODEL_INTEGRITY_ERROR"
    with pytest.raises(DomainError) as error:
        predict(model, (bundle[1][0],), calendar=calendar)
    assert error.value.code == "FUTURE_INFORMATION"
    other = bundle[1][45].model_copy(update={"data_mode": DataMode.LIVE_RESEARCH})
    with pytest.raises(DomainError) as error:
        predict(model, (other,), calendar=calendar)
    assert error.value.code == "COHORT_MISMATCH"


@pytest.mark.parametrize("value", [Decimal("1e1000"), Decimal("1e-1000"), Decimal("NaN")])
def test_numeric_range_fails_instead_of_silent_corruption(value):
    with pytest.raises(DomainError):
        numeric(value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.01])
def test_predictions_outside_domain_are_not_clipped(value):
    with pytest.raises(DomainError) as error:
        prediction_values([value])
    assert error.value.code == "INVALID_ESTIMATE"
