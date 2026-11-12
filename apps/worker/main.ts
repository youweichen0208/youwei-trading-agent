import { ResearchScheduler } from "../../packages/server/src/research-scheduler.js";
import { ResearchStore } from "../../packages/server/src/research-store.js";
import { MarketScanner } from "../../packages/server/src/market-scanner.js";
import { ForecastStore } from "../../packages/server/src/forecast-store.js";
import { ExperienceStore } from "../../packages/server/src/experience-store.js";
import { configuredMarketProvider } from "../../packages/market/src/alpaca.js";
import { readPublication } from "../../packages/hermes-adapter/src/publication.js";
import { ToolCapabilities } from "../../packages/server/src/tool-context.js";
import { database } from "../../packages/server/src/db.js";
import { TaskStore } from "../../packages/server/src/tasks.js";
import { TaskWorker } from "../../packages/server/src/worker.js";
import { ReloadingRegistry } from "../../packages/hermes-adapter/src/client.js";
import { runWorkerService } from "../../packages/server/src/worker-service.js";

process.env.RESEARCH_SINGLE_ROOT ??= "1";
let registry = new ReloadingRegistry(
  process.env.HERMES_PROFILE_REGISTRY,
  process.env.HERMES_REGISTRY_CREDENTIALS,
);
async function reload() {
  if (process.env.HERMES_PUBLICATION_POINTER) {
    const publication = await readPublication(
      process.env.HERMES_PUBLICATION_POINTER,
    );
    const candidate = new ReloadingRegistry(
      publication.registry,
      publication.credentials,
    );
    await candidate.reload();
    registry = candidate;
  } else await registry.reload();
}
await reload();
const resolve: import("../../packages/hermes-adapter/src/client.js").RuntimeResolver =
  (id, identity) => registry.resolve(id, identity);
const pool = database();
const capabilities = process.env.TOOL_CAPABILITY_KEY
  ? new ToolCapabilities(pool, process.env.TOOL_CAPABILITY_KEY)
  : undefined;
const worker = new TaskWorker(new TaskStore(pool), resolve, capabilities);
const scheduler = new ResearchScheduler(pool, {
  research: new ResearchStore(pool),
  scanner: new MarketScanner(pool),
  forecasts: new ForecastStore(pool),
  experience: new ExperienceStore(pool),
  market: configuredMarketProvider(),
});
const closing = new AbortController();
process.once("SIGTERM", () => closing.abort());
process.once("SIGINT", () => closing.abort());
try {
  await runWorkerService(
    worker,
    closing.signal,
    async () => {
      try {
        await reload();
      } catch {
        console.error(
          "Registry update rejected; retaining last valid configuration",
        );
      }
    },
    (signal) => scheduler.tick(new Date(), signal),
  );
} finally {
  await pool.end();
}
