import {z} from "zod";
import {trainingEvidencePageSchema,trainingEvidenceQuerySchema,trainingEvidenceResponseSchema} from "@yta/contracts";
import type {Config} from "./config.js";
import {ApiError} from "./errors.js";
import {hashContent} from "./experimental-report.js";
export function readTrainingEvidence(value:unknown,mode:Config["YTA_DATA_MODE"],identity:string){
  const parsed=trainingEvidenceResponseSchema.safeParse(value);
  if(parsed.success && parsed.data.id===identity && parsed.data.data_mode===mode){
    const {content_hash,...report}=parsed.data.report;
    if(hashContent(report)===content_hash)return parsed.data;
  }
  throw new ApiError("UPSTREAM_INVALID","Training evidence response is invalid",502);
}

export function readTrainingEvidencePage(value:unknown,mode:Config["YTA_DATA_MODE"],modelId:string,query:z.output<typeof trainingEvidenceQuerySchema>){
  const parsed=trainingEvidencePageSchema.safeParse(value);
  if(parsed.success && parsed.data.items.length<=(query.limit??20)
    && parsed.data.items.every(r=>r.model_id===modelId && r.data_mode===mode && r.id!==query.cursor)
    && (!query.cursor || parsed.data.next_cursor!==query.cursor))return parsed.data;
  throw new ApiError("UPSTREAM_INVALID","Training evidence list is invalid",502);
}
