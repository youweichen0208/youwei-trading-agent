import type { Pool } from "pg";
import type { AlphaClient } from "./alpha.js";
import type { Config } from "./config.js";
import { HermesClient } from "./hermes-client.js";
import { HermesStore } from "./hermes-store.js";
import { HermesResearch } from "./hermes-research.js";
import { HermesResearchDriver } from "./hermes-driver.js";
import { ReplayResearchDriver, UnavailableResearchDriver, type ResearchDriver } from "./research-runner.js";

export function configuredDriver(config: Config, pool: Pool, alpha: AlphaClient): ResearchDriver {
  if (config.YTA_RESEARCH_DRIVER !== "HERMES") {
    return config.YTA_DATA_MODE === "REPLAY" ? new ReplayResearchDriver(alpha) : new UnavailableResearchDriver();
  }
  const {YTA_HERMES_ORIGIN: origin, YTA_HERMES_TOKEN: token, YTA_HERMES_MODEL: model} = config;
  // Also fail closed for direct callers supplying Config without readConfig().
  if (!origin || !token || !model || ["staging", "production"].includes(config.YTA_ENV)) {
    throw new Error("Hermes configuration unavailable or deployment gates unverified");
  }
  const work = new HermesResearch(alpha);
  return new HermesResearchDriver(new HermesStore(pool, config.YTA_DATA_MODE),
    new HermesClient({origin, token, profile: "alpha-orchestrator", timeoutMs: config.YTA_RUN_TIMEOUT_MS}), model,
    (run, state) => work.complete(run, state), (run, signal) => work.prepare(run, signal));
}
