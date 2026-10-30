from datetime import timedelta

import pytest
from yta_alpha.errors import DomainError
from yta_alpha.fixtures import replay_snapshot
from yta_alpha.forecast import forecast
from yta_alpha.jobs import ReplayProvider, UnavailableProvider, retry_delay
from yta_alpha.models import DataMode


def test_bounded_retry_schedule():
    assert retry_delay(1, 0) == timedelta(seconds=45)
    assert retry_delay(1, 1) == timedelta(seconds=60)
    assert retry_delay(2, 1) == timedelta(seconds=120)
    assert retry_delay(100000, 1) == timedelta(hours=6)
    with pytest.raises(ValueError):
        retry_delay(0, 0.5)
    with pytest.raises(ValueError):
        retry_delay(1, 2)


def test_real_provider_never_uses_fixture_and_replay_checks_cohort(calendar):
    snapshot = replay_snapshot(calendar)
    prediction = forecast(snapshot, now=snapshot.as_of, calendar=calendar, git_sha="a" * 40)
    with pytest.raises(DomainError) as error:
        UnavailableProvider().observation(prediction, snapshot.as_of)
    assert error.value.code == "DATA_UNAVAILABLE"
    with pytest.raises(DomainError) as error:
        ReplayProvider(calendar).observation(
            prediction.model_copy(update={"data_mode": DataMode.LIVE_RESEARCH}), snapshot.as_of
        )
    assert error.value.code == "COHORT_MISMATCH"
    assert ReplayProvider(calendar).observation(prediction, snapshot.as_of) is None


def test_paused_dispatch_does_not_touch_database_or_provider(calendar):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from yta_alpha.jobs import SettlementWorker

    def forbidden(*args, **kwargs):
        pytest.fail("Paused dispatch must not access execution dependencies")

    worker = SettlementWorker(
        SimpleNamespace(_connect=forbidden),
        SimpleNamespace(observation=forbidden),
        DataMode.REPLAY,
        dispatch_allowed=lambda: False,
    )
    now = datetime.now(UTC)
    assert worker.tick(now) == []
    assert worker.process(None, None, now) is None


@pytest.mark.parametrize("value", ["false", "true", "paused", "", "0"])
def test_jobs_dispatch_configuration_rejects_ambiguous_values(value):
    from pydantic import ValidationError
    from yta_alpha.settings import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None, alpha_token="a" * 32, jobs_dispatch=value)
