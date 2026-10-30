import { defineConfig } from "@playwright/test";

const database = process.env.YTA_TEST_DATABASE_URL ?? "postgresql://yta_app:local-yta-app@127.0.0.1:54329/yta_test";
if (!new URL(database).pathname.endsWith("_test")) throw new Error("E2E requires a dedicated _test database");
const env = { YTA_ENV: "test", YTA_DATA_MODE: "REPLAY", YTA_DATABASE_URL: database, YTA_ALPHA_TOKEN: "local-e2e-fixture-token-not-a-real-secret", YTA_ALPHA_URL: "http://127.0.0.1:8100", YTA_WEB_ORIGIN: "http://localhost:3000" };
export default defineConfig({
  testDir: "./tests/e2e", fullyParallel: false, workers: 1, timeout: 30000,
  globalSetup: "./tests/e2e/setup.ts", reporter: "list",
  use: { baseURL: "http://localhost:3000", browserName: "chromium", screenshot: "only-on-failure", trace: "off" },
  webServer: [
    { command: "uv run uvicorn yta_alpha.api:create_app --factory --host 127.0.0.1 --port 8100", url: "http://127.0.0.1:8100/health", env, reuseExistingServer: false },
    { command: "pnpm --filter @yta/bff start", url: "http://127.0.0.1:3100/api/yta/v1/ready", env, reuseExistingServer: false },
    { command: "pnpm --filter @yta/web start", url: "http://localhost:3000/login", reuseExistingServer: false },
  ],
});
