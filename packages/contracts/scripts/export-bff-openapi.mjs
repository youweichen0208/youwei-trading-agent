import { readFileSync, writeFileSync } from "node:fs";
import { buildBffOpenApi } from "./build-bff-openapi.mjs";

const file = new URL("../bff.openapi.json", import.meta.url);
const expected = JSON.stringify(buildBffOpenApi(), null, 2) + "\n";
const args = process.argv.slice(2);
if (args.length && (args.length !== 1 || args[0] !== "--check")) throw new Error("Usage: export-bff-openapi.mjs [--check]");
if (args[0] === "--check") {
  if (readFileSync(file, "utf8") !== expected) throw new Error("BFF OpenAPI drift: run pnpm contracts:bff:export");
} else writeFileSync(file, expected);
console.log("BFF OpenAPI " + (args.length ? "checked" : "exported"));
