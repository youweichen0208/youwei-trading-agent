"""Explicit operator entry point for unqualified provider evidence acquisition."""

import argparse
import asyncio
import json
import sys
from datetime import date
from uuid import UUID

import psycopg
from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from yta_alpha.acquisition_store import AcquisitionStore, acquisition_connection
from yta_alpha.errors import DomainError
from yta_alpha.massive import MassiveCandidate
from yta_alpha.models import DataMode


class AcquisitionSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="YTA_ACQUIRE_", env_file=None, extra="ignore", hide_input_in_errors=True
    )
    massive_token: SecretStr = Field(min_length=1)
    database_url: SecretStr = Field(min_length=1)

    @field_validator("massive_token")
    @classmethod
    def private_token(cls, token: SecretStr) -> SecretStr:
        value = token.get_secret_value()
        if (
            not value.isascii()
            or any(c.isspace() for c in value)
            or value.startswith("replace-with")
        ):
            raise ValueError("A private provider credential is required")
        return token


class AttemptError(DomainError):
    def __init__(self, code: str, attempt_id: UUID):
        super().__init__(code, "Acquisition attempt did not complete", status=503)
        self.attempt_id = attempt_id


def check_storage(database_url: str) -> None:
    """Fail before provider requests if migrations or append-only privileges are missing."""
    with acquisition_connection(database_url) as connection:
        for table in (
            "provider_acquisitions",
            "provider_response_pages",
            "market_bars",
            "audit_events",
        ):
            relation = "yta." + table
            if connection.execute("SELECT to_regclass(%s)", (relation,)).fetchone()[0] is None:
                raise DomainError("ACQUISITION_STORAGE_NOT_READY", "Apply required migrations")
            privileges = connection.execute(
                "SELECT has_table_privilege(current_user,%s,'SELECT'), "
                "has_table_privilege(current_user,%s,'INSERT'), "
                "has_table_privilege(current_user,%s,'UPDATE,DELETE,TRUNCATE')",
                (relation, relation, relation),
            ).fetchone()
            if privileges != (True, True, False):
                raise DomainError(
                    "ACQUISITION_STORAGE_NOT_READY", "Use a configured append-only runtime role"
                )


async def collect(settings: AcquisitionSettings, start: date, end: date, mode: DataMode) -> dict:
    if start > end or (end - start).days > 3 * 366:
        raise ValueError("Invalid acquisition date range")
    mode = DataMode(mode)
    database_url = settings.database_url.get_secret_value()
    check_storage(database_url)
    store = AcquisitionStore(database_url)
    attempt = store.start_attempt(start, end, mode)
    try:
        result = await MassiveCandidate(settings.massive_token).acquire(start, end)
    except Exception as error:
        code = error.code if isinstance(error, DomainError) else "ACQUISITION_FAILED"
        try:
            store.fail_attempt(attempt, code)
        except psycopg.Error:
            raise AttemptError("ACQUISITION_STORAGE_FAILED", attempt) from None
        raise AttemptError(code, attempt) from None
    try:
        digest = store.save(result, start=start, end=end, data_mode=mode, attempt_id=attempt)
    except Exception:
        # Commit acknowledgement may be unknown. Leave the started attempt for
        # inspection; never append a false failure or automatically refetch data.
        raise AttemptError("ACQUISITION_STORAGE_FAILED", attempt) from None
    return {
        "attempt_id": str(attempt),
        "acquisition_hash": digest,
        "provider": "massive",
        "symbol": "NVDA",
        "data_mode": mode.value,
        "qualification_status": result.qualification_status,
        "bar_pages": len(result.bars),
        "split_pages": len(result.splits),
        "bar_count": sum(len(page.payload().get("results", [])) for page in result.bars),
        "split_count": len(result.split_events),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Acquire unqualified NVDA evidence from Massive")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--data-mode", choices=[mode.value for mode in DataMode], required=True)
    args = parser.parse_args(argv)
    attempt_id = None
    try:
        result = asyncio.run(
            collect(AcquisitionSettings(), args.start, args.end, DataMode(args.data_mode))
        )
    except (ValidationError, ValueError):
        code = "ACQUISITION_CONFIGURATION_INVALID"
    except DomainError as error:
        code = error.code
        if isinstance(error, AttemptError):
            attempt_id = str(error.attempt_id)
    except psycopg.Error:
        code = "ACQUISITION_STORAGE_FAILED"
    except Exception:
        # Neither provider bodies nor database errors/connection strings may reach logs.
        code = "ACQUISITION_FAILED"
    else:
        print(json.dumps(result))
        return 0
    failure = {"error_code": code}
    if attempt_id is not None:
        failure["attempt_id"] = attempt_id
    print(json.dumps(failure), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
