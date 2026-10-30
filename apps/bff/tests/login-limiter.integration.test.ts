import { randomUUID } from "node:crypto";
import { createServer } from "node:http";
import express from "express";
import { Auth, hashPassword } from "../src/auth.js";
import { readConfig } from "../src/config.js";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { database } from "../src/database.js";
import { LoginLimiter, loginPeerHash, loginAccountHash } from "../src/login-limiter.js";

const url = process.env.YTA_TEST_DATABASE_URL;
describe.skipIf(!url)("shared PostgreSQL login budget", () => {
  let first: ReturnType<typeof database>, second: ReturnType<typeof database>;
  beforeAll(() => {
    if (!url || !new URL(url).pathname.endsWith("_test")) throw new Error("Dedicated *_test database required");
    first = database(url); second = database(url);
  });
  afterAll(async () => {await first.end(); await second.end();});

  it("admits exactly ten concurrent checks across pools and survives a new instance", async () => {
    const peer = "test-peer-" + randomUUID();
    const workers = [new LoginLimiter(first), new LoginLimiter(second)];
    const outcomes = await Promise.allSettled(Array.from({length: 24}, (_, i) => workers[i % 2]!.consume(peer, peer + "@test.invalid")));
    expect(outcomes.filter(result => result.status === "fulfilled")).toHaveLength(10);
    for (const result of outcomes.filter(result => result.status === "rejected")) {
      expect(result.reason).toMatchObject({code: "RATE_LIMITED", status: 429});
    }
    const restartedPool = database(url!);
    try { await expect(new LoginLimiter(restartedPool).consume(peer, peer + "@test.invalid")).rejects.toMatchObject({code: "RATE_LIMITED"}); }
    finally { await restartedPool.end(); }
    const rows = await first.query("SELECT peer_hash,attempted_at FROM yta.login_attempts WHERE peer_hash=$1", [loginPeerHash(peer)]);
    expect(rows.rows).toHaveLength(10);
    expect(JSON.stringify(rows.rows)).not.toContain(peer);
    await expect(workers[0]!.consume("different-" + peer, "different-" + peer + "@test.invalid")).resolves.toBeUndefined();
  });

  it("expires slots using database time without mutating history", async () => {
    const peer = "expiry-" + randomUUID(), hash = loginPeerHash(peer);
    await first.query("INSERT INTO yta.login_attempts(peer_hash,attempted_at) SELECT $1,clock_timestamp()-interval '60 seconds' FROM generate_series(1,10)", [hash]);
    const limiter = new LoginLimiter(first);
    for (let i = 0; i < 10; i++) await limiter.consume(peer, peer + "@test.invalid");
    await expect(limiter.consume(peer, peer + "@test.invalid")).rejects.toMatchObject({code: "RATE_LIMITED"});
    const count = await first.query("SELECT count(*)::integer AS n FROM yta.login_attempts WHERE peer_hash=$1", [hash]);
    expect(count.rows[0].n).toBe(20);
    await expect(first.query("UPDATE yta.login_attempts SET peer_hash=peer_hash WHERE peer_hash=$1", [hash])).rejects.toMatchObject({code: "42501"});
    await expect(first.query("DELETE FROM yta.login_attempts WHERE peer_hash=$1", [hash])).rejects.toMatchObject({code: "42501"});
  });

  it("shares successful and failed login slots and ignores spoofed forwarding headers", async () => {
    const peer = "http-peer-" + randomUUID(), owner = randomUUID();
    const email = owner + "@limit.test", password = "test-only-password";
    await first.query("INSERT INTO yta.users(id,email) VALUES ($1,$2)", [owner, email]);
    await first.query("INSERT INTO yta.login_credentials(owner_id,password_hash) VALUES ($1,$2)",
      [owner, await hashPassword(password)]);
    const config = readConfig({YTA_DATABASE_URL: url!, YTA_ALPHA_TOKEN: "limiter-test-token-not-a-live-secret"});
    const servers = [first, second].map(pool => {
      const auth = new Auth(pool, config), app = express();
      app.use(express.json());
      app.post("/login", (req, res) => {
        // Both servers receive the same synthetic direct peer; forwarded headers remain untrusted.
        Object.defineProperty(req, "ip", {value: peer});
        void auth.login(req, res, req.body).then(result => res.json(result))
          .catch(error => res.status(error.status ?? 500).json({code: error.code ?? "INTERNAL_ERROR"}));
      });
      return createServer(app);
    });
    const origins: string[] = [];
    try {
      for (const server of servers) {
        await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
        const address = server.address();
        if (!address || typeof address === "string") throw new Error("Missing test address");
        origins.push("http://127.0.0.1:" + address.port);
      }
      for (let i = 0; i < 12; i++) {
        const response = await fetch(origins[i % 2] + "/login", {method: "POST",
          headers: {Origin: "http://localhost:3000", "Content-Type": "application/json",
            "X-Forwarded-For": "192.0.2." + i, "X-Real-IP": "192.0.2." + i},
          body: JSON.stringify({email, password: i === 0 ? password : "wrong-password"})});
        expect(response.status).toBe(i === 0 ? 200 : i < 10 ? 401 : 429);
        await response.json();
      }
      const count = await first.query("SELECT count(*)::integer AS n FROM yta.login_attempts WHERE peer_hash=$1", [loginPeerHash(peer)]);
      expect(count.rows[0].n).toBe(10);
    } finally {
      for (const server of servers) {server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve()));}
    }
  });

  it("cannot fall back to an in-memory allowance when the database is unavailable", async () => {
    const closed = database(url!); await closed.end();
    await expect(new LoginLimiter(closed).consume("failure-" + randomUUID(), "closed@test.invalid")).rejects.toThrow();
  });

  it("shares account slots across distinct peers, pools, letter case and restarts", async () => {
    const email = randomUUID() + "@account.test";
    const peers = Array.from({length: 24}, () => "peer-" + randomUUID());
    const workers = [new LoginLimiter(first), new LoginLimiter(second)];
    const results = await Promise.allSettled(peers.map((peer, i) => workers[i % 2]!.consume(peer, i % 2 ? email.toUpperCase() : email)));
    expect(results.filter(r => r.status === "fulfilled")).toHaveLength(10);
    for (const r of results) if (r.status === "rejected") expect(r.reason).toMatchObject({code: "RATE_LIMITED", status: 429});
    const accountRows = await first.query("SELECT account_hash FROM yta.login_account_attempts WHERE account_hash=$1", [loginAccountHash(email)]);
    expect(accountRows.rows).toHaveLength(10);
    expect(JSON.stringify(accountRows.rows)).not.toContain(email);
    const peerRows = await first.query("SELECT count(*)::integer AS n FROM yta.login_attempts WHERE peer_hash=ANY($1::text[])", [peers.map(loginPeerHash)]);
    expect(peerRows.rows[0].n).toBe(10); // Refused accounts roll back the peer slot too.
    const restarted = database(url!);
    try { await expect(new LoginLimiter(restarted).consume("new-" + randomUUID(), email)).rejects.toMatchObject({code: "RATE_LIMITED"}); }
    finally { await restarted.end(); }
  });

  it("expires account slots by database time and prevents mutation with runtime credentials", async () => {
    const email = randomUUID() + "@expired.test", hash = loginAccountHash(email);
    await first.query("INSERT INTO yta.login_account_attempts(account_hash,attempted_at) SELECT $1,clock_timestamp()-interval '61 seconds' FROM generate_series(1,10)", [hash]);
    for (let i = 0; i < 10; i++) await new LoginLimiter(first).consume("fresh-" + randomUUID(), email);
    await expect(new LoginLimiter(second).consume("full-" + randomUUID(), email)).rejects.toMatchObject({code: "RATE_LIMITED"});
    expect((await first.query("SELECT count(*)::integer AS n FROM yta.login_account_attempts WHERE account_hash=$1", [hash])).rows[0].n).toBe(20);
    for (const sql of ["UPDATE yta.login_account_attempts SET account_hash=account_hash WHERE account_hash=$1", "DELETE FROM yta.login_account_attempts WHERE account_hash=$1"]) {
      await expect(first.query(sql, [hash])).rejects.toMatchObject({code: "42501"});
    }
  });

  it("does not charge an account when its peer is already full", async () => {
    const peer = "full-peer-" + randomUUID(), email = randomUUID() + "@untouched.test";
    await first.query("INSERT INTO yta.login_attempts(peer_hash) SELECT $1 FROM generate_series(1,10)", [loginPeerHash(peer)]);
    await expect(new LoginLimiter(first).consume(peer, email)).rejects.toMatchObject({code: "RATE_LIMITED"});
    expect((await first.query("SELECT count(*)::integer AS n FROM yta.login_account_attempts WHERE account_hash=$1", [loginAccountHash(email)])).rows[0].n).toBe(0);
    await expect(new LoginLimiter(second).consume("new-" + peer, email)).resolves.toBeUndefined();
  });

  it("applies the same HTTP account window to real and unknown accounts despite rotating peers", async () => {
    const owner = randomUUID(), email = owner + "@http-account.test", absent = randomUUID() + "@http-account.test";
    await first.query("INSERT INTO yta.users(id,email) VALUES ($1,$2)", [owner, email]);
    await first.query("INSERT INTO yta.login_credentials(owner_id,password_hash) VALUES ($1,$2)", [owner, await hashPassword("correct-test-password")]);
    const auth = new Auth(first, readConfig({YTA_DATABASE_URL: url!, YTA_ALPHA_TOKEN: "account-limit-test-token-not-live-123456"}));
    const app = express(); app.use(express.json());
    app.post("/login", (req, res) => {
      Object.defineProperty(req, "ip", {value: "rotating-test-peer-" + randomUUID()});
      void auth.login(req, res, req.body).then(value => res.json(value))
        .catch(error => res.status(error.status ?? 500).json({code: error.code ?? "INTERNAL_ERROR"}));
    });
    const server = createServer(app);
    try {
      await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
      const address = server.address();
      if (!address || typeof address === "string") throw new Error("Missing test address");
      for (const target of [email, absent]) for (let i = 0; i < 11; i++) {
        const response = await fetch(`http://127.0.0.1:${address.port}/login`, {method: "POST",
          headers: {Origin: "http://localhost:3000", "Content-Type": "application/json"},
          body: JSON.stringify({email: i % 2 ? target.toUpperCase() : target, password: "wrong-test-password"})});
        expect(response.status).toBe(i < 10 ? 401 : 429);
        expect(await response.json()).toEqual({code: i < 10 ? "INVALID_CREDENTIALS" : "RATE_LIMITED"});
        expect(response.headers.get("set-cookie")).toBeNull();
      }
      expect((await first.query("SELECT count(*)::integer AS n FROM yta.login_sessions WHERE owner_id=$1", [owner])).rows[0].n).toBe(0);
    } finally {
      server.closeAllConnections();
      await new Promise<void>(resolve => server.close(() => resolve()));
    }
  });
});

it("normalizes IPv4 mapped peers without accepting forwarded input", () => {
  expect(loginPeerHash("::ffff:127.0.0.1")).toBe(loginPeerHash("127.0.0.1"));
  expect(loginPeerHash("127.0.0.2")).not.toBe(loginPeerHash("127.0.0.1"));
  expect(loginAccountHash("USER@example.test")).toBe(loginAccountHash("user@example.test"));
  expect(loginAccountHash("user@example.test")).not.toBe(loginPeerHash("user@example.test"));
});
