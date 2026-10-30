import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

const database = process.env.YTA_TEST_DATABASE_URL ?? "postgresql://yta_app:local-yta-app@127.0.0.1:54329/yta_test";
const env = {
  YTA_ENV: "test", YTA_DATA_MODE: "REPLAY", YTA_DATABASE_URL: database,
  YTA_ALPHA_TOKEN: "isolated-e2e-fixture-token-not-a-real-secret",
  YTA_ALPHA_URL: "http://127.0.0.1:18100", YTA_BFF_PORT: "13100",
  YTA_WEB_ORIGIN: "http://localhost:13000", YTA_RESEARCH_RUNS_PER_24H: "50",
};
export default defineConfig({
  ...base,
  use: {...base.use, baseURL: "http://localhost:13000"},
  webServer: [
    {command: "uv run uvicorn yta_alpha.api:create_app --factory --host 127.0.0.1 --port 18100", url: "http://127.0.0.1:18100/health", env, reuseExistingServer: false},
    {command: "pnpm --filter @yta/bff start", url: "http://127.0.0.1:13100/api/yta/v1/ready", env, reuseExistingServer: false},
    {command: "pnpm --filter @yta/web build && pnpm --filter @yta/web exec next start --hostname 127.0.0.1 --port 13000", url: "http://localhost:13000/login", timeout: 120000,
      env: {YTA_E2E_ISOLATED: "1", YTA_BFF_ORIGIN: "http://127.0.0.1:13100"}, reuseExistingServer: false},
  ],
});
