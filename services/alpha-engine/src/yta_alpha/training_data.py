"""Bind point-in-time indicator features to labels and audited temporal splits."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field

from yta_alpha.calendar import TradingCalendar
from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash, snapshot_hash
from yta_alpha.indicators import indicators
from yta_alpha.labels import PriceLabel
from yta_alpha.models import SHA256, Contract, DataMode, MarketSnapshot
from yta_alpha.walk_forward import SplitConfig, SplitManifest, label_id, walk_forward

FEATURE_NAMES = (
    "RETURN_1D",
    "VOLUME_CHANGE_1D",
    "HIST_VOL20",
    "SMA20",
    "SMA50",
    "EMA12",
    "EMA26",
    "RSI14",
    "MACD",
    "MACD_SIGNAL",
    "MACD_HISTOGRAM",
    "BOLL_MIDDLE",
    "BOLL_UPPER",
    "BOLL_LOWER",
    "ATR14",
)
FeatureValue = Annotated[Decimal, Field(allow_inf_nan=False)] | None


class FeatureRow(Contract):
    schema_version: Literal["training-indicators-v1"] = "training-indicators-v1"
    indicator_version: Literal["indicators-v3"] = "indicators-v3"
    symbol: Literal["NVDA"] = "NVDA"
    data_mode: DataMode
    base_session: date
    as_of: AwareDatetime
    snapshot_hash: SHA256
    calendar_version: str
    values: Annotated[tuple[FeatureValue, ...], Field(min_length=15, max_length=15)]


class PreparedDataset(Contract):
    schema_version: Literal["prepared-training-v1"] = "prepared-training-v1"
    feature_names: tuple[str, ...]
    labels: tuple[PriceLabel, ...]
    features: tuple[FeatureRow, ...]
    splits: SplitManifest


def prepare_dataset(
    labels: tuple[PriceLabel, ...],
    snapshots: tuple[MarketSnapshot, ...],
    *,
    config: SplitConfig,
    calendar: TradingCalendar,
) -> PreparedDataset:
    """Recompute features from each label's exact input snapshot, never its outcome.

    Features and labels are aligned in base-session order. Missing indicators remain
    null; no fit, normalization, imputation, probability or model promotion occurs.
    All supplied snapshots must be required inputs, so an accidental outcome archive
    is rejected instead of being silently incorporated into feature preparation.
    """
    # Validate temporal/cohort/version constraints before any feature work.
    splits = walk_forward(labels, config=config, calendar=calendar)
    ordered = tuple(
        sorted(
            (PriceLabel.model_validate(row.model_dump(mode="json")) for row in labels),
            key=lambda row: row.base_session,
        )
    )
    required = {row.base_snapshot_hash for row in ordered}
    inputs = {}
    for raw in snapshots:
        snapshot = MarketSnapshot.model_validate(raw.model_dump(mode="json"))
        identity = snapshot_hash(snapshot)
        if identity not in required:
            raise DomainError("UNEXPECTED_SNAPSHOT", "Only exact label input snapshots are allowed")
        if identity in inputs:
            raise DomainError("DUPLICATE_SNAPSHOT", "Supply each input snapshot once")
        inputs[identity] = snapshot
    if set(inputs) != required:
        raise DomainError("MISSING_FEATURE_SNAPSHOT", "An exact input snapshot is missing")
    rows = []
    for label in ordered:
        snapshot = inputs[label.base_snapshot_hash]
        if (
            snapshot.symbol != label.symbol
            or snapshot.data_mode != label.data_mode
            or snapshot.as_of != label.feature_as_of
            or snapshot.bars[-1].session != label.base_session
            or snapshot.bars[-1].close != label.base_close
            or snapshot.bars[-1].revision != label.base_revision
        ):
            raise DomainError("FEATURE_LABEL_MISMATCH", "Label does not match its input evidence")
        result = indicators(snapshot, calendar)
        if result["schema_version"] != "indicators-v3":
            raise DomainError("FEATURE_SCHEMA_MISMATCH", "Indicator implementation changed")
        values = tuple(result["series"][name][-1] for name in FEATURE_NAMES)
        rows.append(
            FeatureRow(
                data_mode=label.data_mode,
                base_session=label.base_session,
                as_of=label.feature_as_of,
                snapshot_hash=label.base_snapshot_hash,
                calendar_version=calendar.version,
                values=values,
            )
        )
    if content_hash([label_id(row) for row in ordered]) != splits.dataset_hash:
        raise DomainError("DATASET_MISMATCH", "Temporal split and feature dataset differ")
    return PreparedDataset(
        feature_names=FEATURE_NAMES,
        labels=ordered,
        features=tuple(rows),
        splits=splits,
    )


def prepared_dataset_hash(dataset: PreparedDataset) -> str:
    """Identity includes exact label versions, features, their order and split policy."""
    return content_hash(dataset.model_dump(mode="json"))
