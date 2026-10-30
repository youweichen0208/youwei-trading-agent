"""Exercise installed acquisition CLIs against synthetic HTTP and an isolated test DB."""

import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from yta_alpha.migrate import migrate

BASE = "host=127.0.0.1 port=54329 user=yta_owner password=local-yta-owner dbname=postgres"

# Executed by the installed production interpreter, with no source/test mounts.
# MockTransport prevents any outbound provider request; no real key is supplied.
COLLECT = """
import importlib.util
import os
import httpx
from yta_alpha import acquire
from yta_alpha.massive import MassiveCandidate
assert os.getuid() == 10001
assert importlib.util.find_spec('mcp') is None
def handler(request):
    assert request.url.host == 'api.massive.com'
    result = {'status': 'OK', 'results': []}
    if request.url.path.startswith('/v2/aggs/'):
        return httpx.Response(200, headers={'Content-Type': 'application/json'}, content=(
            b'{"status":"OK","ticker":"NVDA","adjusted":false,"results":['
            b'{"t":1732683600000,"o":100.12345678901234567890123456789,'
            b'"h":102,"l":99,"c":101,"v":123.456}]}'
        ))
    return httpx.Response(200, json=result)
acquire.MassiveCandidate = lambda token: MassiveCandidate(
    token, transport=httpx.MockTransport(handler)
)
raise SystemExit(acquire.main([
    '--start', '2024-11-27', '--end', '2024-12-02', '--data-mode', 'REPLAY'
]))
"""


READ_DAILY = """
import json
import os
import sys
from yta_alpha.acquisition_store import AcquisitionStore
from yta_alpha.errors import DomainError
from yta_alpha.models import DataMode
store = AcquisitionStore(os.environ['YTA_ACQUIRE_DATABASE_URL'])
digest = sys.argv[1]
saved = store.load(digest, data_mode=DataMode.REPLAY)
rows = store.daily_bars(digest, data_mode=DataMode.REPLAY)
assert len(rows) == 1
row = rows[0]
assert row['open'] == '100.12345678901234567890123456789'
assert row['volume'] == '123.456'
assert row['session_date'] == '2024-11-27'
assert row['available_at'] == row['retrieved_at']
assert row['available_at'] == saved.acquisition.bars[0].retrieved_at.isoformat()
assert row['published_at'] is None
assert row['source_page_hash'] == saved.acquisition.bars[0].content_hash
assert row['price_basis'] == 'RAW' and row['qualification_status'] == 'UNVERIFIED'
assert (row['page_index'], row['row_index']) == (0, 0)
assert store.save(saved.acquisition, start=saved.start, end=saved.end,
                  data_mode=DataMode.REPLAY) == digest
assert store.daily_bars(digest, data_mode=DataMode.REPLAY) == rows
try:
    store.daily_bars(digest, data_mode=DataMode.LIVE_RESEARCH)
except DomainError as error:
    assert error.code == 'NOT_FOUND'
else:
    raise AssertionError('Mode boundary failed')
print(json.dumps({'daily_records': len(rows), 'precision_and_source_verified': True,
                  'same_key_retry_verified': True, 'mode_isolation_verified': True}))
"""


def run(image: str, database: str, command: list[str], *, provider: bool):
    env = dict(os.environ)
    env["YTA_ACQUIRE_DATABASE_URL"] = f"postgresql://yta_app:local-yta-app@postgres:5432/{database}"
    args = [
        "docker",
        "run",
        "--rm",
        "--network",
        "yta-dev_default",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--tmpfs",
        "/tmp",
        "--env",
        "YTA_ACQUIRE_DATABASE_URL",
    ]
    if provider:
        env["YTA_ACQUIRE_MASSIVE_TOKEN"] = "synthetic-container-credential"
        args.extend(["--env", "YTA_ACQUIRE_MASSIVE_TOKEN"])
    result = subprocess.run(
        [*args, image, *command], env=env, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0 and not result.stderr
    return json.loads(result.stdout)


def main():
    image = subprocess.run(
        ["docker", "image", "inspect", "yta-alpha:local", "--format", "{{.Id}}"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.strip()
    name = "yta_acquisition_" + uuid4().hex + "_test"
    with psycopg.connect(BASE, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            owner = BASE.replace("dbname=postgres", f"dbname={name}")
            migrate(owner, Path("infra/migrations"))
            with psycopg.connect(owner) as connection:
                connection.execute("GRANT USAGE ON SCHEMA yta TO yta_app")
                connection.execute("GRANT SELECT,INSERT ON ALL TABLES IN SCHEMA yta TO yta_app")
                connection.execute("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA yta TO yta_app")
            created = run(image, name, ["python", "-c", COLLECT], provider=True)
            assert created["qualification_status"] == "UNVERIFIED"
            attempt = run(
                image,
                name,
                [
                    "python",
                    "-m",
                    "yta_alpha.inspect_acquisition",
                    "--attempt-id",
                    created["attempt_id"],
                    "--data-mode",
                    "REPLAY",
                ],
                provider=False,
            )
            assert attempt["status"] == "COMPLETED" and attempt["integrity_checked"] is True
            assert attempt["acquisition_hash"] == created["acquisition_hash"]
            inspected = run(
                image,
                name,
                [
                    "python",
                    "-m",
                    "yta_alpha.inspect_acquisition",
                    "--hash",
                    created["acquisition_hash"],
                    "--data-mode",
                    "REPLAY",
                ],
                provider=False,
            )
            assert inspected["integrity_checked"] is True
            assert inspected["qualification_status"] == "UNVERIFIED"
            assert inspected["quality"]["missing_sessions"] == [
                "2024-11-29",
                "2024-12-02",
            ]
            assert inspected["quality"]["status"] == "ISSUES_FOUND"
            daily = run(
                image,
                name,
                ["python", "-c", READ_DAILY, created["acquisition_hash"]],
                provider=False,
            )
            assert daily["daily_records"] == 1
            with psycopg.connect(owner) as connection:
                assert connection.execute("SELECT count(*) FROM yta.market_bars").fetchone()[0] == 1
                assert (
                    connection.execute(
                        "SELECT count(*) FROM yta.provider_response_pages"
                    ).fetchone()[0]
                    == 2
                )
                assert (
                    connection.execute(
                        "SELECT count(*) FROM yta.audit_events WHERE action='provider.acquire'"
                    ).fetchone()[0]
                    == 1
                )
                assert (
                    connection.execute("SELECT count(*) FROM yta.data_snapshots").fetchone()[0] == 0
                )
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
    print(
        json.dumps(
            {
                "image": image,
                "acquisition_container": "passed",
                "provider_requests": 0,
                **daily,
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit(
            "Acquisition container verification failed (private details not echoed)"
        ) from None
