import {
  createHash,
  randomBytes,
  randomUUID,
  scrypt,
  timingSafeEqual,
} from "node:crypto";
import type { Pool } from "pg";
import { transaction } from "./db.js";
import { DomainError } from "./tasks.js";

const options = { N: 32768, r: 8, p: 1, maxmem: 64 * 1024 * 1024 };
const derive = (password: string, salt: Buffer) =>
  new Promise<Buffer>((resolve, reject) => {
    scrypt(password, salt, 64, options, (error, key) =>
      error ? reject(error) : resolve(key),
    );
  });
const tokenHash = (token: string) =>
  createHash("sha256").update(token).digest("hex");
function emailAddress(value: string) {
  const email = value.trim().toLowerCase();
  if (email.length > 254 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email))
    throw new DomainError("invalid_email", 400);
  return email;
}
export async function hashPassword(password: string) {
  if (password.length < 12 || password.length > 256)
    throw new DomainError("password_length", 400);
  const salt = randomBytes(16);
  return `scrypt-v1:${salt.toString("hex")}:${(await derive(password, salt)).toString("hex")}`;
}
async function verifyPassword(password: string, hash: string) {
  if (password.length > 256) return false;
  const [version, salt, key] = hash.split(":");
  if (
    version !== "scrypt-v1" ||
    !salt ||
    !key ||
    !/^[a-f0-9]{32}$/.test(salt) ||
    !/^[a-f0-9]{128}$/.test(key)
  )
    return false;
  return timingSafeEqual(
    await derive(password, Buffer.from(salt, "hex")),
    Buffer.from(key, "hex"),
  );
}
export class AuthStore {
  constructor(readonly pool: Pool) {}
  async register(email: string, password: string) {
    const normalized = emailAddress(email),
      hash = await hashPassword(password);
    return transaction(this.pool, async (c) => {
      const id = randomUUID();
      const added = await c.query(
        "INSERT INTO users(id,email,password_hash) VALUES($1,$2,$3) ON CONFLICT(email) DO NOTHING RETURNING id",
        [id, normalized, hash],
      );
      if (!added.rowCount) throw new DomainError("account_unavailable");
      for (const role of ["research", "prediction", "portfolio"])
        await c.query(
          "INSERT INTO profile_instances(id,owner_id,role) VALUES($1,$2,$3)",
          [randomUUID(), id, role],
        );
      await c.query(
        "INSERT INTO profile_preparation_jobs(owner_id) VALUES($1)",
        [id],
      );
      return { id, email: normalized };
    });
  }
  async rateLimit(bucket: string, limit = 10) {
    const row = (
      await this.pool.query(
        `INSERT INTO auth_attempts(bucket,count,reset_at) VALUES($1,1,now()+interval '15 minutes')
      ON CONFLICT(bucket) DO UPDATE SET count=CASE WHEN auth_attempts.reset_at<now() THEN 1 ELSE auth_attempts.count+1 END,
      reset_at=CASE WHEN auth_attempts.reset_at<now() THEN now()+interval '15 minutes' ELSE auth_attempts.reset_at END RETURNING count`,
        [tokenHash(bucket)],
      )
    ).rows[0];
    if (row.count > limit) throw new DomainError("rate_limited", 429);
  }
  async login(email: string, password: string) {
    const normalized = emailAddress(email);
    await this.rateLimit(`login:${normalized}`);
    const user = (
      await this.pool.query(
        "SELECT id,email,password_hash FROM users WHERE email=$1",
        [normalized],
      )
    ).rows[0];
    // Perform the same expensive KDF for unknown accounts, avoiding a cheap existence oracle.
    const dummy = `scrypt-v1:${"0".repeat(32)}:${"0".repeat(128)}`;
    const valid = await verifyPassword(password, user?.password_hash ?? dummy);
    if (!user || !valid) throw new DomainError("invalid_credentials", 401);
    const token = randomBytes(32).toString("hex");
    await this.pool.query(
      "INSERT INTO login_sessions(token_hash,owner_id,expires_at) VALUES($1,$2,now()+interval '7 days')",
      [tokenHash(token), user.id],
    );
    return {
      token,
      user: { id: user.id as string, email: user.email as string },
    };
  }
  async authenticate(
    token: string | undefined,
  ): Promise<{ id: string; email: string }> {
    if (!token || !/^[a-f0-9]{64}$/.test(token))
      throw new DomainError("unauthorized", 401);
    const row = (
      await this.pool.query(
        `SELECT u.id,u.email FROM login_sessions s JOIN users u ON u.id=s.owner_id
      WHERE s.token_hash=$1 AND s.expires_at>now()`,
        [tokenHash(token)],
      )
    ).rows[0];
    if (!row) throw new DomainError("unauthorized", 401);
    return row;
  }
  async logout(token: string) {
    await this.pool.query("DELETE FROM login_sessions WHERE token_hash=$1", [
      tokenHash(token),
    ]);
  }
}
