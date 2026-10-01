"""Portable panel evidence recovery. No supplier calls or campaign approval.

The bundle carries original rows and their permanent IDs. Its digest detects
changed bytes; it is not a signature or an external immutability guarantee.
"""

import hashlib
import json
import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Uuid, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from youwei_core.data.panel import validate_panel_registration
from youwei_core.db.meta import (
    data_sources, panel_registrations, raw_objects, securities, security_identities,
)

BUNDLE_VERSION = "panel-registration-bundle-v1"


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    raise TypeError(f"unsupported bundle value: {type(value).__name__}")


def _json_value(value):
    return json.loads(json.dumps(value, default=_json_default, allow_nan=False))


def _digest(payload):
    content = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _database_row(table, row):
    result = dict(row)
    for column in table.columns:
        value = result[column.name]
        if value is None:
            continue
        if isinstance(column.type, Uuid):
            result[column.name] = uuid.UUID(value)
        elif isinstance(column.type, DateTime):
            result[column.name] = datetime.fromisoformat(value)
        elif isinstance(column.type, Date):
            result[column.name] = date.fromisoformat(value)
    return result


def _check_identity_coverage(payload):
    registration = payload["registration"]
    required_ids = {entry["security_id"] for entry in registration["mapping"]}
    if {row["id"] for row in payload["securities"]} != required_ids:
        raise ValueError("panel bundle has incomplete security identities")
    as_of = date.fromisoformat(registration["frame_as_of"])
    for entry in registration["mapping"]:
        candidates = [row for row in payload["security_identities"] if (
            row["security_id"] == entry["security_id"]
            and row["identifier_type"] == "ticker"
            and row["identifier"].upper() == entry["ticker"].upper()
            and date.fromisoformat(row["valid_from"]) <= as_of
            and (row["valid_to"] is None or date.fromisoformat(row["valid_to"]) >= as_of)
        )]
        if not candidates:
            raise ValueError("panel bundle is missing a frozen member's ticker identity")


async def export_registration_bundle(engine: AsyncEngine, registration_id: uuid.UUID) -> dict:
    """Export exact source bytes, metadata, registration and permanent identities."""
    report = await validate_panel_registration(engine, registration_id)
    if not report["ok"]:
        raise ValueError("panel registration is not valid: " + "; ".join(report["issues"]))
    async with engine.begin() as conn:
        registration = (await conn.execute(select(panel_registrations).where(
            panel_registrations.c.id == registration_id
        ))).mappings().one()
        raw = (await conn.execute(select(raw_objects).where(
            raw_objects.c.id == registration.source_raw_object_id
        ))).mappings().one()
        source = (await conn.execute(select(data_sources).where(
            data_sources.c.id == raw.source_id
        ))).mappings().one()
        ids = [uuid.UUID(entry["security_id"]) for entry in registration.mapping]
        security_rows = (await conn.execute(select(securities).where(
            securities.c.id.in_(ids)
        ).order_by(securities.c.id))).mappings().all()
        identity_rows = (await conn.execute(select(security_identities).where(
            security_identities.c.security_id.in_(ids)
        ).order_by(security_identities.c.id))).mappings().all()
    payload = _json_value({
        "data_source": dict(source), "raw_object": dict(raw),
        "registration": dict(registration),
        "securities": [dict(row) for row in security_rows],
        "security_identities": [dict(row) for row in identity_rows],
    })
    _check_identity_coverage(payload)
    return {"bundle_version": BUNDLE_VERSION, "payload": payload, "content_sha256": _digest(payload)}


async def restore_registration_bundle(engine: AsyncEngine, bundle: dict) -> dict:
    """Import a portable bundle, preserving original IDs and source timestamps."""
    if bundle.get("bundle_version") != BUNDLE_VERSION:
        raise ValueError("unsupported panel bundle version")
    payload = bundle["payload"]
    if bundle.get("content_sha256") != _digest(payload):
        raise ValueError("panel bundle content hash mismatch")
    _check_identity_coverage(payload)
    created = False
    registration_id = uuid.UUID(payload["registration"]["id"])
    async with engine.begin() as conn:
        for table, rows in (
            (data_sources, [payload["data_source"]]),
            (raw_objects, [payload["raw_object"]]),
            (securities, payload["securities"]),
            (security_identities, payload["security_identities"]),
            (panel_registrations, [payload["registration"]]),
        ):
            for row in rows:
                values = _database_row(table, row)
                if table is security_identities:
                    conflict = (await conn.execute(select(table.c.id).where(
                        table.c.identifier_type == values["identifier_type"],
                        func.upper(table.c.identifier) == values["identifier"].upper(),
                        table.c.venue == values["venue"],
                        table.c.security_id != values["security_id"],
                        table.c.valid_from <= (values["valid_to"] or date.max),
                        func.coalesce(table.c.valid_to, date.max) >= values["valid_from"],
                    ).limit(1))).scalar_one_or_none()
                    if conflict is not None:
                        raise ValueError("panel bundle identifier conflicts with another security")
                inserted = (await conn.execute(pg_insert(table).values(**values)
                    .on_conflict_do_nothing().returning(table.c.id))).scalar_one_or_none()
                if table is panel_registrations:
                    created = inserted is not None
                if inserted is None:
                    existing = (await conn.execute(select(table).where(
                        table.c.id == values["id"]
                    ))).mappings().one_or_none()
                    if existing is None or _json_value(dict(existing)) != row:
                        raise ValueError(f"panel bundle conflicts with existing {table.name} record")
        report = await validate_panel_registration(
            conn, registration_id,
            panel_security_ids=payload["registration"]["selected"],
            protocol_sha256=payload["registration"]["protocol_sha256"],
        )
        if not report["ok"]:
            raise ValueError("restored panel registration is not valid: " + "; ".join(report["issues"]))
    return {"registration_id": str(registration_id), "created": created, "validation": report}
