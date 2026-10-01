"""Small frozen numeric model: L2 logistic probability and separate ridge mean.

Input assembly, PIT feature selection and TargetSpec labels belong to the caller.
This module only fits already frozen numeric training rows, checking that their
labels were available at the declared training cutoff. Logistic probabilities
are fitted estimates; no separate calibration or quality claim is implied.
"""

from dataclasses import asdict, dataclass
from datetime import datetime
from importlib.metadata import version
from math import isfinite
import warnings

import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.preprocessing import StandardScaler

from quant.models import ModelPrediction


MODEL_VERSION = "quant-logistic-ridge-v1"


@dataclass(frozen=True)
class LogisticSpec:
    horizon_td: int
    feature_names: tuple[str, ...]
    c: float = 1.0
    ridge_alpha: float = 1.0
    seed: int = 20260927
    max_iter: int = 2000
    tol: float = 1e-8

    def __post_init__(self):
        if type(self.horizon_td) is not int or self.horizon_td not in (1, 20, 60):
            raise ValueError("horizon_td must be one of 1, 20, 60")
        if (
            not isinstance(self.feature_names, tuple) or not self.feature_names
            or any(not isinstance(name, str) or not name for name in self.feature_names)
            or len(set(self.feature_names)) != len(self.feature_names)
        ):
            raise ValueError("feature_names must be a nonempty tuple of distinct names")
        if any(not isfinite(value) or value <= 0 for value in (self.c, self.ridge_alpha, self.tol)):
            raise ValueError("C, ridge_alpha and tol must be finite and positive")
        if type(self.max_iter) is not int or self.max_iter < 1:
            raise ValueError("max_iter must be a positive integer")
        if type(self.seed) is not int or not 0 <= self.seed < 2**32:
            raise ValueError("seed must be an unsigned 32-bit integer")


@dataclass(frozen=True)
class FrozenLogisticModel:
    spec: LogisticSpec
    mean: tuple[float, ...]
    scale: tuple[float, ...]
    logistic_coef: tuple[float, ...]
    logistic_intercept: float
    ridge_coef: tuple[float, ...]
    ridge_intercept: float
    training_cutoff: str
    latest_label_available_at: str
    training_rows: int
    libraries: tuple[tuple[str, str], ...]

    def __post_init__(self):
        width = len(self.spec.feature_names)
        for values in (self.mean, self.scale, self.logistic_coef, self.ridge_coef):
            if len(values) != width or not all(isfinite(value) for value in values):
                raise ValueError("frozen parameters must be finite and match the feature width")
        if any(value <= 0 for value in self.scale):
            raise ValueError("frozen scale must be positive")
        if not all(isfinite(value) for value in (self.logistic_intercept, self.ridge_intercept)):
            raise ValueError("frozen intercepts must be finite")
        cutoff = datetime.fromisoformat(self.training_cutoff)
        available_at = datetime.fromisoformat(self.latest_label_available_at)
        if cutoff.utcoffset() is None or available_at.utcoffset() is None or available_at > cutoff:
            raise ValueError("training timestamps must be aware with labels mature at cutoff")
        if type(self.training_rows) is not int or self.training_rows < 2:
            raise ValueError("training requires at least two rows")


def train_model(
    features: list[list[float]],
    excess_returns: list[float],
    *,
    label_available_at: list[datetime],
    training_cutoff: datetime,
    spec: LogisticSpec,
) -> FrozenLogisticModel:
    """Fit one horizon using training rows only, never validation features."""
    if training_cutoff.utcoffset() is None:
        raise ValueError("training_cutoff must be timezone-aware")
    if len(label_available_at) != len(features):
        raise ValueError("label_available_at must identify each training row")
    if any(
        time.utcoffset() is None or time > training_cutoff
        for time in label_available_at
    ):
        raise ValueError("label_available_at must be aware and no later than training_cutoff")
    matrix = _matrix(features, len(spec.feature_names))
    returns = np.asarray(excess_returns, dtype=np.float64)
    scaler = StandardScaler().fit(matrix)
    transformed = scaler.transform(matrix)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", ConvergenceWarning)
            classifier = LogisticRegression(
                penalty="l2", C=spec.c, solver="lbfgs", fit_intercept=True,
                random_state=spec.seed, max_iter=spec.max_iter, tol=spec.tol,
            ).fit(transformed, (returns > 0).astype(int))
    except ConvergenceWarning as exc:
        raise ValueError("logistic fit did not converge") from exc
    regression = Ridge(alpha=spec.ridge_alpha, solver="svd", fit_intercept=True).fit(
        transformed, returns
    )
    return FrozenLogisticModel(
        spec=spec,
        mean=tuple(scaler.mean_), scale=tuple(scaler.scale_),
        logistic_coef=tuple(classifier.coef_[0]),
        logistic_intercept=float(classifier.intercept_[0]),
        ridge_coef=tuple(regression.coef_), ridge_intercept=float(regression.intercept_),
        training_cutoff=training_cutoff.isoformat(),
        latest_label_available_at=max(label_available_at).isoformat(),
        training_rows=len(features),
        libraries=tuple((name, version(name)) for name in ("numpy", "scipy", "scikit-learn")),
    )


def predict_model(
    model: FrozenLogisticModel, features: list[list[float]]
) -> list[ModelPrediction]:
    """Apply a frozen model without updating its training preprocessing."""
    matrix = _matrix(features, len(model.spec.feature_names))
    try:
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            transformed = (matrix - np.asarray(model.mean)) / np.asarray(model.scale)
            scores = transformed @ np.asarray(model.logistic_coef) + model.logistic_intercept
            returns = transformed @ np.asarray(model.ridge_coef) + model.ridge_intercept
    except FloatingPointError as exc:
        raise ValueError("prediction requires finite transformed features and outputs") from exc
    probabilities = np.exp(-np.logaddexp(0, -scores))
    return [
        ModelPrediction(
            source="quant_model", source_status="produced", model_version=MODEL_VERSION,
            p_outperform=float(probability), expected_excess_return=float(mean_return),
        )
        for probability, mean_return in zip(probabilities, returns, strict=True)
    ]


def export_model(model: FrozenLogisticModel) -> dict:
    """Export parameters as portable JSON values; no executable serialization."""
    spec = asdict(model.spec)
    spec["feature_names"] = list(model.spec.feature_names)
    return {
        "schema_version": 1,
        "model_version": MODEL_VERSION,
        "spec": spec,
        "preprocessing": {
            "type": "standard_scaler", "ddof": 0,
            "mean": list(model.mean), "scale": list(model.scale),
        },
        "logistic": {
            "solver": "lbfgs", "penalty": "l2", "fit_intercept": True,
            "class_weight": None, "classes": [0, 1],
            "coef": list(model.logistic_coef), "intercept": model.logistic_intercept,
        },
        "expected_return": {
            "type": "ridge", "solver": "svd", "fit_intercept": True,
            "coef": list(model.ridge_coef), "intercept": model.ridge_intercept,
        },
        "calibration": "none",
        "training": {
            "cutoff": model.training_cutoff,
            "latest_label_available_at": model.latest_label_available_at,
            "rows": model.training_rows,
        },
        "libraries": dict(model.libraries),
    }


def load_model(payload: dict) -> FrozenLogisticModel:
    """Restore JSON parameters without importing arbitrary classes or pickle."""
    try:
        return _load_model(payload)
    except (KeyError, TypeError, OverflowError) as exc:
        raise ValueError("invalid frozen model payload") from exc


def _load_model(payload: dict) -> FrozenLogisticModel:
    spec_payload = dict(payload["spec"])
    spec_payload["feature_names"] = tuple(spec_payload["feature_names"])
    model = FrozenLogisticModel(
        spec=LogisticSpec(**spec_payload),
        mean=tuple(payload["preprocessing"]["mean"]),
        scale=tuple(payload["preprocessing"]["scale"]),
        logistic_coef=tuple(payload["logistic"]["coef"]),
        logistic_intercept=payload["logistic"]["intercept"],
        ridge_coef=tuple(payload["expected_return"]["coef"]),
        ridge_intercept=payload["expected_return"]["intercept"],
        training_cutoff=payload["training"]["cutoff"],
        latest_label_available_at=payload["training"]["latest_label_available_at"],
        training_rows=payload["training"]["rows"],
        libraries=tuple(sorted(payload["libraries"].items())),
    )
    if export_model(model) != payload:
        raise ValueError("frozen model metadata or schema differs from the supported model")
    return model


def _matrix(features: list[list[float]], width: int) -> np.ndarray:
    matrix = np.asarray(features, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[1] != width or not len(matrix):
        raise ValueError("features must be nonempty rows matching the registered feature names")
    if not np.isfinite(matrix).all():
        raise ValueError("features must contain finite values")
    return matrix
