"""Immutable historical backtest inputs, signal sources and reports with full replay."""

import hashlib
import re
from pathlib import Path
from typing import Annotated

from psycopg.types.json import Jsonb
from pydantic import AwareDatetime, Field

from yta_alpha.backtest import ExecutionCosts
from yta_alpha.backtest_signals import BacktestSignalService, RegisteredBacktestRequest
from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash
from yta_alpha.models import Contract, DataMode, MarketSnapshot

PREFIX = "backtest-report-"
CODE_FILES = (
    "backtest.py",
    "backtest_report.py",
    "backtest_signals.py",
    "training.py",
    "experimental_predictions.py",
    "forecast.py",
    "calendar.py",
)


class BacktestInput(Contract):
    requests: Annotated[tuple[RegisteredBacktestRequest, ...], Field(min_length=1, max_length=500)]
    observation: MarketSnapshot
    cutoff: AwareDatetime
    costs: ExecutionCosts


def normalized_input(value):
    parsed = BacktestInput.model_validate(
        value.model_dump(mode="python") if isinstance(value, BacktestInput) else value
    )
    # Caller ordering cannot create a new registration for an identical signal set.
    return parsed.model_copy(
        update={"requests": tuple(sorted(parsed.requests, key=lambda r: str(r.signal_id)))}
    )


def verify_backtest(row, replayed, model_ids):
    payload = row["payload"]
    try:
        value = normalized_input(payload["input"])
        hashes = payload["code_sha256"]
        valid = (
            set(payload) == {"schema_version", "data_mode", "input", "report", "code_sha256"}
            and payload["schema_version"] == "backtest-registration-v1"
            and payload["data_mode"] == row["data_mode"] == replayed["data_mode"]
            and payload["input"] == value.model_dump(mode="json")
            and payload["report"] == replayed
            and row["content_hash"] == content_hash(payload)
            and row["id"] == PREFIX + row["content_hash"]
            and isinstance(hashes, dict)
            and set(hashes) == set(CODE_FILES)
            and all(
                isinstance(h, str) and re.fullmatch(r"[a-f0-9]{64}", h) for h in hashes.values()
            )
            and sorted(model_ids) == sorted({r.model_id for r in value.requests})
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise DomainError("BACKTEST_INTEGRITY_ERROR", "Stored backtest cannot be reproduced")
    return payload


class BacktestRegistry:
    def __init__(self, url, calendar):
        self.service = BacktestSignalService(url, calendar)

    def _connect(self):
        return self.service.models._connect()

    def replay(self, mode, value):
        mode = DataMode(mode)
        return self.service.report(
            value.requests, value.observation, mode=mode, cutoff=value.cutoff, costs=value.costs
        )

    def create(self, mode, value):
        mode, value = DataMode(mode), normalized_input(value)
        report = self.replay(mode, value)
        payload = {
            "schema_version": "backtest-registration-v1",
            "data_mode": mode.value,
            "input": value.model_dump(mode="json"),
            "report": report,
            "code_sha256": {
                name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                for name in CODE_FILES
            },
        }
        digest = content_hash(payload)
        identity = PREFIX + digest
        model_ids = sorted({request.model_id for request in value.requests})
        with self._connect() as connection:
            inserted = connection.execute(
                "INSERT INTO yta.backtest_reports(id,data_mode,content_hash,payload) "
                "VALUES(%s,%s,%s,%s) "
                "ON CONFLICT DO NOTHING RETURNING id",
                (identity, mode.value, digest, Jsonb(payload)),
            ).fetchone()
            if inserted:
                for model_id in model_ids:
                    connection.execute(
                        "INSERT INTO yta.backtest_report_models(report_id,model_id) VALUES(%s,%s)",
                        (identity, model_id),
                    )
                connection.execute(
                    "INSERT INTO yta.audit_events(actor_id,action,resource_id,payload) "
                    "VALUES(NULL,'model.backtest_registered',%s,%s)",
                    (
                        identity,
                        Jsonb(
                            {
                                "data_mode": mode.value,
                                "status": report["status"],
                                "qualification": "UNVERIFIED",
                            }
                        ),
                    ),
                )
            row = connection.execute(
                "SELECT * FROM yta.backtest_reports WHERE id=%s", (identity,)
            ).fetchone()
            references = connection.execute(
                "SELECT model_id FROM yta.backtest_report_models WHERE report_id=%s", (identity,)
            ).fetchall()
            verify_backtest(row, report, [r["model_id"] for r in references])
        return identity

    def get(self, identity, *, mode):
        mode = DataMode(mode)
        with self._connect() as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            row = connection.execute(
                "SELECT * FROM yta.backtest_reports WHERE id=%s AND data_mode=%s",
                (identity, mode.value),
            ).fetchone()
            references = (
                connection.execute(
                    "SELECT model_id FROM yta.backtest_report_models WHERE report_id=%s",
                    (identity,),
                ).fetchall()
                if row
                else []
            )
        if row is None:
            raise DomainError("BACKTEST_NOT_FOUND", "Backtest report not found", status=404)
        try:
            value = normalized_input(row["payload"]["input"])
        except (KeyError, TypeError, ValueError) as error:
            raise DomainError("BACKTEST_INTEGRITY_ERROR", "Stored inputs are invalid") from error
        return verify_backtest(row, self.replay(mode, value), [r["model_id"] for r in references])
