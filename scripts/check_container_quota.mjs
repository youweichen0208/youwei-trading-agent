// Run on stdin inside the isolated compose.app-local BFF, with a quota of two.
// No real model/provider requests; facts belong to a fresh synthetic test owner.
import assert from "node:assert/strict";
import { randomBytes, randomUUID } from "node:crypto";

let stage = "guard";
let pool;
try {
  assert.equal(process.env.YTA_ENV, "test");
  assert.equal(process.env.YTA_DATA_MODE, "REPLAY");
  assert.equal(process.env.YTA_RESEARCH_RUNS_PER_24H, "2");
  assert.ok(new URL(process.env.YTA_DATABASE_URL).pathname.endsWith("_test"));
  const { database } = await import("./dist/database.js");
  const { hashPassword } = await import("./dist/auth.js");
  const { loginAccountHash } = await import("./dist/login-limiter.js");
  const { databaseStatus } = await import("./dist/readiness.js");
  pool = database(process.env.YTA_DATABASE_URL);
  stage = "database_readiness";
  assert.equal(await databaseStatus(pool), "ready");
  const owner = randomUUID(), email = owner + "@container-quota.test";
  const password = randomBytes(32).toString("base64url");
  stage = "synthetic_owner";
  await pool.query("INSERT INTO yta.users(id,email) VALUES($1,$2)", [owner, email]);
  await pool.query("INSERT INTO yta.login_credentials(owner_id,password_hash) VALUES($1,$2)", [owner, await hashPassword(password)]);
  let cookie, csrf;
  const request = (path, body, key) => fetch("http://127.0.0.1:3100/api/yta/v1" + path, {
    method: body === undefined ? "GET" : "POST",
    headers: {"Content-Type": "application/json", Origin: "http://localhost:3300",
      ...(cookie ? {Cookie: cookie, "X-CSRF-Token": csrf} : {}),
      ...(key ? {"Idempotency-Key": key} : {})},
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(10000), redirect: "error",
  });
  stage = "login";
  const login = await request("/auth/login", {email, password});
  assert.equal(login.status, 200);
  cookie = login.headers.get("set-cookie").split(";")[0];
  csrf = (await login.json()).csrf_token;
  stage = "normalized_stock_paths";
  for (const endpoint of ["bars", "indicators"]) {
    const canonical = await request(`/stocks/NVDA/${endpoint}`);
    const normalized = await request(`/stocks/%20nvDa%20/${endpoint}`);
    assert.equal(canonical.status, 200);
    assert.equal(normalized.status, 200);
    assert.deepEqual(await normalized.json(), await canonical.json());
    for (const symbol of ["AAPL", "NV DA", "ＮＶＤＡ"]) {
      assert.equal((await request(`/stocks/${encodeURIComponent(symbol)}/${endpoint}`)).status, 422);
    }
  }
  stage = "persisted_account_slot";
  const accountSlots = await pool.query("SELECT count(*)::integer AS n FROM yta.login_account_attempts WHERE account_hash=$1", [loginAccountHash(email)]);
  assert.equal(accountSlots.rows[0].n, 1);
  const sessions = [];
  stage = "sessions";
  for (let i = 0; i < 3; i++) {
    const r = await request("/sessions", {profile_id: "alpha-orchestrator"}, randomUUID());
    assert.equal(r.status, 201);
    sessions.push((await r.json()).session_id);
  }
  const keys = sessions.map(() => randomUUID()), runs = [];
  const submit = i => request(`/sessions/${sessions[i]}/messages`, {text: "Container quota REPLAY probe", request_id: keys[i]}, keys[i]);
  try {
    stage = "quota_admission";
    for (let i = 0; i < 2; i++) {
      const r = await submit(i); assert.equal(r.status, 202); runs.push((await r.json()).run_id);
    }
    const refused = await submit(2);
    assert.equal(refused.status, 429);
    const error = (await refused.json()).error;
    assert.equal(error.code, "RESEARCH_QUOTA_EXCEEDED");
    assert.equal(error.retryable, false);
    stage = "idempotent_replay";
    const replay = await submit(0);
    assert.equal(replay.status, 202);
    assert.equal((await replay.json()).run_id, runs[0]);
    const facts = await pool.query("SELECT (SELECT count(*)::integer FROM yta.research_runs WHERE owner_id=$1) AS runs, (SELECT count(*)::integer FROM yta.research_messages m JOIN yta.research_runs r ON r.id=m.run_id WHERE r.owner_id=$1 AND m.role='user') AS messages", [owner]);
    assert.deepEqual(facts.rows[0], {runs: 2, messages: 2});
    stage = "documented_contract";
    const docs = await request("/docs/openapi.json");
    assert.equal(docs.status, 200);
    assert.ok((await docs.json()).paths["/sessions/{id}/messages"].post.responses["429"]);
    console.log(JSON.stringify({data_mode: "REPLAY", authenticated_http: true, quota: 2,
      accepted: 2, rejected_status: 429, same_key_original_run: true, persisted_user_messages: 2,
      packaged_openapi_has_quota: true, persisted_login_account_slots: 1,
      normalized_stock_paths_verified: true,
      database_readiness: "ready", real_provider_requests: 0}));
  } finally {
    for (const run of runs) {
      const result = await request(`/runs/${run}/cancel`, {}, randomUUID());
      assert.equal(result.status, 202);
    }
  }
} catch {
  console.error(JSON.stringify({error: "CONTAINER_QUOTA_CHECK_FAILED", stage}));
  process.exitCode = 1;
} finally { if (pool) await pool.end(); }
