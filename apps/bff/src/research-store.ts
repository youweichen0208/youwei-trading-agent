import { randomUUID } from "node:crypto";
import { agentEventSchema, type AgentEvent, type DataMode } from "@yta/contracts";
import type { Pool, PoolClient } from "pg";
import { digest } from "./auth.js";
import { lock, transaction } from "./database.js";
import { ApiError } from "./errors.js";

export const terminalTypes = ["run.completed", "run.failed", "run.canceled", "run.execution_unknown"];
export interface Run {
  id: string; session_id: string; owner_id: string; prompt: string;
  data_mode: DataMode; profile_id: "alpha-orchestrator"; created_at: Date;
}
export type EventBody = AgentEvent extends infer E ? E extends AgentEvent ? Pick<E, "type" | "payload"> : never : never;

export class ResearchRepository {
  constructor(readonly pool: Pool, readonly mode: DataMode, readonly runsPer24Hours = 50) {
    if (!Number.isInteger(runsPer24Hours) || runsPer24Hours < 1 || runsPer24Hours > 10000) {
      throw new Error("Invalid research admission limit");
    }
  }

  private checkMode(mode: DataMode) {
    if (mode !== this.mode) throw new ApiError("DATA_MODE_MISMATCH", "Request belongs to another data environment", 409);
  }

  async createSession(owner: string, mode: DataMode, key: string) {
    this.checkMode(mode);
    return transaction(this.pool, async connection => {
      await lock(connection, `session:${owner}:${key}`);
      const hash = digest(JSON.stringify({ profile_id: "alpha-orchestrator", data_mode: mode }));
      const existing = await connection.query("SELECT * FROM yta.research_sessions WHERE owner_id=$1 AND request_key=$2", [owner, key]);
      if (existing.rows[0]) {
        if (existing.rows[0].request_hash !== hash) throw new ApiError("IDEMPOTENCY_CONFLICT", "Session key belongs to another request", 409);
        return this.sessionDto(existing.rows[0]);
      }
      const created = await connection.query("INSERT INTO yta.research_sessions(id,owner_id,profile_id,data_mode,request_key,request_hash) VALUES ($1,$2,'alpha-orchestrator',$3,$4,$5) RETURNING *", [randomUUID(), owner, mode, key, hash]);
      return this.sessionDto(created.rows[0]);
    });
  }

  private sessionDto(row: { id: string; profile_id: string; data_mode: string; created_at: Date }) {
    return { session_id: row.id, profile_id: row.profile_id, data_mode: row.data_mode, created_at: row.created_at.toISOString() };
  }

  async listSessions(owner: string, mode: DataMode, cursor?: string, limit = 25) {
    this.checkMode(mode);
    if (!Number.isInteger(limit) || limit < 1 || limit > 100) throw new ApiError("INVALID_REQUEST", "Invalid page size", 422);
    if (cursor) {
      const boundary = await this.pool.query("SELECT 1 FROM yta.research_sessions WHERE id=$1 AND owner_id=$2 AND data_mode=$3", [cursor, owner, mode]);
      if (!boundary.rowCount) throw new ApiError("NOT_FOUND", "Session cursor not found", 404);
    }
    // Compare timestamps inside PostgreSQL to retain microsecond precision.
    const rows = await this.pool.query(
      "SELECT * FROM yta.research_sessions WHERE owner_id=$1 AND data_mode=$2 " +
      "AND ($3::uuid IS NULL OR (created_at,id) < (SELECT created_at,id FROM yta.research_sessions WHERE id=$3 AND owner_id=$1 AND data_mode=$2)) " +
      "ORDER BY created_at DESC,id DESC LIMIT $4", [owner, mode, cursor ?? null, limit + 1]);
    const items = rows.rows.slice(0, limit).map(row => this.sessionDto(row));
    return { items, next_cursor: rows.rows.length > limit ? items.at(-1)!.session_id : null };
  }

  async requireSession(owner: string, id: string, connection: Pool | PoolClient = this.pool) {
    const result = await connection.query("SELECT * FROM yta.research_sessions WHERE id=$1 AND owner_id=$2 AND data_mode=$3", [id, owner, this.mode]);
    if (!result.rows[0]) throw new ApiError("NOT_FOUND", "Research session not found", 404);
    return result.rows[0] as { id: string; data_mode: DataMode };
  }

  async submit(owner: string, sessionId: string, requestId: string, text: string, mode: DataMode) {
    this.checkMode(mode);
    return transaction(this.pool, async connection => {
      await lock(connection, `message:${owner}:${requestId}`);
      await this.requireSession(owner, sessionId, connection);
      const hash = digest(JSON.stringify({ session_id: sessionId, text }));
      const prior = await connection.query("SELECT * FROM yta.research_runs WHERE owner_id=$1 AND request_id=$2", [owner, requestId]);
      if (prior.rows[0]) {
        if (prior.rows[0].request_hash !== hash) throw new ApiError("IDEMPOTENCY_CONFLICT", "Message key belongs to another request", 409);
        return { run_id: prior.rows[0].id as string, created: false };
      }
      await lock(connection, `session-run:${sessionId}`);
      const busy = await connection.query("SELECT 1 FROM yta.research_runs r WHERE r.session_id=$1 AND NOT EXISTS (SELECT 1 FROM yta.agent_events e WHERE e.run_id=r.id AND e.type=ANY($2::text[])) LIMIT 1", [sessionId, terminalTypes.filter(t => t !== "run.execution_unknown")]);
      if (busy.rowCount) throw new ApiError("SESSION_BUSY", "Wait for the active run or resolve its unknown execution state", 409);
      // All BFF instances serialize new admissions for this authenticated owner/mode.
      // Existing identities return above; terminal status never refunds a remote call.
      await lock(connection, `research-budget:${owner}:${mode}`);
      const usage = await connection.query(
        "SELECT count(*)::integer AS used FROM yta.research_runs r " +
        "JOIN yta.research_sessions s ON s.id=r.session_id " +
        "WHERE r.owner_id=$1 AND s.data_mode=$2 " +
        "AND r.created_at >= statement_timestamp() - interval '24 hours'", [owner, mode]);
      if (usage.rows[0].used >= this.runsPer24Hours) {
        throw new ApiError("RESEARCH_QUOTA_EXCEEDED", "Research request limit reached for the rolling 24-hour window", 429);
      }
      const runId = randomUUID();
      await connection.query("INSERT INTO yta.research_runs(id,session_id,owner_id,request_id,request_hash,prompt) VALUES ($1,$2,$3,$4,$5,$6)", [runId, sessionId, owner, requestId, hash, text]);
      await connection.query("INSERT INTO yta.research_messages(session_id,run_id,role,content) VALUES ($1,$2,'user',$3)", [sessionId, runId, text]);
      return { run_id: runId, created: true };
    });
  }

  async getRun(owner: string, id: string, connection = this.pool as Pool | PoolClient): Promise<Run> {
    const result = await connection.query("SELECT r.*,s.data_mode,s.profile_id FROM yta.research_runs r JOIN yta.research_sessions s ON s.id=r.session_id WHERE r.owner_id=$1 AND r.id=$2 AND s.data_mode=$3", [owner, id, this.mode]);
    if (!result.rows[0]) throw new ApiError("NOT_FOUND", "Research run not found", 404);
    return result.rows[0];
  }

  async runSummary(owner: string, id: string) {
    const run = await this.getRun(owner, id);
    const events = await this.pool.query("SELECT event FROM yta.agent_events WHERE run_id=$1 AND (type LIKE 'run.%' OR type IN ('prediction.draft','message.completed')) ORDER BY sequence", [id]);
    let status = "QUEUED";
    let draft: unknown = null;
    let report: unknown = null;
    for (const row of events.rows) {
      const event = agentEventSchema.parse(row.event);
      if (event.type.startsWith("run.")) status = event.type === "run.started" ? "RUNNING" : event.type.slice(4).toUpperCase();
      if (event.type === "prediction.draft") draft = event.payload;
      if (event.type === "message.completed" && event.payload.report) report = event.payload.report;
    }
    return { run_id: id, session_id: run.session_id, status, data_mode: run.data_mode, draft, report, created_at: run.created_at.toISOString() };
  }

  async pending(): Promise<Run[]> {
    const result = await this.pool.query("SELECT r.*,s.data_mode,s.profile_id FROM yta.research_runs r JOIN yta.research_sessions s ON s.id=r.session_id WHERE s.data_mode=$2 AND NOT EXISTS (SELECT 1 FROM yta.agent_events e WHERE e.run_id=r.id AND e.type=ANY($1::text[])) ORDER BY r.created_at LIMIT 200", [terminalTypes, this.mode]);
    return result.rows;
  }

  async append(run: Run, input: EventBody, cancellationRequiresConfirmation = false): Promise<AgentEvent | null> {
    return transaction(this.pool,c=>this.appendWithin(c,run,input,cancellationRequiresConfirmation));
  }

  async appendWithin(connection: PoolClient, run: Run, input: EventBody, cancellationRequiresConfirmation = false): Promise<AgentEvent | null> {
    this.checkMode(run.data_mode);
    const actual=await this.getRun(run.owner_id,run.id,connection);
    if(actual.session_id!==run.session_id || actual.profile_id!==run.profile_id)throw new ApiError("NOT_FOUND","Research run not found",404);
      await lock(connection, `events:${run.id}`);
      const previous = await connection.query("SELECT type,sequence FROM yta.agent_events WHERE run_id=$1 ORDER BY sequence DESC LIMIT 1", [run.id]);
      if (terminalTypes.includes(previous.rows[0]?.type ?? "")) return null;
      const cancel = await this.isCanceled(run.id, connection);
      if (cancel && !terminalTypes.includes(input.type)) return null;
      const body: EventBody = cancel && input.type === "run.completed"
        ? cancellationRequiresConfirmation
          ? {type:"run.execution_unknown",payload:{code:"EXECUTION_UNKNOWN",message:"Cancellation raced with remote completion; reconcile the saved run",request_id:run.id,retryable:false}}
          : { type: "run.canceled", payload: {} } : input;
      const event = agentEventSchema.parse({ ...body, event_id: randomUUID(), schema_version: 1, session_id: run.session_id, run_id: run.id, profile_id: run.profile_id, sequence: (previous.rows[0]?.sequence ?? 0) + 1, occurred_at: new Date().toISOString() });
      await connection.query("INSERT INTO yta.agent_events(event_id,run_id,sequence,type,event) VALUES ($1,$2,$3,$4,$5)", [event.event_id, run.id, event.sequence, event.type, event]);
      if (terminalTypes.includes(event.type)) {
        const chunks = await connection.query("SELECT event->'payload'->>'text' AS text FROM yta.agent_events WHERE run_id=$1 AND type='message.delta' ORDER BY sequence", [run.id]);
        const completed = event.type === "run.completed"
          ? await connection.query("SELECT event->'payload'->>'text' AS text FROM yta.agent_events WHERE run_id=$1 AND type='message.completed' ORDER BY sequence DESC LIMIT 1", [run.id])
          : null;
        const text = completed?.rows[0]?.text ?? chunks.rows.map(r => r.text as string).join("");
        if (text) await connection.query("INSERT INTO yta.research_messages(session_id,run_id,role,content,partial) VALUES ($1,$2,'assistant',$3,$4) ON CONFLICT DO NOTHING", [run.session_id, run.id, text, event.type !== "run.completed"]);
      }
      return event;
  }

  async events(owner: string, runId: string, after: number) {
    await this.getRun(owner, runId);
    const result = await this.pool.query("SELECT event FROM yta.agent_events WHERE run_id=$1 AND sequence>$2 ORDER BY sequence LIMIT 100", [runId, after]);
    return result.rows.map(row => agentEventSchema.parse(row.event));
  }

  async cursor(owner: string, runId: string, cursor?: string) {
    await this.getRun(owner, runId);
    if (!cursor) return 0;
    const result = await this.pool.query("SELECT sequence FROM yta.agent_events WHERE run_id=$1 AND event_id=$2", [runId, cursor]);
    if (!result.rows[0]) throw new ApiError("CURSOR_EXPIRED", "Reload persisted history before subscribing again", 409);
    return result.rows[0].sequence as number;
  }

  async messages(owner: string, session: string, after: string = "0") {
    await this.requireSession(owner, session);
    // Old immutable messages may contain the structured model stream. Project final
    // prose from the verified completion event without rewriting historical facts.
    const result = await this.pool.query(`SELECT m.id::text,m.run_id,m.role,
      CASE WHEN m.role='assistant' AND NOT m.partial AND EXISTS (
        SELECT 1 FROM yta.agent_events t WHERE t.run_id=m.run_id AND t.type='run.completed'
      ) THEN COALESCE((SELECT e.event->'payload'->>'text' FROM yta.agent_events e
        WHERE e.run_id=m.run_id AND e.type='message.completed' ORDER BY e.sequence DESC LIMIT 1),m.content)
      ELSE m.content END AS content,m.partial,m.created_at
      FROM yta.research_messages m WHERE m.session_id=$1 AND m.id>$2 ORDER BY m.id LIMIT 101`, [session, after]);
    const items = result.rows.slice(0, 100).map(row => ({ ...row, created_at: row.created_at.toISOString() }));
    return { items, next_cursor: result.rows.length > 100 ? items.at(-1)?.id ?? null : null };
  }

  async isCanceled(runId: string, connection: Pool | PoolClient = this.pool): Promise<boolean> {
    return Boolean((await connection.query("SELECT 1 FROM yta.run_cancellations WHERE run_id=$1", [runId])).rowCount);
  }

  async cancel(owner: string, runId: string) {
    await this.getRun(owner, runId);
    await this.pool.query("INSERT INTO yta.run_cancellations(run_id,owner_id) VALUES ($1,$2) ON CONFLICT DO NOTHING", [runId, owner]);
    return this.runSummary(owner, runId);
  }
}
