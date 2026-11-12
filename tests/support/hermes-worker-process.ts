// Opt-in integration subprocess. Inject only test runtime bindings through stdin;
// run the production scan/maintenance/lease loop without a fictitious image report.
import { createInterface } from "node:readline";
import { Pool } from "pg";
import { HermesClient } from "../../packages/hermes-adapter/src/client.js";
import { TaskStore } from "../../packages/server/src/tasks.js";
import { ToolCapabilities } from "../../packages/server/src/tool-context.js";
import { TaskWorker } from "../../packages/server/src/worker.js";
import { runWorkerService } from "../../packages/server/src/worker-service.js";

const url = process.env.TEST_DATABASE_URL;
if (!url || !new URL(url).pathname.endsWith("_test"))
  throw new Error("Dedicated test database required");
const input = createInterface({ input: process.stdin });
const message = await input[Symbol.asyncIterator]().next();
input.close();
if (message.done) throw new Error("Test runtime bindings required");
const config: {
  schema: string;
  key: string;
  bindings: {
    profileId: string;
    origin: string;
    token: string;
    image?: string;
  }[];
} = JSON.parse(message.value);
if (!/^test_[a-f0-9]{32}$/.test(config.schema))
  throw new Error("Dedicated test schema required");
const pool = new Pool({
  connectionString: url,
  options: `-c search_path=${config.schema}`,
});
const runtimes = new Map(
  config.bindings.map((binding) => {
    if (new URL(binding.origin).hostname !== "127.0.0.1")
      throw new Error("Local test peer required");
    return [
      binding.profileId,
      new HermesClient(
        binding.origin,
        binding.token,
        binding.image ?? "test-pinned-source-process-not-an-image",
        3_600_000,
      ),
    ] as const;
  }),
);
const worker = new TaskWorker(
  new TaskStore(pool),
  (id) => runtimes.get(id),
  new ToolCapabilities(pool, config.key),
);
const closing = new AbortController();
process.once("SIGTERM", () => closing.abort());
process.once("SIGINT", () => closing.abort());
try {
  await runWorkerService(worker, closing.signal);
} finally {
  await pool.end();
}
