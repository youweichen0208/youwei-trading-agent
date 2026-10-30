"""Authenticated run snapshots and immutable read-only finance tool receipts."""

from typing import Literal
from uuid import UUID

from psycopg.types.json import Jsonb

from yta_alpha.errors import DomainError
from yta_alpha.forecast import content_hash, snapshot_hash
from yta_alpha.indicators import indicators
from yta_alpha.models import Contract, DataMode, MarketSnapshot, ToolMarketSnapshot
from yta_alpha.store import ResearchStore

ToolName = Literal["market_snapshot", "indicators"]


class ToolContextRequest(Contract):
    run_id: UUID


class ToolObservationRequest(Contract):
    context_id: UUID
    request_id: UUID
    tool: ToolName


class ResearchTools:
    def __init__(self, store: ResearchStore):
        self.store = store

    @staticmethod
    def _run(connection, owner, run_id, mode):
        row = connection.execute(
            "SELECT r.owner_id,s.data_mode,s.profile_id FROM yta.research_runs r "
            "JOIN yta.research_sessions s ON s.id=r.session_id WHERE r.id=%s",
            (run_id,),
        ).fetchone()
        if (
            row is None
            or row["data_mode"] != mode.value
            or row["profile_id"] != "alpha-orchestrator"
            or (owner is not None and row["owner_id"] != owner)
        ):
            raise DomainError("NOT_FOUND", "Research context not found", status=404)
        return row["owner_id"]

    def _read(self, connection, run_id, mode, owner=None):
        actual_owner = self._run(connection, owner, run_id, mode)
        row = connection.execute(
            "SELECT c.*,s.payload FROM yta.research_tool_contexts c "
            "JOIN yta.data_snapshots s ON s.hash=c.snapshot_hash WHERE c.run_id=%s",
            (run_id,),
        ).fetchone()
        if row is None:
            raise DomainError("NOT_FOUND", "Research context not found", status=404)
        snapshot = MarketSnapshot.model_validate(row["payload"])
        if (
            row["owner_id"] != actual_owner
            or row["data_mode"] != mode.value
            or snapshot.data_mode != mode
            or snapshot_hash(snapshot) != row["snapshot_hash"]
        ):
            raise DomainError("RESEARCH_CONTEXT_INVALID", "Research snapshot binding differs")
        return snapshot

    def create(self, owner: UUID, run_id: UUID, mode: DataMode, load_snapshot):
        with self.store._connect() as connection:
            self.store._lock(connection, f"research-context:{run_id}")
            self._run(connection, owner, run_id, mode)
            if not connection.execute(
                "SELECT 1 FROM yta.research_tool_contexts WHERE run_id=%s", (run_id,)
            ).fetchone():
                snapshot = load_snapshot()
                if snapshot.data_mode != mode:
                    raise DomainError("DATA_MODE_MISMATCH", "Research snapshot mode differs")
                digest = self.store._save_snapshot(connection, snapshot)
                connection.execute(
                    "INSERT INTO yta.research_tool_contexts"
                    "(run_id,owner_id,data_mode,snapshot_hash) "
                    "VALUES(%s,%s,%s,%s)",
                    (run_id, owner, mode.value, digest),
                )
                connection.execute(
                    "INSERT INTO yta.audit_events(actor_id,action,resource_id,payload) "
                    "VALUES(%s,'research.context_fixed',%s,%s)",
                    (owner, str(run_id), Jsonb({"snapshot_hash": digest, "data_mode": mode.value})),
                )
            snapshot = self._read(connection, run_id, mode, owner)
        return self.summary(run_id, snapshot)

    def draft(self, owner, run_id, mode, clock):
        # The context is immutable; no current-market read may replace this input.
        with self.store._connect() as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            snapshot = self._read(connection, run_id, mode, owner)
        now = snapshot.as_of if mode == DataMode.REPLAY else clock()
        draft = self.store.create_draft(owner, snapshot, now, run_id)
        saved = draft["forecast"]
        if (
            saved["data_snapshot_hash"] != snapshot_hash(snapshot)
            or saved["data_mode"] != mode.value
            or saved["as_of"] != snapshot.model_dump(mode="json")["as_of"]
        ):
            raise DomainError("RESEARCH_CONTEXT_INVALID", "Draft differs from fixed context")
        return draft

    @staticmethod
    def summary(run_id, snapshot):
        available = max(item.available_at for item in (*snapshot.bars, *snapshot.splits))
        return {
            "context_id": str(run_id),
            "data_mode": snapshot.data_mode.value,
            "as_of": snapshot.model_dump(mode="json")["as_of"],
            "data_snapshot_hash": snapshot_hash(snapshot),
            "source_id": snapshot.source_id,
            "available_at": available.isoformat().replace("+00:00", "Z"),
        }

    def result(self, identity, run_id, name, snapshot):
        result = (
            ToolMarketSnapshot(
                snapshot=snapshot, data_snapshot_hash=snapshot_hash(snapshot)
            ).model_dump(mode="json")
            if name == "market_snapshot"
            else indicators(snapshot, self.store.calendar)
        )
        return {
            "schema_version": "research-tool-result-v1",
            "tool_call_id": str(identity),
            "tool": name,
            **self.summary(run_id, snapshot),
            "result": result,
        }

    def observe(self, request: ToolObservationRequest, mode: DataMode):
        with self.store._connect() as connection:
            self.store._lock(connection, f"research-tool:{request.request_id}")
            self.store._lock(connection, f"research-context:{request.context_id}")
            snapshot = self._read(connection, request.context_id, mode)
            existing = connection.execute(
                "SELECT 1 FROM yta.research_tool_observations WHERE id=%s",
                (request.request_id,),
            ).fetchone()
            if not existing:
                count = connection.execute(
                    "SELECT count(*) AS n FROM yta.research_tool_observations WHERE run_id=%s",
                    (request.context_id,),
                ).fetchone()["n"]
                if count >= 100:
                    raise DomainError("TOOL_BUDGET_EXCEEDED", "Tool receipt budget exceeded")
            payload = self.result(request.request_id, request.context_id, request.tool, snapshot)
            digest = content_hash(payload)
            inserted = connection.execute(
                "INSERT INTO yta.research_tool_observations(id,run_id,tool,content_hash,payload) "
                "VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id",
                (request.request_id, request.context_id, request.tool, digest, Jsonb(payload)),
            ).fetchone()
            row = connection.execute(
                "SELECT payload,content_hash FROM yta.research_tool_observations WHERE id=%s",
                (request.request_id,),
            ).fetchone()
            if row["payload"] != payload or row["content_hash"] != digest:
                raise DomainError(
                    "IDEMPOTENCY_CONFLICT", "Tool receipt identity differs", status=409
                )
            if inserted:
                connection.execute(
                    "INSERT INTO yta.audit_events(actor_id,action,resource_id,payload) "
                    "VALUES(NULL,'research.tool_observed',%s,%s)",
                    (
                        str(request.request_id),
                        Jsonb(
                            {
                                "run_id": str(request.context_id),
                                "tool": request.tool,
                                "content_hash": digest,
                            }
                        ),
                    ),
                )
        return payload

    def get(self, owner, run_id, mode):
        with self.store._connect() as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            snapshot = self._read(connection, run_id, mode, owner)
            rows = connection.execute(
                "SELECT * FROM yta.research_tool_observations WHERE run_id=%s "
                "ORDER BY created_at,id LIMIT 101",
                (run_id,),
            ).fetchall()
            if len(rows) > 100:
                raise DomainError("RESEARCH_CONTEXT_INVALID", "Tool receipt budget exceeded")
            receipts = []
            for row in rows:
                expected = self.result(row["id"], run_id, row["tool"], snapshot)
                if row["payload"] != expected or row["content_hash"] != content_hash(expected):
                    raise DomainError("RESEARCH_CONTEXT_INVALID", "Tool receipt cannot replay")
                receipts.append({k: v for k, v in expected.items() if k != "result"})
        return {**self.summary(run_id, snapshot), "receipts": receipts}
