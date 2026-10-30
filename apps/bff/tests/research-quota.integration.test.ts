import { randomBytes, randomUUID } from "node:crypto";
import { setTimeout as delay } from "node:timers/promises";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { database } from "../src/database.js";
import { ResearchRepository, type EventBody } from "../src/research-store.js";
import { readConfig } from "../src/config.js";
import { createBff } from "../src/app.js";
import { digest } from "../src/auth.js";

const url = process.env.YTA_TEST_DATABASE_URL;
describe.skipIf(!url)("persistent research admission quota", () => {
  let first: ReturnType<typeof database>, second: ReturnType<typeof database>;
  const owners: string[] = [];
  beforeAll(() => {
    if (!url || !new URL(url).pathname.endsWith("_test")) throw new Error("Dedicated *_test database required");
    first = database(url); second = database(url);
  });
  afterAll(async () => {
    try {
      // Append terminal cleanup only for this suite's synthetic admissions; retain facts.
      for (const mode of ["REPLAY", "LIVE_RESEARCH"] as const) {
        const repo = new ResearchRepository(first, mode);
        const rows = await first.query("SELECT r.id,r.owner_id FROM yta.research_runs r JOIN yta.research_sessions s ON s.id=r.session_id WHERE r.owner_id=ANY($1::uuid[]) AND s.data_mode=$2", [owners, mode]);
        for (const row of rows.rows) await repo.append(await repo.getRun(row.owner_id, row.id), {type: "run.canceled", payload: {}});
      }
    } finally { await first.end(); await second.end(); }
  });

  async function owner() {
    const id = randomUUID();
    await first.query("INSERT INTO yta.users(id,email) VALUES ($1,$2)", [id, id + "@quota.test"]);
    owners.push(id);
    return id;
  }
  async function session(repo: ResearchRepository, id: string) {
    return (await repo.createSession(id, repo.mode, randomUUID())).session_id;
  }

  it("serializes different sessions across pools, preserves retries, and survives restart", async () => {
    const id = await owner();
    const repos = [new ResearchRepository(first, "REPLAY", 3), new ResearchRepository(second, "REPLAY", 3)];
    const submissions = await Promise.all(Array.from({length: 12}, async (_, i) => ({
      repo: repos[i % 2]!, session: await session(repos[i % 2]!, id), key: randomUUID(),
    })));
    const results = await Promise.allSettled(submissions.map(s => s.repo.submit(id, s.session, s.key, "quota probe", "REPLAY")));
    expect(results.filter(r => r.status === "fulfilled")).toHaveLength(3);
    for (const result of results) if (result.status === "rejected") {
      expect(result.reason).toMatchObject({code: "RESEARCH_QUOTA_EXCEEDED", status: 429, retryable: false});
    }
    const accepted = results.findIndex(r => r.status === "fulfilled");
    const identity = submissions[accepted]!;
    const original = results[accepted]!;
    if (original.status !== "fulfilled") throw new Error("Expected accepted run");
    const fresh = database(url!);
    try {
      const restarted = new ResearchRepository(fresh, "REPLAY", 3);
      await expect(restarted.submit(id, identity.session, identity.key, "quota probe", "REPLAY"))
        .resolves.toEqual({run_id: original.value.run_id, created: false});
      await expect(restarted.submit(id, identity.session, identity.key, "changed", "REPLAY"))
        .rejects.toMatchObject({code: "IDEMPOTENCY_CONFLICT"});
      await expect(restarted.submit(id, await session(restarted, id), randomUUID(), "new", "REPLAY"))
        .rejects.toMatchObject({code: "RESEARCH_QUOTA_EXCEEDED"});
    } finally { await fresh.end(); }
    const counts = await first.query("SELECT (SELECT count(*)::integer FROM yta.research_runs WHERE owner_id=$1) AS runs, (SELECT count(*)::integer FROM yta.research_messages m JOIN yta.research_runs r ON r.id=m.run_id WHERE r.owner_id=$1) AS messages", [id]);
    expect(counts.rows[0]).toEqual({runs: 3, messages: 3});
  });

  it("does not refund failed, cancelled, or unknown runs and isolates owners and modes", async () => {
    const id = await owner(), other = await owner();
    const repo = new ResearchRepository(first, "REPLAY", 3);
    const terminal: EventBody[] = [
      {type: "run.canceled", payload: {}},
      {type: "run.failed", payload: {code: "PROVIDER_UNAVAILABLE", message: "Unavailable", request_id: randomUUID(), retryable: false}},
      {type: "run.execution_unknown", payload: {code: "EXECUTION_UNKNOWN", message: "Unknown", request_id: randomUUID(), retryable: false}},
    ];
    for (const event of terminal) {
      const admitted = await repo.submit(id, await session(repo, id), randomUUID(), "terminal probe", "REPLAY");
      await repo.append(await repo.getRun(id, admitted.run_id), event);
    }
    await expect(repo.submit(id, await session(repo, id), randomUUID(), "new", "REPLAY"))
      .rejects.toMatchObject({code: "RESEARCH_QUOTA_EXCEEDED"});
    await expect(repo.submit(other, await session(repo, other), randomUUID(), "other", "REPLAY"))
      .resolves.toMatchObject({created: true});
    const live = new ResearchRepository(second, "LIVE_RESEARCH", 3);
    await expect(live.submit(id, await session(live, id), randomUUID(), "mode isolation", "LIVE_RESEARCH"))
      .resolves.toMatchObject({created: true});
  });

  it("expires admissions by database time without deleting history or charging rejected ownership", async () => {
    const id = await owner(), other = await owner();
    const repo = new ResearchRepository(first, "REPLAY", 1);
    const old = await session(repo, id);
    await first.query("INSERT INTO yta.research_runs(id,session_id,owner_id,request_id,request_hash,prompt,created_at) VALUES ($1,$2,$3,$4,$5,'old admission',statement_timestamp()-interval '25 hours')",
      [randomUUID(), old, id, randomUUID(), "a".repeat(64)]);
    await expect(repo.submit(id, await session(repo, other), randomUUID(), "wrong owner", "REPLAY"))
      .rejects.toMatchObject({code: "NOT_FOUND"});
    const current = await session(repo, id);
    await expect(repo.submit(id, current, randomUUID(), "allowed", "REPLAY")).resolves.toMatchObject({created: true});
    await expect(repo.submit(id, await session(repo, id), randomUUID(), "full", "REPLAY"))
      .rejects.toMatchObject({code: "RESEARCH_QUOTA_EXCEEDED"});
    expect((await first.query("SELECT count(*)::integer AS n FROM yta.research_runs WHERE owner_id=$1", [id])).rows[0].n).toBe(2);
  });

  it("returns a diagnostic HTTP 429 before dispatch and still replays the accepted identity", async () => {
    const id = await owner(), token = randomBytes(32).toString("base64url");
    const credential = await first.query("INSERT INTO yta.login_credentials(owner_id,password_hash) VALUES ($1,$2) RETURNING id", [id, "unused-test-credential"]);
    await first.query("INSERT INTO yta.login_sessions(token_hash,owner_id,credential_id,expires_at) VALUES ($1,$2,$3,now()+interval '1 hour')", [digest(token), id, credential.rows[0].id]);
    let calls = 0;
    const config = readConfig({YTA_DATABASE_URL: url!, YTA_ALPHA_TOKEN: "quota-http-token-not-live-123456789", YTA_RESEARCH_RUNS_PER_24H: "1"});
    const server = await createBff({config, pool: first, startRunner: false, log: () => {}, driver: {async execute() { calls++; }}});
    try {
      await server.app.listen(0, "127.0.0.1");
      const origin = await server.app.getUrl();
      const originalSession = await session(server.research, id), secondSession = await session(server.research, id);
      const requestId = randomUUID();
      const send = (sessionId: string, key: string) => fetch(origin + `/api/yta/v1/sessions/${sessionId}/messages`, {
        method: "POST", headers: {"Content-Type": "application/json", Origin: "http://localhost:3000", Cookie: "yta_session=" + token, "X-CSRF-Token": digest("csrf:" + token)},
        body: JSON.stringify({text: "request quota test", request_id: key}),
      });
      const accepted = await send(originalSession, requestId);
      expect(accepted.status).toBe(202);
      const identity = await accepted.json();
      const denied = await send(secondSession, randomUUID());
      expect(denied.status).toBe(429);
      expect(await denied.json()).toMatchObject({error: {code: "RESEARCH_QUOTA_EXCEEDED", retryable: false}});
      const replay = await send(originalSession, requestId);
      expect(replay.status).toBe(202);
      expect(await replay.json()).toEqual(identity);
      for (let i = 0; i < 100 && calls === 0; i++) await delay(10);
      expect(calls).toBe(1);
    } finally { await server.close(); }
  });
});

it("validates configured research quotas without accepting unlimited or fractional values", () => {
  const base = {YTA_DATABASE_URL: "postgresql://unused/unused_test", YTA_ALPHA_TOKEN: "quota-test-service-token-not-live-12345"};
  expect(readConfig(base).YTA_RESEARCH_RUNS_PER_24H).toBe(50);
  for (const invalid of ["0", "-1", "1.5", "10001", "unlimited", ""]) {
    expect(() => readConfig({...base, YTA_RESEARCH_RUNS_PER_24H: invalid})).toThrow("YTA_RESEARCH_RUNS_PER_24H");
  }
});
