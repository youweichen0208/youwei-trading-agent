import { grantFeishuIdentityAccess } from "../../packages/server/src/feishu-access.js";
import { readFile, writeFile } from "node:fs/promises";
import { randomBytes } from "node:crypto";
import { Pool } from "pg";
import { migrate } from "../../packages/server/src/migrate.js";
import { transaction } from "../../packages/server/src/db.js";
import { TaskStore } from "../../packages/server/src/tasks.js";
const root = "/opt/hermes-alpha-lab/private";
const config = JSON.parse(
  await readFile(`${root}/feishu-bootstrap.json`, "utf8"),
);
const admin = new Pool({ connectionString: process.env.DATABASE_URL });
await migrate(admin);
const owner = (
  await admin.query("SELECT id FROM users WHERE email=$1", [
    "youweichen0208@gmail.com",
  ])
).rows[0]?.id;
if (!owner) throw Error("owner_not_found");
const name = "alpha_live_feishu";
const password = randomBytes(32).toString("hex");
const url = new URL(process.env.DATABASE_URL!);
url.username = name;
url.password = password;
await writeFile(
  `${root}/feishu.env`,
  Object.entries({
    DATABASE_URL: url.toString(),
    FEISHU_APP_ID: config.appId,
    FEISHU_APP_SECRET: config.appSecret,
    FEISHU_TENANT_KEY: config.tenantKey,
    FEISHU_OWNER_OPEN_ID: config.ownerOpenId,
    FEISHU_OWNER_ID: owner,
    FEISHU_GROUP_CHAT_IDS: "",
    APP_ORIGIN: "https://trading.youwei-agent.com",
  })
    .map(([k, v]) => `${k}=${v}`)
    .join("\n") + "\n",
  { mode: 0o600, flag: "wx" },
);
await transaction(admin, async (c) => {
  await c.query(
    `CREATE ROLE ${name} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 16 PASSWORD '${password}'`,
  );
  await c.query(`GRANT CONNECT ON DATABASE alpha_production TO ${name}`);
  await c.query(`GRANT USAGE ON SCHEMA public TO ${name}`);
  await c.query(`ALTER ROLE ${name} SET search_path=pg_catalog,public,pg_temp`);
  await c.query(
    `GRANT EXECUTE ON FUNCTION public.lock_task_owner(uuid) TO ${name}`,
  );
  await c.query(`GRANT USAGE ON SEQUENCE public.tasks_ordinal_seq TO ${name}`);
  await c.query(
    `GRANT SELECT,INSERT ON public.threads,public.workspaces,public.tasks,public.messages,public.task_events TO ${name}`,
  );
  await c.query(`GRANT UPDATE(title) ON public.threads TO ${name}`);
  await c.query(
    `GRANT UPDATE(version,revision,snapshot) ON public.workspaces TO ${name}`,
  );
  await c.query(
    `GRANT UPDATE(status,cancel_requested_at,cancel_reason,sequence) ON public.tasks TO ${name}`,
  );
  await c.query(
    `GRANT SELECT(task_id,status),UPDATE(status) ON public.tool_operations TO ${name}`,
  );
  await c.query(
    `GRANT SELECT,INSERT,UPDATE ON public.feishu_chats,public.feishu_inbox,public.feishu_outbox TO ${name}`,
  );
});
await admin.query(
  `GRANT SELECT ON public.task_artifacts,public.datasets,public.results TO ${name}`,
);
await grantFeishuIdentityAccess(admin, name);
const runtime = new Pool({ connectionString: url.toString() });
const rollback = new Error("verification_rollback");
try {
  await transaction(runtime, async (c) => {
    const tasks = new TaskStore(runtime, c);
    const thread = await tasks.createThread(owner);
    const task = await tasks.submit(
      owner,
      thread.id,
      "feishu:permission-check",
      { input: "permission check" },
    );
    await tasks.stop(owner, task.id);
    throw rollback;
  });
} catch (e) {
  if (e !== rollback) throw e;
}
let denied = false;
try {
  await runtime.query("SELECT password_hash FROM users LIMIT 1");
} catch (e: any) {
  denied = e.code === "42501";
}
if (!denied) throw Error("unexpected_user_access");
await runtime.end();
await admin.end();
console.info("feishu_permissions_verified");
