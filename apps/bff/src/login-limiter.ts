import { createHash } from "node:crypto";
import type { Pool } from "pg";
import { lock, transaction } from "./database.js";
import { ApiError } from "./errors.js";

export function loginPeerHash(peer: string): string {
  // Node may represent the same IPv4 peer as an IPv4-mapped IPv6 address.
  const normalized = peer.toLowerCase().replace(/^::ffff:(?=\d+\.\d+\.\d+\.\d+$)/, "");
  return createHash("sha256").update("login-peer-v1:" + normalized).digest("hex");
}

export function loginAccountHash(email: string): string {
  return createHash("sha256").update("login-account-v1:" + email.toLowerCase()).digest("hex");
}

export class LoginLimiter {
  constructor(private pool: Pool) {}
  async consume(peer: string, email: string): Promise<void> {
    const hash = loginPeerHash(peer);
    const accountHash = loginAccountHash(email);
    await transaction(this.pool, async connection => {
      await connection.query("SET LOCAL statement_timeout = '3s'");
      await connection.query("SET LOCAL lock_timeout = '2s'");
      for (const key of ["login-limit:" + hash, "login-account-limit:" + accountHash].sort()) {
        await lock(connection, key);
      }
      // Sample DB time after both locks. Both accepted slots commit or roll back together.
      const result = await connection.query(
        "WITH moment AS MATERIALIZED (SELECT clock_timestamp() AS at) " +
        "INSERT INTO yta.login_attempts(peer_hash,attempted_at) " +
        "SELECT $1,at FROM moment WHERE (SELECT count(*) FROM yta.login_attempts " +
        "WHERE peer_hash=$1 AND attempted_at > moment.at - interval '60 seconds') < 10 " +
        "RETURNING id", [hash]);
      if (!result.rowCount) throw new ApiError("RATE_LIMITED", "Too many login attempts", 429, true);
      const account = await connection.query(
        "WITH moment AS MATERIALIZED (SELECT clock_timestamp() AS at) " +
        "INSERT INTO yta.login_account_attempts(account_hash,attempted_at) " +
        "SELECT $1,at FROM moment WHERE (SELECT count(*) FROM yta.login_account_attempts " +
        "WHERE account_hash=$1 AND attempted_at > moment.at - interval '60 seconds') < 10 " +
        "RETURNING id", [accountHash]);
      if (!account.rowCount) throw new ApiError("RATE_LIMITED", "Too many login attempts", 429, true);
    });
  }
}
