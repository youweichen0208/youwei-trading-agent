"""Known answers at the frozen numeric model's train/predict/JSON boundary."""

from datetime import UTC, datetime
import json

import pytest

from quant.logistic import LogisticSpec, export_model, load_model, predict_model, train_model


CUTOFF = datetime(2026, 1, 1, tzinfo=UTC)
MATURED = datetime(2025, 12, 31, tzinfo=UTC)


def train_symmetric_model():
    return train_model(
        [[-2.0], [-1.0], [1.0], [2.0]],
        [-0.2, -0.1, 0.1, 0.2],
        label_available_at=[MATURED] * 4,
        training_cutoff=CUTOFF,
        spec=LogisticSpec(horizon_td=20, feature_names=("momentum",)),
    )


def test_symmetric_labels_predict_half_at_center_and_order_the_two_tails():
    predictions = predict_model(train_symmetric_model(), [[-2.0], [0.0], [2.0]])

    assert predictions[1].p_outperform == pytest.approx(0.5, abs=1e-8)
    assert predictions[0].p_outperform < 0.5 < predictions[2].p_outperform
    # Ridge alpha=1 shrinks y=0.1*x to 0.08*x on these standardized rows.
    assert predictions[0].expected_excess_return == pytest.approx(-0.16)
    assert predictions[1].expected_excess_return == pytest.approx(0.0, abs=1e-12)
    assert predictions[2].expected_excess_return == pytest.approx(0.16)


@pytest.mark.parametrize(
    "available_at",
    [datetime(2026, 1, 2, tzinfo=UTC), datetime(2025, 12, 31)],
)
def test_training_rejects_unmatured_or_unlocated_label_time(available_at):
    with pytest.raises(ValueError, match="label_available_at"):
        train_model(
            [[-1.0], [1.0]], [-0.1, 0.1],
            label_available_at=[MATURED, available_at], training_cutoff=CUTOFF,
            spec=LogisticSpec(horizon_td=20, feature_names=("momentum",)),
        )


def test_json_roundtrip_and_extreme_holdout_leave_training_preprocessing_unchanged():
    model = train_symmetric_model()
    payload = export_model(model)
    assert payload["preprocessing"]["mean"] == [0.0]
    assert payload["preprocessing"]["scale"] == pytest.approx([2.5 ** 0.5])
    assert payload["logistic"]["classes"] == [0, 1]
    assert payload["calibration"] == "none"
    assert payload["libraries"]["scikit-learn"]
    frozen_json = json.dumps(payload, sort_keys=True, allow_nan=False)

    restored = load_model(json.loads(frozen_json))
    expected = predict_model(model, [[-0.5], [0.5]])
    assert predict_model(restored, [[-0.5], [0.5]]) == expected
    predict_model(restored, [[1e6], [-1e6]])
    assert json.dumps(export_model(restored), sort_keys=True, allow_nan=False) == frozen_json
    assert predict_model(restored, [[-0.5], [0.5]]) == expected


def test_feature_width_must_match_the_registered_feature_names():
    with pytest.raises(ValueError, match="feature"):
        train_model(
            [[-1.0, 0.0], [1.0, 0.0]], [-0.1, 0.1],
            label_available_at=[MATURED] * 2, training_cutoff=CUTOFF,
            spec=LogisticSpec(horizon_td=20, feature_names=("momentum",)),
        )


@pytest.mark.parametrize(
    "features,returns",
    [
        ([[-1.0], [float("nan")]], [-0.1, 0.1]),
        ([[-1.0], [float("inf")]], [-0.1, 0.1]),
        ([[-1.0], [1.0]], [-0.1, float("nan")]),
        ([[-1.0], [1.0]], [0.1, 0.2]),
        ([[-1.0], [1.0]], [-0.1, -0.2]),
    ],
)
def test_training_rejects_nonfinite_rows_or_single_class_labels(features, returns):
    with pytest.raises(ValueError):
        train_model(
            features, returns, label_available_at=[MATURED] * 2,
            training_cutoff=CUTOFF,
            spec=LogisticSpec(horizon_td=20, feature_names=("momentum",)),
        )


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("logistic", "classes", [1, 0]),
        ("logistic", "coef", [float("nan")]),
        ("preprocessing", "scale", [0.0]),
        ("preprocessing", "mean", [0.0, 1.0]),
        ("spec", "horizon_td", 5),
        ("spec", "c", 0.0),
        ("spec", "feature_names", ["momentum", "momentum"]),
        ("expected_return", "type", "probability_mapped_to_return"),
    ],
)
def test_loading_rejects_inconsistent_or_nonfinite_frozen_parameters(section, key, value):
    payload = export_model(train_symmetric_model())
    payload[section][key] = value
    with pytest.raises(ValueError):
        load_model(payload)


def test_nonconverged_fit_is_rejected_instead_of_exported_as_a_candidate():
    with pytest.raises(ValueError, match="converge"):
        train_model(
            [[-2.0], [-1.0], [1.0], [2.0]], [-0.2, -0.1, 0.1, 0.2],
            label_available_at=[MATURED] * 4, training_cutoff=CUTOFF,
            spec=LogisticSpec(horizon_td=20, feature_names=("momentum",), max_iter=1),
        )


def test_prediction_rejects_numeric_overflow_before_returning_a_produced_result():
    payload = export_model(train_symmetric_model())
    payload["preprocessing"]["scale"] = [1e-300]
    model = load_model(payload)
    with pytest.raises(ValueError, match="finite"):
        predict_model(model, [[1e100]])
