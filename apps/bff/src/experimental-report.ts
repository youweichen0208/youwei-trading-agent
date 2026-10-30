import { createHash } from "node:crypto";
import { experimentalReportComparisonSchema, experimentalPriceReportSchema } from "@yta/contracts";
import type { Config } from "./config.js";
import { ApiError } from "./errors.js";

// JSON object keys follow Python Unicode code-point order, including source metadata.
// Callers validate safe integer numeric fields before hashing.
// Preserve array order and original timestamp/decimal strings, matching Alpha content_hash.
function compareKeys(a:string,b:string):number {
  const left=Array.from(a),right=Array.from(b);
  for(let i=0;i<Math.min(left.length,right.length);i++){
    const difference=left[i]!.codePointAt(0)!-right[i]!.codePointAt(0)!;
    if(difference)return difference;
  }
  return left.length-right.length;
}
function canonical(value: unknown): string {
  if (Array.isArray(value)) return "[" + value.map(canonical).join(",") + "]";
  if (value !== null && typeof value === "object") {
    return "{" + Object.entries(value).sort(([a], [b]) => compareKeys(a,b))
      .map(([key, item]) => JSON.stringify(key) + ":" + canonical(item)).join(",") + "}";
  }
  return JSON.stringify(value);
}
export const hashContent = (value: unknown) => createHash("sha256").update(canonical(value)).digest("hex");
export function readExperimentalReport(value: unknown, identity: string, mode: Config["YTA_DATA_MODE"]) {
  const parsed = experimentalPriceReportSchema.safeParse(value);
  if (parsed.success) {
    const {content_hash, ...payload} = parsed.data;
    if (identity === "model-evaluation-experimental-" + content_hash
      && parsed.data.cohort.data_mode === mode && hashContent(payload) === content_hash
      && hashContent(parsed.data.cohort) === parsed.data.cohort_hash) return parsed.data;
  }
  throw new ApiError("UPSTREAM_INVALID", "Experimental evaluation response is invalid", 502);
}


export function readExperimentalComparison(value: unknown, identities: string[], mode: Config["YTA_DATA_MODE"]) {
  const parsed = experimentalReportComparisonSchema.safeParse(value);
  if (parsed.success && parsed.data.evaluation_ids.length === identities.length
    && parsed.data.evaluation_ids.every((id, i) => id === identities[i])) {
    parsed.data.reports.forEach((report, i) => readExperimentalReport(report, identities[i]!, mode));
    return parsed.data;
  }
  throw new ApiError("UPSTREAM_INVALID", "Experimental comparison response is invalid", 502);
}
