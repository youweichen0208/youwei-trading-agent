"""Deterministic expanding-window splits over versioned, point-in-time labels."""

from datetime import date
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, StrictInt

from yta_alpha.calendar import TradingCalendar
from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash
from yta_alpha.labels import PriceLabel
from yta_alpha.models import SHA256, Contract


class TestWindow(Contract):
    start: date
    end: date


class SplitConfig(Contract):
    schema_version: Literal["walk-forward-v1"] = "walk-forward-v1"
    test_windows: Annotated[tuple[TestWindow, ...], Field(min_length=1)]
    embargo_sessions: Annotated[StrictInt, Field(ge=0, le=252)] = 0
    min_train_samples: Annotated[StrictInt, Field(ge=1)] = 1


class Fold(Contract):
    test_window: TestWindow
    training_cutoff: AwareDatetime
    train: tuple[SHA256, ...]
    test: tuple[SHA256, ...]
    purged: tuple[SHA256, ...]
    unavailable: tuple[SHA256, ...]
    embargoed: tuple[SHA256, ...]


class SplitManifest(Contract):
    schema_version: Literal["walk-forward-manifest-v1"] = "walk-forward-manifest-v1"
    config: SplitConfig
    dataset_hash: SHA256
    calendar_version: str
    folds: tuple[Fold, ...]


def label_id(label: PriceLabel) -> str:
    return content_hash(label.model_dump(mode="json"))


def walk_forward(
    labels: tuple[PriceLabel, ...], *, config: SplitConfig, calendar: TradingCalendar
) -> SplitManifest:
    """Purge target >= test start, then enforce availability and prior-fold embargo.

    Embargo excludes training samples whose base session falls in the N sessions
    immediately after any earlier test fold's final target session. It never drops
    test samples. Strictly past training makes a current fold's post-test embargo
    relevant only to later folds. Prior test samples may train later folds once eligible.
    """
    config = SplitConfig.model_validate(config.model_dump(mode="json"))
    if not labels:
        raise DomainError("EMPTY_DATASET", "Labels are required")
    rows = sorted(
        (PriceLabel.model_validate(row.model_dump(mode="json")) for row in labels),
        key=lambda row: row.base_session,
    )
    first = rows[0]
    cohort = (first.symbol, first.data_mode, first.horizon_sessions, first.calendar_version)
    if any((r.symbol, r.data_mode, r.horizon_sessions, r.calendar_version) != cohort for r in rows):
        raise DomainError("COHORT_MISMATCH", "Split each symbol, mode and horizon separately")
    if first.calendar_version != calendar.version:
        raise DomainError("CALENDAR_MISMATCH", "Labels require the same calendar version")
    if len({row.base_session for row in rows}) != len(rows):
        raise DomainError("AMBIGUOUS_LABEL_VERSION", "Select one label version per base session")
    for row in rows:
        if (
            row.target_session != calendar.after(row.base_session, row.horizon_sessions)
            or row.target_close_at != calendar.close_at(row.target_session)
            or not calendar.close_at(row.base_session)
            <= row.feature_as_of
            < calendar.open_at(calendar.after(row.base_session, 1))
            or row.label_available_at < row.target_close_at
        ):
            raise DomainError("INVALID_LABEL_WINDOW", "Label timing does not match the calendar")
    previous_end = None
    for window in config.test_windows:
        if window.start > window.end or (previous_end is not None and window.start <= previous_end):
            raise DomainError("INVALID_TEST_WINDOWS", "Test windows must be ordered and disjoint")
        calendar.sessions(window.start, window.end)
        previous_end = window.end

    ids = {row.base_session: label_id(row) for row in rows}
    folds = []
    embargo_intervals = []
    for window in config.test_windows:
        test = [row for row in rows if window.start <= row.base_session <= window.end]
        if tuple(row.base_session for row in test) != calendar.sessions(window.start, window.end):
            raise DomainError(
                "INCOMPLETE_TEST_WINDOW", "Every requested test session needs a label"
            )
        cutoff = test[0].feature_as_of
        selected, purged, unavailable, embargoed = [], [], [], []
        for row in rows:
            if row.base_session >= window.start:
                continue  # never admit current/future test features to training
            identity = ids[row.base_session]
            if row.target_session >= window.start:
                purged.append(identity)
            elif row.label_available_at > cutoff:
                unavailable.append(identity)
            elif any(start < row.base_session <= end for start, end in embargo_intervals):
                embargoed.append(identity)
            else:
                selected.append(identity)
        if len(selected) < config.min_train_samples:
            raise DomainError("INSUFFICIENT_TRAINING_DATA", "Too few eligible training samples")
        folds.append(
            Fold(
                test_window=window,
                training_cutoff=cutoff,
                train=tuple(selected),
                test=tuple(ids[row.base_session] for row in test),
                purged=tuple(purged),
                unavailable=tuple(unavailable),
                embargoed=tuple(embargoed),
            )
        )
        if config.embargo_sessions:
            end = max(row.target_session for row in test)
            embargo_intervals.append((end, calendar.after(end, config.embargo_sessions)))
    return SplitManifest(
        config=config,
        dataset_hash=content_hash([ids[row.base_session] for row in rows]),
        calendar_version=calendar.version,
        folds=tuple(folds),
    )
