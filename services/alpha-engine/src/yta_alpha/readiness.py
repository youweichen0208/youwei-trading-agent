"""Bounded, read-only dependency probes; return categories, never connection diagnostics."""

import psycopg

from yta_alpha.models import DataMode
from yta_alpha.settings import Settings

FACT_TABLES = (
    "symbols",
    "model_versions",
    "data_snapshots",
    "prediction_drafts",
    "predictions",
    "prediction_events",
    "prediction_outcomes",
    "idempotency_records",
    "audit_events",
    "evaluation_reports",
    "settlement_attempts",
    "settlement_evidence",
    "outcome_evidence",
    "outcome_base_revisions",
    "draft_requests",
)


def database_status(settings: Settings) -> str:
    if settings.database_url is None:
        return "not_configured"
    try:
        with psycopg.connect(
            settings.database_url.get_secret_value(),
            connect_timeout=2,
            options="-c statement_timeout=2000 -c lock_timeout=1000",
        ) as connection:
            names = [f"yta.{name}" for name in FACT_TABLES]
            rows = connection.execute(
                "SELECT name,to_regclass(name) IS NOT NULL AS present, "
                "has_table_privilege(current_user,to_regclass(name),'SELECT') "
                "AND has_table_privilege(current_user,to_regclass(name),'INSERT') "
                "AND NOT has_table_privilege(current_user,to_regclass(name),'UPDATE') "
                "AND NOT has_table_privilege(current_user,to_regclass(name),'DELETE') "
                "AND NOT has_table_privilege(current_user,to_regclass(name),'TRUNCATE') AS safe "
                "FROM unnest(%s::text[]) name",
                (names,),
            ).fetchall()
            if any(not row[1] for row in rows):
                return "schema_missing"
            if any(row[2] is not True for row in rows):
                return "permissions_invalid"
            guards = connection.execute(
                "SELECT count(*) FROM pg_trigger WHERE tgrelid=ANY("
                "SELECT to_regclass(name) FROM unnest(%s::text[]) name) "
                "AND tgname='immutable_fact' AND tgenabled='O'",
                (names,),
            ).fetchone()[0]
            if guards != len(names):
                return "immutable_guards_missing"
        return "ready"
    except Exception:
        return "unavailable"


def readiness(settings: Settings) -> dict:
    database = database_status(settings)
    replay = settings.data_mode == DataMode.REPLAY and settings.env in ("development", "test")
    market = "replay" if replay else "unverified"
    return {
        "status": "ready" if database == "ready" and replay else "not_ready",
        "service": "alpha-engine",
        "data_mode": settings.data_mode.value,
        "checks": {"database": database, "market": market},
    }
