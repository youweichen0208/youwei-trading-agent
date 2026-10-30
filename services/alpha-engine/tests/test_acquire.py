import asyncio
import json
import os
import subprocess
import sys
from datetime import date

import psycopg
import pytest
from yta_alpha import acquire
from yta_alpha.errors import DomainError
from yta_alpha.models import DataMode

ARGS = ["--start", "2024-01-01", "--end", "2025-01-01", "--data-mode", "REPLAY"]
SECRET = "private-test-value-never-print"


def configure(monkeypatch):
    monkeypatch.setenv("YTA_ACQUIRE_MASSIVE_TOKEN", SECRET)
    monkeypatch.setenv(
        "YTA_ACQUIRE_DATABASE_URL", "postgresql://user:" + SECRET + "@localhost/test"
    )


def test_unconfigured_subprocess_exits_without_loading_project_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith("YTA_ACQUIRE_")}
    result = subprocess.run(
        [sys.executable, "-m", "yta_alpha.acquire", *ARGS],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 1 and not result.stdout
    assert json.loads(result.stderr) == {"error_code": "ACQUISITION_CONFIGURATION_INVALID"}


@pytest.mark.parametrize(
    "error,code",
    [
        (DomainError("PROVIDER_AUTH_FAILED", SECRET), "PROVIDER_AUTH_FAILED"),
        (psycopg.OperationalError(SECRET), "ACQUISITION_STORAGE_FAILED"),
        (RuntimeError(SECRET), "ACQUISITION_FAILED"),
    ],
)
def test_cli_errors_do_not_emit_secrets_or_tracebacks(monkeypatch, capsys, error, code):
    configure(monkeypatch)

    async def fail(*args):
        raise error

    monkeypatch.setattr(acquire, "collect", fail)
    assert acquire.main(ARGS) == 1
    output = capsys.readouterr()
    assert not output.out and json.loads(output.err) == {"error_code": code}
    assert SECRET not in output.err


def test_bad_range_rejected_before_database_or_provider(monkeypatch):
    configure(monkeypatch)

    def forbidden(*args):
        pytest.fail("Invalid request must have no I/O")

    monkeypatch.setattr(acquire, "check_storage", forbidden)
    monkeypatch.setattr(acquire, "MassiveCandidate", forbidden)
    with pytest.raises(ValueError):
        asyncio.run(
            acquire.collect(
                acquire.AcquisitionSettings(), date(2025, 1, 1), date(2024, 1, 1), DataMode.REPLAY
            )
        )


def test_storage_failure_prevents_provider_request(monkeypatch):
    configure(monkeypatch)

    def not_ready(*args):
        raise DomainError("ACQUISITION_STORAGE_NOT_READY", "Fixture")

    def forbidden(*args):
        pytest.fail("Storage must be checked before requesting paid data")

    monkeypatch.setattr(acquire, "check_storage", not_ready)
    monkeypatch.setattr(acquire, "MassiveCandidate", forbidden)
    with pytest.raises(DomainError, match="Fixture"):
        asyncio.run(
            acquire.collect(
                acquire.AcquisitionSettings(), date(2024, 1, 1), date(2025, 1, 1), DataMode.REPLAY
            )
        )
