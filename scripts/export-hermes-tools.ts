import { writeFile } from "node:fs/promises";
import { toolDescriptors } from "../packages/server/src/tool-gateway.js";

const roles = ["research", "prediction", "portfolio"] as const;
await writeFile(
  new URL("../services/hermes/alpha_hermes/tools.json", import.meta.url),
  JSON.stringify(
    Object.fromEntries(roles.map((r) => [r, toolDescriptors(r)])),
    null,
    2,
  ) + "\n",
);
