import { probabilityPolicyPageSchema, type probabilityPolicyQuerySchema, probabilityPolicyResponseSchema, probabilityAssessmentResponseSchema, type probabilityAssessmentRequestSchema } from "@yta/contracts";
import type { z } from "zod";
import type { Config } from "./config.js";
import { hashContent } from "./experimental-report.js";
import { ApiError } from "./errors.js";

export function readProbabilityPolicy(value: unknown, id: string, mode: Config["YTA_DATA_MODE"]) {
  const parsed=probabilityPolicyResponseSchema.safeParse(value);
  if(parsed.success && parsed.data.id===id && parsed.data.policy.data_mode===mode
    && hashContent(parsed.data.policy)===parsed.data.content_hash) return parsed.data;
  throw new ApiError("UPSTREAM_INVALID","Quality policy response is invalid",502);
}
export function readProbabilityAssessment(value: unknown, mode: Config["YTA_DATA_MODE"],
  expected: {id?:string; request?:z.output<typeof probabilityAssessmentRequestSchema>}) {
  const parsed=probabilityAssessmentResponseSchema.safeParse(value);
  if(parsed.success) {
    const result=parsed.data,{content_hash,...body}=result,input=expected.request;
    if(result.policy.data_mode===mode && hashContent(result.policy)===result.policy_hash
      && hashContent(body)===content_hash
      && (!expected.id || expected.id==="probability-quality-assessment-"+content_hash)
      && (!input || (input.policy_id==="probability-quality-policy-"+result.policy_hash
        && input.calibration_id===result.calibration_id))) return result;
  }
  throw new ApiError("UPSTREAM_INVALID","Quality assessment response is invalid",502);
}

export function readProbabilityPolicyPage(value: unknown, mode: Config["YTA_DATA_MODE"], input: z.output<typeof probabilityPolicyQuerySchema>) {
  const parsed=probabilityPolicyPageSchema.safeParse(value);
  if(!parsed.success || parsed.data.items.length>(input.limit ?? 20)
    || parsed.data.items.some(row=>row.id===input.cursor || (input.horizon_sessions && String(row.policy.horizon_sessions)!==input.horizon_sessions))
    || (input.cursor && parsed.data.next_cursor===input.cursor)) {
    throw new ApiError("UPSTREAM_INVALID","Quality policy page is invalid",502);
  }
  for(const item of parsed.data.items) readProbabilityPolicy(item,item.id,mode);
  return parsed.data;
}
