import { database } from "../packages/server/src/db.js";
import { upgradeRuntimeAccess } from "../packages/server/src/upgrade-access.js";
const [web, worker, tools, feishu, ...extra] = process.argv.slice(2);
if (!web || !worker || !tools || !feishu || extra.length)
  throw new Error(
    "usage: upgrade-multiuser-access WEB_ROLE WORKER_ROLE TOOLS_ROLE FEISHU_ROLE",
  );
const pool = database();
try {
  await upgradeRuntimeAccess(pool, { web, worker, tools, feishu });
  console.info("multiuser_permissions_upgraded");
} finally {
  await pool.end();
}
