import { randomUUID } from "node:crypto";
import type { Pool } from "pg";
import { transaction } from "./db.js";
import { DomainError } from "./tasks.js";
export class ExecutionContextStore {
  constructor(readonly pool: Pool) {}
  async group(owner: string, appId: string, chatId: string) {
    if (![appId, chatId].every((value) => /^[A-Za-z0-9_-]{1,200}$/.test(value)))
      throw new DomainError("invalid_group_context", 400);
    return transaction(this.pool, async (c) => {
      const context = (
        await c.query(
          `INSERT INTO execution_contexts(id,owner_id,kind,app_id,chat_id)
        VALUES($1,$2,'group',$3,$4) ON CONFLICT(owner_id,app_id,chat_id) WHERE kind='group'
        DO UPDATE SET chat_id=excluded.chat_id RETURNING id,owner_id,kind,app_id,chat_id`,
          [randomUUID(), owner, appId, chatId],
        )
      ).rows[0];
      for (const role of ["research", "prediction", "portfolio"])
        await c.query(
          "INSERT INTO profile_instances(id,owner_id,context_id,role) VALUES($1,$2,$3,$4) ON CONFLICT(context_id,role) DO NOTHING",
          [randomUUID(), owner, context.id, role],
        );
      await c.query(
        "INSERT INTO profile_preparation_jobs(owner_id) VALUES($1) ON CONFLICT DO NOTHING",
        [context.id],
      );
      return context;
    });
  }

  async thread(owner: string, threadId: string) {
    const row = (
      await this.pool.query(
        `SELECT c.* FROM thread_contexts t JOIN execution_contexts c ON c.id=t.context_id AND c.owner_id=t.owner_id
      WHERE t.thread_id=$1 AND t.owner_id=$2`,
        [threadId, owner],
      )
    ).rows[0];
    if (!row) throw new DomainError("not_found", 404);
    return row;
  }
}
