import { randomUUID } from "node:crypto";
import type { Pool } from "pg";
import { z } from "zod";
import { transaction, lockOwner } from "./db.js";
import { DomainError } from "./tasks.js";

export const uuid = z.string().uuid();
export const symbol = z.string().regex(/^[A-Z][A-Z0-9.-]{0,14}$/);
export const decimalString = z
  .string()
  .regex(/^(0|[1-9]\d{0,17})(\.\d{1,18})?$/);
export const holdings = z
  .object({
    asOfSession: z.iso.date(),
    positions: z
      .array(z.object({ symbol, quantity: decimalString }).strict())
      .max(100),
    cashUsd: decimalString,
  })
  .strict()
  .refine(
    (v) =>
      new Set(v.positions.map((p) => p.symbol)).size === v.positions.length,
    { message: "duplicate_symbol" },
  );
export class BusinessStore {
  constructor(readonly pool: Pool) {}
  async threads(owner: string) {
    return (
      await this.pool.query(
        "SELECT t.id,t.title,t.created_at,c.kind AS source,t.source AS channel FROM threads t JOIN thread_contexts tc ON tc.thread_id=t.id AND tc.owner_id=t.owner_id JOIN execution_contexts c ON c.id=tc.context_id WHERE t.owner_id=$1 AND t.source<>'background' AND t.deleted_at IS NULL ORDER BY t.created_at DESC LIMIT 100",
        [owner],
      )
    ).rows;
  }
  async thread(owner: string, id: string) {
    return transaction(this.pool, async (c) => {
      await c.query(
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
      );
      const thread = (
        await c.query(
          "SELECT t.id,t.title,c.kind AS source,t.source AS channel FROM threads t JOIN thread_contexts tc ON tc.thread_id=t.id AND tc.owner_id=t.owner_id JOIN execution_contexts c ON c.id=tc.context_id WHERE t.id=$1 AND t.owner_id=$2 AND t.deleted_at IS NULL",
          [id, owner],
        )
      ).rows[0];
      if (!thread) throw new DomainError("not_found", 404);
      const messages = (
        await c.query(
          `SELECT m.id,m.role,m.content,m.task_id FROM messages m JOIN tasks t ON t.id=m.task_id
    WHERE m.thread_id=$1 AND m.owner_id=$2 ORDER BY t.ordinal,CASE m.role WHEN 'user' THEN 0 ELSE 1 END`,
          [id, owner],
        )
      ).rows;
      const tasks = (
        await c.query(
          "SELECT id,role,status,output,stream_text,stream_message_id,created_at FROM tasks WHERE thread_id=$1 AND owner_id=$2 AND parent_id IS NULL ORDER BY ordinal DESC",
          [id, owner],
        )
      ).rows;
      const workspace = (
        await c.query(
          "SELECT id,version,revision,snapshot FROM workspaces WHERE thread_id=$1 AND owner_id=$2",
          [id, owner],
        )
      ).rows[0];
      return { ...thread, messages, tasks, workspace };
    });
  }
  async deleteThread(owner: string, id: string) {
    return transaction(this.pool, async (c) => {
      const thread = (
        await c.query(
          "SELECT id FROM threads WHERE id=$1 AND owner_id=$2 AND deleted_at IS NULL FOR UPDATE",
          [id, owner],
        )
      ).rows[0];
      if (!thread) throw new DomainError("not_found", 404);
      const active = await c.query(
        "SELECT 1 FROM tasks WHERE thread_id=$1 AND owner_id=$2 AND status IN ('queued','running','stopping','unknown') LIMIT 1",
        [id, owner],
      );
      if (active.rowCount)
        throw new DomainError("thread_busy", 409);
      await c.query(
        "UPDATE threads SET deleted_at=now() WHERE id=$1 AND owner_id=$2",
        [id, owner],
      );
      return { id, deleted: true };
    });
  }
  async resource(
    owner: string,
    kind: "datasets" | "results" | "workspaces",
    id: string,
  ) {
    const row = (
      await this.pool.query(
        `SELECT * FROM ${kind} WHERE id=$1 AND owner_id=$2`,
        [id, owner],
      )
    ).rows[0];
    if (!row) throw new DomainError("not_found", 404);
    return row;
  }
  async saveLayout(
    owner: string,
    id: string,
    expectedVersion: number,
    order: string[],
  ) {
    return transaction(this.pool, async (c) => {
      await lockOwner(c, owner);
      const row = (
        await c.query(
          "SELECT * FROM workspaces WHERE id=$1 AND owner_id=$2 FOR UPDATE",
          [id, owner],
        )
      ).rows[0];
      if (!row) throw new DomainError("not_found", 404);
      const context = (
        await c.query(
          "SELECT c.kind FROM thread_contexts tc JOIN execution_contexts c ON c.id=tc.context_id WHERE tc.thread_id=$1 AND tc.owner_id=$2",
          [row.thread_id, owner],
        )
      ).rows[0];
      if (context?.kind !== "personal")
        throw new DomainError("group_research_read_only", 403);
      if (row.version !== expectedVersion)
        throw new DomainError("version_conflict");
      const artifacts: Array<{ artifactId: string }> = row.snapshot.artifacts;
      if (
        order.length !== artifacts.length ||
        new Set(order).size !== order.length ||
        order.some((id) => !artifacts.some((a) => a.artifactId === id))
      )
        throw new DomainError("invalid_layout", 400);
      if (order.every((id, index) => id === artifacts[index]?.artifactId))
        return {
          id: row.id,
          version: row.version,
          revision: row.revision,
          snapshot: row.snapshot,
        };
      await c.query(
        "UPDATE tasks SET artifact_order=$3 WHERE workspace_id=$1 AND workspace_revision=$2 AND parent_id IS NULL",
        [id, row.revision, order],
      );
      const snapshot = {
        ...row.snapshot,
        artifacts: order.map((id) =>
          artifacts.find((a) => a.artifactId === id),
        ),
      };
      return (
        await c.query(
          "UPDATE workspaces SET snapshot=$3,version=version+1 WHERE id=$1 AND owner_id=$2 RETURNING id,version,revision,snapshot",
          [id, owner, JSON.stringify(snapshot)],
        )
      ).rows[0];
    });
  }
  async saveHoldings(
    owner: string,
    input: z.infer<typeof holdings>,
    portfolioId?: string,
  ) {
    const data = holdings.parse(input);
    return transaction(this.pool, async (c) => {
      let id = portfolioId;
      if (id) {
        if (
          !(
            await c.query(
              "SELECT id FROM portfolios WHERE id=$1 AND owner_id=$2",
              [id, owner],
            )
          ).rowCount
        )
          throw new DomainError("not_found", 404);
      } else {
        id = randomUUID();
        await c.query(
          "INSERT INTO portfolios(id,owner_id,name) VALUES($1,$2,'模拟组合')",
          [id, owner],
        );
      }
      const snapshotId = randomUUID();
      await c.query(
        "INSERT INTO portfolio_snapshots(id,owner_id,portfolio_id,as_of_session,positions,cash_usd) VALUES($1,$2,$3,$4,$5,$6)",
        [
          snapshotId,
          owner,
          id,
          data.asOfSession,
          JSON.stringify(data.positions),
          data.cashUsd,
        ],
      );
      return { portfolioId: id, snapshotId };
    });
  }
  async portfolios(owner: string) {
    return (
      await this.pool.query(
        `SELECT p.id,p.name,s.id AS snapshot_id,s.as_of_session::text,s.positions,s.cash_usd::text FROM portfolios p
   JOIN LATERAL (SELECT * FROM portfolio_snapshots WHERE portfolio_id=p.id AND owner_id=p.owner_id ORDER BY ordinal DESC LIMIT 1) s ON true
   WHERE p.owner_id=$1 ORDER BY p.created_at DESC`,
        [owner],
      )
    ).rows;
  }
  async portfolio(owner: string, id: string, version?: string) {
    const row = (
      await this.pool.query(
        `SELECT id,portfolio_id,as_of_session::text,positions,cash_usd::text FROM portfolio_snapshots
   WHERE owner_id=$1 AND portfolio_id=$2 AND ($3::uuid IS NULL OR id=$3) ORDER BY ordinal DESC LIMIT 1`,
        [owner, id, version ?? null],
      )
    ).rows[0];
    if (!row) throw new DomainError("not_found", 404);
    return row;
  }
  async watchlist(owner: string, add?: string) {
    if (add)
      await this.pool.query(
        "INSERT INTO watchlist(owner_id,symbol) VALUES($1,$2) ON CONFLICT DO NOTHING",
        [owner, symbol.parse(add)],
      );
    return (
      await this.pool.query(
        "SELECT symbol FROM watchlist WHERE owner_id=$1 ORDER BY symbol",
        [owner],
      )
    ).rows;
  }
}
