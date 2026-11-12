import { createHash, randomBytes, randomUUID } from "node:crypto";
import type { Pool } from "pg";
import { AuthStore } from "./auth.js";
import { transaction } from "./db.js";
import { DomainError } from "./tasks.js";
const hash = (value: string) =>
  createHash("sha256").update(value).digest("hex");

/** Only verified bot events may call confirm; commands never enter research history. */
export class FeishuLoginStore {
  constructor(readonly pool: Pool) {}
  async create(bindingOwner?: string) {
    await new AuthStore(this.pool).rateLimit("feishu-login-create", 100);
    const id = randomUUID(),
      browser = randomBytes(32).toString("hex"),
      code = randomBytes(12).toString("hex").toUpperCase();
    await this.pool.query(
      "INSERT INTO feishu_login_challenges(id,code_hash,browser_hash,binding_owner_id) VALUES($1,$2,$3,$4)",
      [id, hash(code), hash(browser), bindingOwner ?? null],
    );
    return {
      id,
      browser,
      command: `/login ${bindingOwner ? "bind" : "confirm"} ${code}`,
      expiresIn: 300,
    };
  }
  async confirm(
    command: string,
    chatType: string,
    appId: string,
    openId: string,
  ) {
    if (!command.startsWith("/login")) return false;
    if (chatType !== "p2p")
      throw new DomainError("login_private_chat_required", 403);
    await new AuthStore(this.pool).rateLimit(
      `feishu-login-confirm:${appId}:${openId}`,
      10,
    );
    const match = /^\/login (confirm|bind) ([A-F0-9]{24})$/.exec(command);
    if (!match) throw new DomainError("login_confirmation_required", 400);
    if (match[1] === "bind") {
      if (![appId, openId].every((v) => /^[A-Za-z0-9_-]{1,200}$/.test(v)))
        throw new DomainError("invalid_feishu_identity", 400);
      const binding = await this.pool.query(
        `UPDATE feishu_login_challenges SET confirmed_app_id=$2,confirmed_open_id=$3,confirmed_at=now()
        WHERE code_hash=$1 AND binding_owner_id IS NOT NULL AND expires_at>now() AND confirmed_at IS NULL AND consumed_at IS NULL RETURNING id`,
        [hash(match[2]!), appId, openId],
      );
      if (!binding.rowCount)
        throw new DomainError("login_challenge_invalid", 400);
      return true;
    }
    const result = await this.pool.query(
      `UPDATE feishu_login_challenges SET owner_id=i.owner_id,confirmed_at=now()
      FROM feishu_identities i WHERE binding_owner_id IS NULL AND code_hash=$1 AND i.app_id=$2 AND i.open_id=$3
      AND expires_at>now() AND confirmed_at IS NULL AND consumed_at IS NULL RETURNING id`,
      [hash(match[2]!), appId, openId],
    );
    if (!result.rowCount) throw new DomainError("login_challenge_invalid", 400);
    return true;
  }
  async status(id: string, browser: string) {
    const row = (
      await this.pool.query(
        "SELECT confirmed_at FROM feishu_login_challenges WHERE id=$1 AND browser_hash=$2 AND expires_at>now() AND consumed_at IS NULL",
        [id, hash(browser)],
      )
    ).rows[0];
    if (!row) throw new DomainError("login_challenge_invalid", 400);
    return { confirmed: !!row.confirmed_at };
  }
  async complete(id: string, browser: string, authenticatedOwner?: string) {
    return transaction(this.pool, async (c) => {
      const row = (
        await c.query(
          `UPDATE feishu_login_challenges SET consumed_at=now() WHERE id=$1 AND browser_hash=$2
        AND expires_at>now() AND confirmed_at IS NOT NULL AND consumed_at IS NULL RETURNING owner_id,binding_owner_id,confirmed_app_id,confirmed_open_id`,
          [id, hash(browser)],
        )
      ).rows[0];
      if (!row) throw new DomainError("login_challenge_invalid", 400);
      if (row.binding_owner_id) {
        if (row.binding_owner_id !== authenticatedOwner)
          throw new DomainError("binding_session_required", 403);
        await c.query("SELECT pg_advisory_xact_lock(hashtext($1))", [
          `feishu-admission:${row.confirmed_app_id}`,
        ]);
        const conflicts = (
          await c.query(
            "SELECT owner_id,open_id FROM feishu_identities WHERE app_id=$1 AND (open_id=$2 OR owner_id=$3)",
            [row.confirmed_app_id, row.confirmed_open_id, authenticatedOwner],
          )
        ).rows;
        if (
          conflicts.some(
            (r) =>
              r.owner_id !== authenticatedOwner ||
              r.open_id !== row.confirmed_open_id,
          )
        )
          throw new DomainError("feishu_identity_already_bound", 409);
        await c.query(
          "INSERT INTO feishu_identities(app_id,open_id,owner_id) VALUES($1,$2,$3) ON CONFLICT DO NOTHING",
          [row.confirmed_app_id, row.confirmed_open_id, authenticatedOwner],
        );
        row.owner_id = authenticatedOwner;
      }
      const token = randomBytes(32).toString("hex");
      await c.query(
        "INSERT INTO login_sessions(token_hash,owner_id,expires_at) VALUES($1,$2,now()+interval '7 days')",
        [hash(token), row.owner_id],
      );
      return token;
    });
  }
}
