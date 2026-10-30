"""Subprocess checks of CI failure behavior, using synthetic marked tests (no DB calls)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    "url,body,code,message",
    [
        (None, "pass", 4, "dedicated *_test database"),
        (
            "postgresql://user:secret-fixture@localhost/production",
            "pass",
            4,
            "dedicated *_test database",
        ),
        (
            "postgresql://user:secret-fixture@localhost/gate_test",
            "pytest.skip('fixture')",
            1,
            "Required PostgreSQL verification was skipped",
        ),
        ("postgresql://user:secret-fixture@localhost/gate_test", "pass", 0, "1 passed"),
    ],
)
def test_required_postgres_gate(tmp_path, url, body, code, message):
    (tmp_path / "conftest.py").write_text((ROOT / "conftest.py").read_text())
    (tmp_path / "pytest.ini").write_text("[pytest]\nmarkers = postgres: synthetic gate marker\n")
    (tmp_path / "test_gate.py").write_text(
        "import pytest\n@pytest.mark.postgres\ndef test_gate():\n    " + body + "\n"
    )
    env = {key: value for key, value in os.environ.items() if key != "YTA_TEST_DATABASE_URL"}
    if url is not None:
        env["YTA_TEST_DATABASE_URL"] = url
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--require-postgres"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == code
    assert message in result.stdout + result.stderr
    assert "secret-fixture" not in result.stdout + result.stderr


def test_required_postgres_cannot_select_only_unit_tests(tmp_path):
    (tmp_path / "conftest.py").write_text((ROOT / "conftest.py").read_text())
    (tmp_path / "test_gate.py").write_text("def test_unit():\n    pass\n")
    env = {**os.environ, "YTA_TEST_DATABASE_URL": "postgresql://user@localhost/gate_test"}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--require-postgres"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 4
    assert "needs selected postgres tests" in result.stderr
