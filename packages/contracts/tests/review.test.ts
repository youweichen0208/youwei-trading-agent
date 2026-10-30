import {readFileSync} from "node:fs";
import {it,expect} from "vitest";
import {modelReviewRequestSchema,modelReviewSubmissionSchema,modelReviewResponseSchema} from "../src/index.js";
const saved=JSON.parse(readFileSync(new URL("../fixtures/model-review.json",import.meta.url),"utf8"));
it("accepts blocked/rejected review transport but rejects injected authority and inconsistent state",()=>{
  expect(modelReviewResponseSchema.parse(saved)).toEqual(saved);
  const r=saved.review;
  const input={proposal_id:r.proposal_id,assessment_id:r.assessment_id,action:r.action,reason:r.reason};
  expect(modelReviewRequestSchema.parse(input).expected_previous_review_id).toBeNull();
  for(const extra of [{reviewer_id:r.reviewer_id},{reason:" "},{reason:"🧪".repeat(2001)},{promotion_allowed:true}]) expect(modelReviewRequestSchema.safeParse({...input,...extra}).success).toBe(false);
  for(const changes of [{disposition:"APPROVED"},{promotion_allowed:true},{revision:2},{blocking_reasons:[]},{failed_checks:["minimum_samples","minimum_samples"]}]) expect(modelReviewResponseSchema.safeParse({...saved,review:{...r,...changes}}).success).toBe(false);
});

const combined=JSON.parse(readFileSync(new URL("../fixtures/model-review-v2.json",import.meta.url),"utf8"));
it("accepts v2 fixed evidence and rejects ambiguous requests and inconsistent combined review state",()=>{
  expect(modelReviewResponseSchema.parse(combined)).toEqual(combined);
  const r=combined.review,input={proposal_id:r.proposal_id,quality_gate_id:r.quality_gate_id,action:r.action,reason:r.reason};
  expect(modelReviewSubmissionSchema.parse(input).expected_previous_review_id).toBeNull();
  for(const extra of [{assessment_id:saved.review.assessment_id},{reviewer_id:r.reviewer_id},{promotion_allowed:true}])
    expect(modelReviewSubmissionSchema.safeParse({...input,...extra}).success).toBe(false);
  for(const change of [{quality_gate_content_hash:"0".repeat(64)},{blocking_reasons:[]},{failed_probability_checks:["maximum_ece","maximum_ece"]},{promotion_allowed:true},{schema_version:"model-review-record-v1"}])
    expect(modelReviewResponseSchema.safeParse({...combined,review:{...r,...change}}).success).toBe(false);
});
