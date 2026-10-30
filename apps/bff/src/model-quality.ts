import { qualityPolicyPageSchema, type qualityPolicyQuerySchema, qualityPolicyResponseSchema, qualityAssessmentResponseSchema, type qualityAssessmentRequestSchema } from "@yta/contracts";
import type { z } from "zod";
import type { Config } from "./config.js";
import { hashContent } from "./experimental-report.js";
import { ApiError } from "./errors.js";

export function readQualityPolicy(value: unknown, id: string, mode: Config["YTA_DATA_MODE"]) {
  const parsed=qualityPolicyResponseSchema.safeParse(value);
  if(parsed.success && parsed.data.id===id && parsed.data.policy.data_mode===mode
    && hashContent(parsed.data.policy)===parsed.data.content_hash) return parsed.data;
  throw new ApiError("UPSTREAM_INVALID","Quality policy response is invalid",502);
}
export function readQualityAssessment(value: unknown, mode: Config["YTA_DATA_MODE"],
  expected: {id?:string; request?:z.output<typeof qualityAssessmentRequestSchema>}) {
  const parsed=qualityAssessmentResponseSchema.safeParse(value);
  if(parsed.success) {
    const result=parsed.data,{content_hash,...body}=result,input=expected.request;
    if(result.policy.data_mode===mode && hashContent(result.policy)===result.policy_hash
      && hashContent(body)===content_hash
      && (!expected.id || expected.id==="historical-quality-assessment-"+content_hash)
      && (!input || (input.policy_id==="historical-quality-policy-"+result.policy_hash
        && input.candidate_evaluation_id===result.candidate_evaluation_id
        && input.reference_evaluation_id===result.reference_evaluation_id))) return result;
  }
  throw new ApiError("UPSTREAM_INVALID","Quality assessment response is invalid",502);
}

export function readQualityPolicyPage(value: unknown, mode: Config["YTA_DATA_MODE"], input: z.output<typeof qualityPolicyQuerySchema>) {
  const parsed=qualityPolicyPageSchema.safeParse(value);
  if(!parsed.success || parsed.data.items.length>(input.limit ?? 20)
    || parsed.data.items.some(row=>row.id===input.cursor || (input.horizon_sessions && String(row.policy.horizon_sessions)!==input.horizon_sessions))
    || (input.cursor && parsed.data.next_cursor===input.cursor)) {
    throw new ApiError("UPSTREAM_INVALID","Quality policy page is invalid",502);
  }
  for(const item of parsed.data.items) readQualityPolicy(item,item.id,mode);
  return parsed.data;
}
