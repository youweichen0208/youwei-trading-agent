import { createBff } from "./app.js";
import { readConfig } from "./config.js";
import { startTracing } from "./tracing.js";

let shutdownTracing = async () => {};
try {
  const config = readConfig();
  shutdownTracing = startTracing(config);
  const server = await createBff({config});
  await server.app.listen(server.config.YTA_BFF_PORT, server.config.YTA_BFF_HOST);
  console.log(`YTA BFF listening on ${server.config.YTA_BFF_HOST}:${server.config.YTA_BFF_PORT} (${server.config.YTA_DATA_MODE})`);
  for (const signal of ["SIGINT", "SIGTERM"] as const) process.once(signal, () => {
    void server.close().then(shutdownTracing).then(() => process.exit(0), () => process.exit(1));
  });
} catch (error) {
  console.error(JSON.stringify({ event: "bff.startup_failed", kind: error instanceof Error ? error.name : "unknown" }));
  process.exitCode = 1;
  await shutdownTracing().catch(() => {});
}
