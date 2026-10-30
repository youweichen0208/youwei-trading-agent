"""CPU-only experimental regression; no model registration or promotion authority."""

import hashlib
import math
import os
import tempfile
from datetime import datetime
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, StrictInt

from yta_alpha.calendar import TradingCalendar
from yta_alpha.errors import DomainError
from yta_alpha.forecast import direction, rounded, snapshot_hash
from yta_alpha.indicators import indicators
from yta_alpha.labels import build_label
from yta_alpha.models import SHA256, Contract, DataMode
from yta_alpha.training_data import FEATURE_NAMES, prepare_dataset, prepared_dataset_hash
from yta_alpha.walk_forward import Fold, SplitConfig, label_id


class TrainConfig(Contract):
    rounds: Annotated[StrictInt, Field(ge=1, le=1000)] = 50
    seed: Annotated[StrictInt, Field(ge=0, le=2147483647)] = 17
    num_leaves: Annotated[StrictInt, Field(ge=2, le=64)] = 15
    min_data_in_leaf: Annotated[StrictInt, Field(ge=2, le=10000)] = 10


class ModelArtifact(Contract):
    schema_version: Literal["experimental-lightgbm-v1"] = "experimental-lightgbm-v1"
    status: Literal["EXPERIMENTAL"] = "EXPERIMENTAL"
    library_version: Literal["4.6.0"] = "4.6.0"
    symbol: Literal["NVDA"] = "NVDA"
    data_mode: DataMode
    horizon_sessions: Literal[1, 3, 5, 10, 20]
    training_cutoff: AwareDatetime
    calendar_version: str
    config: TrainConfig
    dataset_hash: SHA256
    split_config: SplitConfig
    fold: Fold
    source_hashes: dict[str, SHA256]
    model_sha256: SHA256
    model_text: str
    test_predictions: tuple[Decimal, ...]
    test_mae: Decimal
    zero_return_mae: Decimal
    probability_up: None = None


def library():
    try:
        import lightgbm as lgb
    except (ImportError, OSError) as error:
        raise DomainError(
            "TRAINING_UNAVAILABLE", "Install pinned training and OpenMP runtime", status=503
        ) from error
    if lgb.__version__ != "4.6.0":
        raise DomainError("TRAINING_VERSION_MISMATCH", "Expected LightGBM 4.6.0", status=503)
    return lgb


def numeric(value):
    if value is None:
        return float("nan")
    converted = float(value)
    if not math.isfinite(converted) or (value != 0 and converted == 0):
        raise DomainError("TRAINING_NUMERIC_RANGE", "Value cannot enter the floating-point learner")
    return converted


def prediction_values(raw):
    with localcontext() as context:
        context.prec = 50
        values = []
        for number in raw:
            if not math.isfinite(float(number)) or number < -1:
                raise DomainError(
                    "INVALID_ESTIMATE", "Model return is outside the price-return domain"
                )
            values.append(rounded(Decimal(str(float(number)))))
        return tuple(values)


def parameters(config):
    return {
        "objective": "regression",
        "device_type": "cpu",
        "num_threads": 1,
        "deterministic": True,
        "force_col_wise": True,
        "seed": config.seed,
        "learning_rate": 0.05,
        "num_leaves": config.num_leaves,
        "min_data_in_leaf": config.min_data_in_leaf,
        "use_missing": True,
        "zero_as_missing": False,
        "verbosity": -1,
    }


def train_fold(
    labels,
    snapshots,
    observations,
    *,
    split_config: SplitConfig,
    config: TrainConfig,
    fold_index: int,
    cutoff: datetime,
    calendar: TradingCalendar,
) -> ModelArtifact:
    config = TrainConfig.model_validate(config.model_dump(mode="json"))
    prepared = prepare_dataset(labels, snapshots, config=split_config, calendar=calendar)
    if type(fold_index) is not int or not 0 <= fold_index < len(prepared.splits.folds):
        raise DomainError("INVALID_FOLD", "Select an existing fold")
    bases = {snapshot_hash(row): row for row in snapshots}
    observed = {snapshot_hash(row): row for row in observations}
    required = {row.observation_snapshot_hash for row in labels}
    if set(observed) != required or len(observed) != len(observations):
        raise DomainError("LABEL_EVIDENCE_MISMATCH", "Supply each exact outcome snapshot once")
    # Never trust a deserialized label/feature artifact as evidence. Rebuild both.
    for label in labels:
        rebuilt = build_label(
            bases[label.base_snapshot_hash],
            observed[label.observation_snapshot_hash],
            horizon_sessions=label.horizon_sessions,
            cutoff=cutoff,
            calendar=calendar,
        )
        if rebuilt != label:
            raise DomainError("LABEL_EVIDENCE_MISMATCH", "Label differs from its source evidence")
    import numpy as np

    lgb = library()
    fold = prepared.splits.folds[fold_index]
    rows = {
        label_id(label): (label, feature)
        for label, feature in zip(prepared.labels, prepared.features, strict=True)
    }
    x = np.array([[numeric(v) for v in rows[key][1].values] for key in fold.train], dtype="float64")
    # LightGBM internally stores regression labels as float32. Validate that boundary explicitly.
    with np.errstate(over="ignore", under="ignore"):
        y = np.array([numeric(rows[key][0].actual_return) for key in fold.train], dtype="float32")
    if not np.isfinite(y).all() or any(
        value == 0 and rows[key][0].actual_return != 0
        for key, value in zip(fold.train, y, strict=True)
    ):
        raise DomainError("TRAINING_NUMERIC_RANGE", "Label is outside the learner's numeric range")
    data = lgb.Dataset(x, label=y, feature_name=list(FEATURE_NAMES))
    booster = lgb.train(parameters(config), data, num_boost_round=config.rounds)
    test_x = np.array(
        [[numeric(v) for v in rows[key][1].values] for key in fold.test], dtype="float64"
    )
    predictions = prediction_values(booster.predict(test_x, num_threads=1))
    text = booster.model_to_string()
    with localcontext() as context:
        context.prec = 50
        targets = [rows[key][0].actual_return for key in fold.test]
        mae = rounded(
            sum(abs(p - y) for p, y in zip(predictions, targets, strict=True)) / len(targets)
        )
        zero = rounded(sum(abs(y) for y in targets) / len(targets))
    modules = (
        "training.py",
        "training_data.py",
        "walk_forward.py",
        "labels.py",
        "forecast.py",
        "indicators.py",
        "models.py",
        "calendar.py",
    )
    source_hashes = {
        name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
        for name in modules
    }
    return ModelArtifact(
        data_mode=labels[0].data_mode,
        horizon_sessions=labels[0].horizon_sessions,
        training_cutoff=fold.training_cutoff,
        calendar_version=calendar.version,
        config=config,
        dataset_hash=prepared_dataset_hash(prepared),
        split_config=prepared.splits.config,
        fold=fold,
        source_hashes=source_hashes,
        model_sha256=hashlib.sha256(text.encode()).hexdigest(),
        model_text=text,
        test_predictions=predictions,
        test_mae=mae,
        zero_return_mae=zero,
    )


def load_booster(artifact):
    artifact = ModelArtifact.model_validate(artifact.model_dump(mode="json"))
    if hashlib.sha256(artifact.model_text.encode()).hexdigest() != artifact.model_sha256:
        raise DomainError("MODEL_INTEGRITY_ERROR", "Model text hash differs")
    booster = library().Booster(model_str=artifact.model_text)
    if booster.feature_name() != list(FEATURE_NAMES):
        raise DomainError("FEATURE_SCHEMA_MISMATCH", "Model feature order differs")
    return booster


def predict(artifact: ModelArtifact, snapshots, *, calendar: TradingCalendar):
    import numpy as np

    from yta_alpha.models import MarketSnapshot

    booster = load_booster(artifact)
    matrix = []
    for raw in snapshots:
        snapshot = MarketSnapshot.model_validate(raw.model_dump(mode="json"))
        if (
            snapshot.data_mode != artifact.data_mode
            or calendar.version != artifact.calendar_version
        ):
            raise DomainError("COHORT_MISMATCH", "Inference must use the trained cohort/calendar")
        if snapshot.as_of < artifact.training_cutoff:
            raise DomainError("FUTURE_INFORMATION", "Model was not trained at this input cutoff")
        if snapshot.as_of >= calendar.open_at(calendar.after(snapshot.bars[-1].session, 1)):
            raise DomainError("FEATURE_WINDOW_CLOSED", "Inference input is outside its EOD window")
        result = indicators(snapshot, calendar)
        matrix.append(
            [
                numeric(Decimal(result["series"][name][-1]))
                if result["series"][name][-1] is not None
                else numeric(None)
                for name in FEATURE_NAMES
            ]
        )
    if not matrix:
        raise DomainError("EMPTY_DATASET", "Inference inputs are required")
    values = prediction_values(booster.predict(np.array(matrix, dtype="float64"), num_threads=1))
    return tuple(
        {
            "expected_return": str(value),
            "direction": direction(value).value,
            "probability_up": None,
            "status": "EXPERIMENTAL",
        }
        for value in values
    )


def save_artifact(artifact: ModelArtifact, path: Path) -> str:
    load_booster(artifact)
    raw = artifact.model_dump_json(indent=2).encode()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".model-")
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(raw)
            file.flush()
            os.fsync(file.fileno())
        os.link(temporary, path)  # atomic publish; never overwrite an existing artifact
    finally:
        os.unlink(temporary)
    return hashlib.sha256(raw).hexdigest()


def read_artifact(path: Path, expected_sha256: str) -> ModelArtifact:
    with path.open("rb") as file:
        raw = file.read(32 * 1024 * 1024 + 1)
    if len(raw) > 32 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise DomainError("MODEL_INTEGRITY_ERROR", "Artifact differs from its trusted hash")
    artifact = ModelArtifact.model_validate_json(raw)
    load_booster(artifact)
    return artifact
