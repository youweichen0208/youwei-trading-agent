import { database } from "../packages/server/src/db.js";
import { migrate } from "../packages/server/src/migrate.js";
const pool = database();
try {
  await migrate(pool);
  console.log("Migrations applied");
} finally {
  await pool.end();
}
