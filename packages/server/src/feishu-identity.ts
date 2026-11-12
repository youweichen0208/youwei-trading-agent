import { randomUUID } from "node:crypto";
import type { Pool } from "pg";
import { transaction } from "./db.js";
import { DomainError } from "./tasks.js";

/** Trusted server boundary: identities must come from verified Feishu events/OAuth.
 * Never expose resolve or bind directly with browser-provided identity strings.
 */
export class FeishuIdentityStore {
  constructor(readonly pool: Pool) {}

  async resolve(
    appId: string,
    openId: string,
    options: {
      allowRegistration: boolean;
      limit?: number;
      displayName?: string;
    },
  ) {
    this.validate(appId, openId);
    const limit = options.limit ?? 10;
    if (!Number.isSafeInteger(limit) || limit < 1)
      throw new DomainError("invalid_admission_limit", 400);
    return transaction(this.pool, async (c) => {
      // Serialize admission and explicit binding across all identities of this app.
      await c.query("SELECT pg_advisory_xact_lock(hashtext($1))", [
        `feishu-admission:${appId}`,
      ]);
      const existing = (
        await c.query(
          "SELECT owner_id FROM feishu_identities WHERE app_id=$1 AND open_id=$2",
          [appId, openId],
        )
      ).rows[0];
      if (existing) return { id: existing.owner_id as string, created: false };
      if (!options.allowRegistration)
        throw new DomainError("feishu_admission_closed", 403);
      const count = (
        await c.query(
          "SELECT count(*)::integer AS n FROM feishu_identities WHERE app_id=$1",
          [appId],
        )
      ).rows[0].n;
      if (count >= limit) throw new DomainError("feishu_trial_full", 403);
      const id = randomUUID();
      await c.query("INSERT INTO users(id,display_name) VALUES($1,$2)", [
        id,
        options.displayName?.trim().slice(0, 100) || "飞书用户",
      ]);
      await c.query(
        "INSERT INTO feishu_identities(app_id,open_id,owner_id) VALUES($1,$2,$3)",
        [appId, openId, id],
      );
      for (const role of ["research", "prediction", "portfolio"])
        await c.query(
          "INSERT INTO profile_instances(id,owner_id,role) VALUES($1,$2,$3)",
          [randomUUID(), id, role],
        );
      await c.query(
        "INSERT INTO profile_preparation_jobs(owner_id) VALUES($1)",
        [id],
      );
      return { id, created: true };
    });
  }

  /** ownerId must be the authenticated account or an explicitly configured legacy owner. */
  async bind(appId: string, openId: string, ownerId: string) {
    this.validate(appId, openId);
    return transaction(this.pool, async (c) => {
      await c.query("SELECT pg_advisory_xact_lock(hashtext($1))", [
        `feishu-admission:${appId}`,
      ]);
      const rows = (
        await c.query(
          "SELECT open_id,owner_id FROM feishu_identities WHERE app_id=$1 AND (open_id=$2 OR owner_id=$3)",
          [appId, openId, ownerId],
        )
      ).rows;
      if (
        rows.some((row) => row.open_id !== openId || row.owner_id !== ownerId)
      )
        throw new DomainError("feishu_identity_already_bound", 409);
      await c.query(
        "INSERT INTO feishu_identities(app_id,open_id,owner_id) VALUES($1,$2,$3) ON CONFLICT DO NOTHING",
        [appId, openId, ownerId],
      );
      return { id: ownerId };
    });
  }

  private validate(appId: string, openId: string) {
    if (![appId, openId].every((value) => /^[A-Za-z0-9_-]{1,200}$/.test(value)))
      throw new DomainError("invalid_feishu_identity", 400);
  }
}
