import {modelReviewPageSchema,modelReviewResponseSchema,type modelReviewSubmissionSchema} from "@yta/contracts";
import type {z} from "zod";
import type {Config} from "./config.js";
import {ApiError} from "./errors.js";
import {hashContent} from "./experimental-report.js";
export function readModelReview(value:unknown,mode:Config["YTA_DATA_MODE"],
  expected:{id?:string;reviewer?:string;key?:string;request?:z.output<typeof modelReviewSubmissionSchema>}) {
  const parsed=modelReviewResponseSchema.safeParse(value);
  if(parsed.success) {
    const result=parsed.data,row=result.review,{content_hash,...body}=row,input=expected.request;
    if(row.data_mode===mode && hashContent(body)===content_hash
      && (!expected.id || result.id===expected.id) && (!expected.reviewer || row.reviewer_id===expected.reviewer)
      && (!expected.key || row.request_id===expected.key)
      && (!input || (row.proposal_id===input.proposal_id && ("quality_gate_id" in input
          ? row.schema_version==="model-review-record-v2" && row.quality_gate_id===input.quality_gate_id
          : row.schema_version==="model-review-record-v1" && row.assessment_id===input.assessment_id)
        && row.action===input.action && row.reason===input.reason && row.previous_review_id===input.expected_previous_review_id))) return result;
  }
  throw new ApiError("UPSTREAM_INVALID","Model review response is invalid",502);
}

export function readModelReviewPage(value:unknown,mode:Config["YTA_DATA_MODE"],proposal:string,
  query:{cursor?:string;limit?:number}) {
  const parsed=modelReviewPageSchema.safeParse(value);
  if(parsed.success) {
    const page=parsed.data,items=page.items,limit=query.limit ?? 20,tail=items.at(-1);
    const valid=items.length<=limit && new Set(items.map(item=>item.id)).size===items.length
      && items.every((item,index)=>item.review.proposal_id===proposal && item.id!==query.cursor
        && (index===0 || (items[index-1]!.review.previous_review_id===item.id
          && items[index-1]!.review.revision===item.review.revision+1 && item.review.disposition!=="REJECTED")))
      && (tail ? (tail.review.revision===1 ? page.next_cursor===null : page.next_cursor===tail.id && items.length===limit)
        : page.next_cursor===null);
    if(valid) {
      items.forEach(item=>readModelReview(item,mode,{}));
      return page;
    }
  }
  throw new ApiError("UPSTREAM_INVALID","Model review history is invalid",502);
}
