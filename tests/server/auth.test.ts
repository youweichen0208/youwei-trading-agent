import { before, after, test } from "node:test";
import assert from "node:assert/strict";
import { randomUUID, createHash } from "node:crypto";
import { Pool } from "pg";
import { migrate } from "../../packages/server/src/migrate.js";
import { AuthStore } from "../../packages/server/src/auth.js";
const url = process.env.TEST_DATABASE_URL;
if (!url || !new URL(url).pathname.endsWith("_test"))
  throw new Error("TEST_DATABASE_URL ending in _test required");
const schema = `test_${randomUUID().replaceAll("-", "")}`;
const admin = new Pool({ connectionString: url });
const pool = new Pool({
  connectionString: url,
  options: `-c search_path=${schema}`,
});
const auth = new AuthStore(pool);
before(async () => {
  await admin.query(`CREATE SCHEMA ${schema}`);
  await migrate(pool);
});
after(async () => {
  await pool.end();
  await admin.query(`DROP SCHEMA ${schema} CASCADE`);
  await admin.end();
});
test("registration provisions distinct profile identities; passwords and sessions are not plaintext", async () => {
  const email = `${randomUUID()}@example.test`,
    password = "a long test password";
  const user = await auth.register(email, password);
  const profiles = (
    await pool.query("SELECT * FROM profile_instances WHERE owner_id=$1", [
      user.id,
    ])
  ).rows;
  assert.equal(profiles.length, 3);
  const job = (
    await pool.query(
      "SELECT * FROM profile_preparation_jobs WHERE owner_id=$1",
      [user.id],
    )
  ).rows[0];
  assert.equal(job.status, "queued");
  assert.equal(job.first_port, null);
  const count = (
    await pool.query("SELECT count(*) FROM profile_preparation_jobs")
  ).rows[0].count;
  await assert.rejects(auth.register(email, password), /account_unavailable/);
  assert.equal(
    (await pool.query("SELECT count(*) FROM profile_preparation_jobs")).rows[0]
      .count,
    count,
  );

  assert.equal(new Set(profiles.map((p) => p.id)).size, 3);
  const hash = (
    await pool.query("SELECT password_hash FROM users WHERE id=$1", [user.id])
  ).rows[0].password_hash;
  assert.notEqual(hash, password);
  assert.match(hash, /^scrypt-v1:/);
  await assert.rejects(
    auth.login(email, "wrong password"),
    /invalid_credentials/,
  );
  const login = await auth.login(email, password);
  assert.equal((await auth.authenticate(login.token)).id, user.id);
  const row = (
    await pool.query(
      "SELECT token_hash FROM login_sessions WHERE owner_id=$1",
      [user.id],
    )
  ).rows[0];
  assert.notEqual(row.token_hash, login.token);
  assert.equal(
    row.token_hash,
    createHash("sha256").update(login.token).digest("hex"),
  );
  await auth.logout(login.token);
  await assert.rejects(auth.authenticate(login.token), /unauthorized/);
});
test("expired sessions and invalid tokens fail closed", async () => {
  const email = `${randomUUID()}@example.test`;
  await auth.register(email, "a long test password");
  const login = await auth.login(email, "a long test password");
  await pool.query(
    "UPDATE login_sessions SET expires_at=now()-interval '1 second' WHERE owner_id=$1",
    [login.user.id],
  );
  await assert.rejects(auth.authenticate(login.token), /unauthorized/);
  await assert.rejects(auth.authenticate(undefined), /unauthorized/);
});
test("rate limiter increments atomically across concurrent requests", async () => {
  const results = await Promise.allSettled(
    Array.from({ length: 15 }, () => auth.rateLimit("same-client", 10)),
  );
  assert.equal(results.filter((r) => r.status === "fulfilled").length, 10);
  assert.equal(results.filter((r) => r.status === "rejected").length, 5);
});

test("a failed preparation enqueue rolls back the new user and all three Profile identities", async () => {
  const email = `${randomUUID()}@atomic.test`;
  const profiles = (await pool.query("SELECT count(*) FROM profile_instances"))
    .rows[0].count;
  await pool.query(`CREATE FUNCTION fail_preparation_enqueue() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'test_enqueue_failure'; END $$;
    CREATE TRIGGER fail_preparation_enqueue BEFORE INSERT ON profile_preparation_jobs FOR EACH ROW EXECUTE FUNCTION fail_preparation_enqueue()`);
  try {
    await assert.rejects(
      auth.register(email, "a long test password"),
      /test_enqueue_failure/,
    );
    assert.equal(
      (await pool.query("SELECT count(*) FROM users WHERE email=$1", [email]))
        .rows[0].count,
      "0",
    );
    assert.equal(
      (await pool.query("SELECT count(*) FROM profile_instances")).rows[0]
        .count,
      profiles,
    );
  } finally {
    await pool.query(
      "DROP TRIGGER fail_preparation_enqueue ON profile_preparation_jobs",
    );
  }
});

test("Feishu concurrent admission is unique, capped and explicit binding preserves owner", async () => {
  const { FeishuIdentityStore } =
    await import("../../packages/server/src/feishu-identity.js");
  const identities = new FeishuIdentityStore(pool);
  const app = `cli_${randomUUID()}`;
  const results = await Promise.all(
    Array.from({ length: 8 }, () =>
      identities.resolve(app, "ou_one", { allowRegistration: true, limit: 1 }),
    ),
  );
  assert.ok(results[0]);
  assert.equal(new Set(results.map((row) => row.id)).size, 1);
  assert.equal(results.filter((row) => row.created).length, 1);
  await assert.rejects(
    identities.resolve(app, "ou_two", { allowRegistration: true, limit: 1 }),
    /feishu_trial_full/,
  );
  assert.equal(
    (await identities.resolve(app, "ou_one", { allowRegistration: false })).id,
    results[0].id,
  );
  await assert.rejects(
    identities.resolve(app, "ou_three", { allowRegistration: false }),
    /feishu_admission_closed/,
  );
  const original = await auth.register(
    `${randomUUID()}@example.test`,
    "legacy password long enough",
  );
  await identities.bind(app, "ou_legacy", original.id);
  await identities.bind(app, "ou_legacy", original.id);
  await assert.rejects(
    identities.bind(app, "ou_one", original.id),
    /feishu_identity_already_bound/,
  );
  assert.equal(
    (await identities.resolve(app, "ou_legacy", { allowRegistration: false }))
      .id,
    original.id,
  );
});

test("Feishu login requires private confirmation and original browser; completion is single use", async () => {
  const { FeishuIdentityStore } =
    await import("../../packages/server/src/feishu-identity.js");
  const { FeishuLoginStore } =
    await import("../../packages/server/src/feishu-login.js");
  const app = `cli_${randomUUID()}`;
  const identity = await new FeishuIdentityStore(pool).resolve(
    app,
    "ou_login",
    { allowRegistration: true },
  );
  const store = new FeishuLoginStore(pool);
  const challenge = await store.create();
  await assert.rejects(
    store.confirm(challenge.command, "group", app, "ou_login"),
    /login_private_chat_required/,
  );
  await assert.rejects(
    store.complete(challenge.id, challenge.browser),
    /login_challenge_invalid/,
  );
  await assert.rejects(
    store.confirm(challenge.command, "p2p", app, "ou_forged"),
    /login_challenge_invalid/,
  );
  await store.confirm(challenge.command, "p2p", app, "ou_login");
  await assert.rejects(
    store.status(challenge.id, "wrong-browser"),
    /login_challenge_invalid/,
  );
  await assert.rejects(
    store.complete(challenge.id, "wrong-browser"),
    /login_challenge_invalid/,
  );
  assert.deepEqual(await store.status(challenge.id, challenge.browser), {
    confirmed: true,
  });
  const session = await store.complete(challenge.id, challenge.browser);
  assert.equal((await auth.authenticate(session)).id, identity.id);
  await assert.rejects(
    store.complete(challenge.id, challenge.browser),
    /login_challenge_invalid/,
  );
  const expired = await store.create();
  await pool.query(
    "UPDATE feishu_login_challenges SET expires_at=now()-interval '1 second' WHERE id=$1",
    [expired.id],
  );
  await assert.rejects(
    store.confirm(expired.command, "p2p", app, "ou_login"),
    /login_challenge_invalid/,
  );
});

test("OAuth binds state to browser, consumes once, sends PKCE and creates normal session", async () => {
  const { FeishuOAuth } =
    await import("../../packages/server/src/feishu-oauth.js");
  let calls = 0;
  let verifier = "";
  const oauth = new FeishuOAuth(
    pool,
    {
      appId: `cli_${randomUUID()}`,
      appSecret: "test-secret",
      origin: "https://example.test",
      allowRegistration: true,
    },
    async (url, init) => {
      calls++;
      if (String(url).endsWith("/token")) {
        const body = JSON.parse(String(init?.body));
        verifier = body.code_verifier;
        assert.equal(body.redirect_uri, oauth.redirectUri);
        return Response.json({ access_token: "test-access-token" });
      }
      return Response.json({
        code: 0,
        data: { open_id: "ou_oauth", name: "测试用户" },
      });
    },
  );
  const started = await oauth.start();
  const url = new URL(started.url),
    state = url.searchParams.get("state")!;
  await assert.rejects(
    oauth.callback(state, "0".repeat(64), "code"),
    /oauth_state_invalid/,
  );
  assert.equal(calls, 0);
  const session = await oauth.callback(state, started.browser, "code");
  assert.ok((await auth.authenticate(session)).id);
  assert.equal(
    url.searchParams.get("code_challenge"),
    createHash("sha256").update(verifier).digest("base64url"),
  );
  await assert.rejects(
    oauth.callback(state, started.browser, "code"),
    /oauth_state_invalid/,
  );
  assert.equal(calls, 2);
});

test("explicit binding requires original logged-in account and refuses identity takeover", async () => {
  const { FeishuLoginStore } =
    await import("../../packages/server/src/feishu-login.js");
  const { FeishuIdentityStore } =
    await import("../../packages/server/src/feishu-identity.js");
  const store = new FeishuLoginStore(pool);
  const owner = await auth.register(
    `${randomUUID()}@example.test`,
    "long binding password",
  );
  const app = `cli_${randomUUID()}`;
  const challenge = await store.create(owner.id);
  await store.confirm(challenge.command, "p2p", app, "ou_binding");
  await assert.rejects(
    store.complete(challenge.id, challenge.browser),
    /binding_session_required/,
  );
  const session = await store.complete(
    challenge.id,
    challenge.browser,
    owner.id,
  );
  assert.equal((await auth.authenticate(session)).id, owner.id);
  const other = await auth.register(
    `${randomUUID()}@example.test`,
    "other binding password",
  );
  const takeover = await store.create(other.id);
  await store.confirm(takeover.command, "p2p", app, "ou_binding");
  await assert.rejects(
    store.complete(takeover.id, takeover.browser, other.id),
    /feishu_identity_already_bound/,
  );
  assert.equal(
    (
      await new FeishuIdentityStore(pool).resolve(app, "ou_binding", {
        allowRegistration: false,
      })
    ).id,
    owner.id,
  );
});

test("bot identity role provisions users and confirms login without reading authentication secrets", async () => {
  const { grantFeishuIdentityAccess } =
    await import("../../packages/server/src/feishu-access.js");
  const { FeishuIdentityStore } =
    await import("../../packages/server/src/feishu-identity.js");
  const { FeishuLoginStore } =
    await import("../../packages/server/src/feishu-login.js");
  const role = `bot_${randomUUID().replaceAll("-", "")}`;
  await admin.query(`CREATE ROLE ${role}`);
  const bot = new Pool({
    connectionString: url,
    options: `-c search_path=${schema} -c role=${role}`,
  });
  try {
    await pool.query(`GRANT USAGE ON SCHEMA ${schema} TO ${role}`);
    await grantFeishuIdentityAccess(pool, role);
    const app = `cli_${randomUUID()}`;
    const identity = await new FeishuIdentityStore(bot).resolve(
      app,
      "ou_restricted",
      { allowRegistration: true },
    );
    const login = new FeishuLoginStore(pool);
    const challenge = await login.create();
    await new FeishuLoginStore(bot).confirm(
      challenge.command,
      "p2p",
      app,
      "ou_restricted",
    );
    assert.equal(
      (
        await auth.authenticate(
          await login.complete(challenge.id, challenge.browser),
        )
      ).id,
      identity.id,
    );
    for (const query of [
      "SELECT password_hash FROM users",
      "SELECT email FROM users",
      "SELECT token_hash FROM login_sessions",
      "SELECT browser_hash FROM feishu_login_challenges",
      "SELECT verifier FROM feishu_oauth_states",
    ])
      await assert.rejects(
        bot.query(query),
        (error: any) => error.code === "42501",
      );
  } finally {
    await bot.end();
    await admin.query(`DROP OWNED BY ${role}`);
    await admin.query(`DROP ROLE ${role}`);
  }
});
