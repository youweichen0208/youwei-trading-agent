"""Read-only experimental inference, deliberately separate from forecast facts."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, field_validator

from yta_alpha.calendar import TradingCalendar
from yta_alpha.errors import DomainError
from yta_alpha.forecast import snapshot_hash
from yta_alpha.model_registry import ModelRegistry
from yta_alpha.models import SHA256, Contract, DataMode, Direction, MarketSnapshot
from yta_alpha.training import predict


class ExperimentalRequest(Contract):
    snapshot: MarketSnapshot
    horizon_sessions: Literal[1, 3, 5, 10, 20]

    @field_validator("horizon_sessions", mode="before")
    @classmethod
    def strict_horizon(cls, value):
        if type(value) is not int:
            raise ValueError("Horizon must be an integer")
        return value


class ExperimentalPrediction(Contract):
    status: Literal["EXPERIMENTAL"] = "EXPERIMENTAL"
    model_kind: Literal["TRAINED"] = "TRAINED"
    model_id: str
    model_sha256: SHA256
    data_snapshot_hash: SHA256
    data_mode: DataMode
    horizon_sessions: Literal[1, 3, 5, 10, 20]
    as_of: AwareDatetime
    training_cutoff: AwareDatetime
    base_session: date
    target_session: date
    target_close_at: AwareDatetime
    calendar_version: str
    expected_return: Annotated[Decimal, Field(ge=-1, allow_inf_nan=False)]
    direction: Direction
    probability_up: None = None
    protocol_version: Literal["eod-price-v1"] = "eod-price-v1"
    return_basis: Literal["split-only-price-return"] = "split-only-price-return"


def experimental_predict(
    registry: ModelRegistry,
    identity: str,
    body: ExperimentalRequest,
    *,
    calendar: TradingCalendar,
    now: datetime,
    replay: bool,
):
    snapshot = body.snapshot
    if now.tzinfo is None or snapshot.as_of > now:
        raise DomainError("FUTURE_INFORMATION", "Input cutoff is ahead of the server clock")
    at = snapshot.as_of if replay else now
    base = snapshot.bars[-1].session
    if calendar.latest_closed(at) != base:
        raise DomainError("STALE_DATA", "Input does not represent the latest completed session")
    if at >= calendar.open_at(calendar.after(base, 1)):
        raise DomainError("FEATURE_WINDOW_CLOSED", "Research again after the next EOD bar")
    registration, registered_at = registry.get_with_timestamp(identity)
    if not replay and snapshot.as_of < registered_at:
        raise DomainError("MODEL_NOT_AVAILABLE", "Model was not registered at the input cutoff")
    artifact = registration.artifact
    if artifact.horizon_sessions != body.horizon_sessions:
        raise DomainError("MODEL_HORIZON_MISMATCH", "Request and registered model horizons differ")
    value = predict(artifact, (snapshot,), calendar=calendar)[0]
    target = calendar.after(base, artifact.horizon_sessions)
    return ExperimentalPrediction(
        model_id=identity,
        model_sha256=artifact.model_sha256,
        data_snapshot_hash=snapshot_hash(snapshot),
        data_mode=snapshot.data_mode,
        horizon_sessions=artifact.horizon_sessions,
        as_of=snapshot.as_of,
        training_cutoff=artifact.training_cutoff,
        base_session=base,
        target_session=target,
        target_close_at=calendar.close_at(target),
        calendar_version=calendar.version,
        expected_return=value["expected_return"],
        direction=value["direction"],
    )
