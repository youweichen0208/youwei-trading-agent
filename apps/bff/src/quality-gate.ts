import {qualityGateResponseSchema,type qualityGateRequestSchema} from "@yta/contracts";
import type {z} from "zod";
import type {Config} from "./config.js";
import {hashContent} from "./experimental-report.js";
import {ApiError} from "./errors.js";

export function readQualityGate(value:unknown,mode:Config["YTA_DATA_MODE"],expected:{id?:string;request?:z.output<typeof qualityGateRequestSchema>}) {
  const parsed=qualityGateResponseSchema.safeParse(value);
  if(parsed.success) {
    const row=parsed.data,{content_hash,...body}=row,input=expected.request;
    if(row.data_mode===mode && hashContent(body)===content_hash
      && (!expected.id || expected.id==="model-quality-gate-"+content_hash)
      && (!input || (input.proposal_id===row.proposal_id && input.price_assessment_id===row.price_assessment_id
        && input.probability_assessment_id===row.probability_assessment_id))) return row;
  }
  throw new ApiError("UPSTREAM_INVALID","Combined quality response is invalid",502);
}
