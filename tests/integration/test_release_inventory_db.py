import importlib
import os
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

pytestmark = pytest.mark.postgres
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def context(monkeypatch):
    url = os.environ.get("YTA_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Dedicated PostgreSQL test connection required")
    assert conninfo_to_dict(url)["dbname"].endswith("_test")
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    module = importlib.import_module("release_inventory")
    catalog = {p.name: module.sha(p) for p in (ROOT / "infra/migrations").glob("*.sql")}
    return module, url, catalog


def test_real_registry_matches_without_changing_rows(context):
    module, url, catalog = context
    with psycopg.connect(url) as connection:
        before = connection.execute("SELECT * FROM public.yta_migrations ORDER BY name").fetchall()
    report = module.database_migrations(url, catalog)
    assert report == {
        "status": "consistent",
        "read_only": True,
        "applied_count": len(catalog),
        "pending": [],
        "unexpected_applied": [],
        "checksum_conflicts": [],
    }
    with psycopg.connect(url) as connection:
        after = connection.execute("SELECT * FROM public.yta_migrations ORDER BY name").fetchall()
    assert before == after


@pytest.mark.parametrize("difference", ["pending", "older_artifact", "checksum"])
def test_real_registry_rejects_incompatible_release_catalog(context, difference):
    module, url, catalog = context
    latest = sorted(catalog)[-1]
    if difference == "pending":
        catalog["999_future.sql"] = "f" * 64
    elif difference == "older_artifact":
        catalog.pop(latest)
    else:
        catalog[latest] = "f" * 64
    report = module.database_migrations(url, catalog)
    assert report["read_only"] is True
    assert report["status"] == ("pending" if difference == "pending" else "conflict")
    key, expected = {
        "pending": ("pending", "999_future.sql"),
        "older_artifact": ("unexpected_applied", latest),
        "checksum": ("checksum_conflicts", latest),
    }[difference]
    assert report[key] == [expected]


def test_runtime_without_migration_metadata_permission_is_not_reported_consistent(context):
    module, url, catalog = context
    config = conninfo_to_dict(url)
    config.update(user="yta_app", password="local-yta-app")
    runtime = os.environ.get("YTA_TEST_RUNTIME_DATABASE_URL", make_conninfo(**config))
    assert module.database_migrations(runtime, catalog) == {"status": "metadata_permission_missing"}
