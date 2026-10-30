import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

// Only the dedicated local REPLAY Compose stack; never target a deployed service.
const database = new URL(process.env.YTA_TEST_DATABASE_URL ?? "postgresql://yta_app:local-yta-app@127.0.0.1:54329/yta_test");
if (database.hostname !== "127.0.0.1" || database.port !== "54329"
    || database.pathname !== "/yta_test" || database.username !== "yta_app") {
  throw new Error("Container browser checks require the local yta_app/yta_test database");
}

export default defineConfig({
  ...base,
  webServer: [],
  outputDir: "test-results/container",
  use: { ...base.use, baseURL: "http://localhost:3300" },
});
