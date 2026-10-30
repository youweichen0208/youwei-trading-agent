import { readFileSync, writeFileSync } from "node:fs";
import { z } from "zod";
import { researchReportSchema } from "../dist/index.js";

const args = process.argv.slice(2);
if (args.length && (args.length !== 1 || args[0] !== "--check")) throw new Error("Usage: export-report.mjs [--check]");
const artifact = z.toJSONSchema(researchReportSchema);
artifact.description = "Orchestrator prose and references only. BFF validateResearchReport additionally binds scope, evidence and draft to verified server context; JSON shape alone grants no authority.";
const expected = JSON.stringify(artifact, null, 2) + "\n";
const file = new URL("../research-report.schema.json", import.meta.url);
if (args.length) {
  if (readFileSync(file, "utf8") !== expected) throw new Error("Research report schema drift");
} else writeFileSync(file, expected);
console.log("Research report schema " + (args.length ? "checked" : "exported"));
