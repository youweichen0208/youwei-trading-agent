"""Optional CI gate: PostgreSQL tests must be selected and cannot pass by skipping."""

import os

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict


def pytest_addoption(parser):
    parser.addoption(
        "--require-postgres", action="store_true", help="Fail if DB tests are absent/skipped"
    )


def pytest_configure(config):
    if not config.getoption("--require-postgres"):
        return
    try:
        value = os.environ.get("YTA_TEST_DATABASE_URL")
        if not value or not conninfo_to_dict(value).get("dbname", "").endswith("_test"):
            raise ValueError("Dedicated test DB required")
    except (ValueError, psycopg.Error):
        raise pytest.UsageError(
            "--require-postgres needs YTA_TEST_DATABASE_URL naming a dedicated *_test database"
        ) from None


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    if config.getoption("--require-postgres") and not any(
        item.get_closest_marker("postgres") for item in items
    ):
        raise pytest.UsageError("--require-postgres needs selected postgres tests")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if (
        item.config.getoption("--require-postgres")
        and item.get_closest_marker("postgres")
        and report.skipped
    ):
        report.outcome = "failed"
        report.longrepr = (
            "Required PostgreSQL verification was skipped; database verification failed"
        )
