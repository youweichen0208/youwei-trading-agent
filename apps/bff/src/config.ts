import { config as dotenv } from "dotenv";
import { z } from "zod";

const reviewerIds = z.string().default("").transform(value => value === "" ? [] : value.split(",").map(id => id.trim().toLowerCase()))
  .pipe(z.array(z.uuid()).max(100).refine(ids => new Set(ids).size === ids.length,
    {message: "Reviewer identities must be distinct"}));

const schema = z.object({
  YTA_ENV: z.enum(["development", "test", "staging", "production"]).default("development"),
  YTA_DATA_MODE: z.enum(["REPLAY", "LIVE_RESEARCH"]).default("REPLAY"),
  YTA_RESEARCH_DRIVER: z.enum(["AUTO", "HERMES"]).default("AUTO"),
  YTA_HERMES_ORIGIN: z.url().optional(),
  YTA_HERMES_TOKEN: z.string().min(32).regex(/^[^\r\n]+$/).optional(),
  YTA_HERMES_MODEL: z.string().trim().min(1).max(200).optional(),
  YTA_DATABASE_URL: z.string().min(1),
  YTA_ALPHA_URL: z.url().default("http://127.0.0.1:8100"),
  YTA_ALPHA_TOKEN: z.string().min(32),
  YTA_WEB_ORIGIN: z.url().default("http://localhost:3000"),
  YTA_BFF_HOST: z.enum(["127.0.0.1", "0.0.0.0"]).default("127.0.0.1"),
  YTA_BFF_PORT: z.coerce.number().int().min(0).max(65535).default(3100),
  YTA_EXPERIMENTAL_MODELS: z.enum(["DISABLED", "ENABLED"]).default("DISABLED"),
  YTA_MODEL_REVIEWER_IDS: reviewerIds,
  YTA_WRITE_ADMISSION: z.enum(["OPEN", "PAUSED"]).default("OPEN"),
  YTA_RUN_TIMEOUT_MS: z.coerce.number().int().min(100).max(300000).default(120000),
  YTA_RESEARCH_RUNS_PER_24H: z.coerce.number().int().min(1).max(10000).default(50),
  YTA_OTLP_TRACES_URL: z.url().optional(),
});
export type Config = z.infer<typeof schema>;

export function readConfig(env?: NodeJS.ProcessEnv): Config {
  if (!env) dotenv({ path: new URL("../../../.env", import.meta.url) });
  const result = schema.safeParse(env ?? process.env);
  if (!result.success) throw new Error(`Invalid configuration: ${result.error.issues.map(i => i.path.join(".")).join(", ")}`);
  const value = result.data;
  const deployed = ["production", "staging"].includes(value.YTA_ENV);
  const hermesFields = [value.YTA_HERMES_ORIGIN, value.YTA_HERMES_TOKEN, value.YTA_HERMES_MODEL];
  if (value.YTA_RESEARCH_DRIVER === "HERMES") {
    if (hermesFields.some(field => !field)) throw new Error("Hermes requires origin, token and model configuration");
    if (deployed) throw new Error("Hermes deployment capability gates remain unverified");
    if (value.YTA_HERMES_TOKEN === value.YTA_ALPHA_TOKEN || value.YTA_HERMES_TOKEN?.startsWith("replace-with")) {
      throw new Error("Hermes requires a distinct non-example gateway credential");
    }
  } else if (hermesFields.some(field => field !== undefined)) {
    throw new Error("Hermes configuration requires explicit HERMES driver selection");
  }
  if (value.YTA_OTLP_TRACES_URL) {
    const url = new URL(value.YTA_OTLP_TRACES_URL);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash || url.pathname !== "/v1/traces") {
      throw new Error("Tracing requires an HTTP(S) /v1/traces endpoint without credentials or query");
    }
  }
  if (deployed && (value.YTA_DATA_MODE !== "LIVE_RESEARCH" || !value.YTA_WEB_ORIGIN.startsWith("https://"))) {
    throw new Error("Deployed BFF requires LIVE_RESEARCH and HTTPS web origin");
  }
  for (const target of [value.YTA_ALPHA_URL, value.YTA_WEB_ORIGIN, ...(value.YTA_HERMES_ORIGIN ? [value.YTA_HERMES_ORIGIN] : [])]) {
    const url = new URL(target);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash || url.pathname !== "/") {
      throw new Error("Configured service URLs must be HTTP(S) origins without credentials or paths");
    }
  }
  if (value.YTA_ALPHA_TOKEN.startsWith("replace-with")) throw new Error("Replace the example service token");
  return value;
}
