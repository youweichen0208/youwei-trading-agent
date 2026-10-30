import {readFileSync} from "node:fs";
import {it,expect} from "vitest";
import {historicalQualityPolicySchema,qualityAssessmentResponseSchema,qualityAssessmentRequestSchema} from "../src/index.js";
const saved=JSON.parse(readFileSync(new URL("../fixtures/quality-assessment.json",import.meta.url),"utf8"));
it("accepts registered quality evidence with explicit decimal policy and fixed blocked promotion",()=>{
  expect(qualityAssessmentResponseSchema.parse(saved)).toEqual(saved);
  for(const value of ["0","00.1","1","01.000"]) expect(historicalQualityPolicySchema.safeParse({...saved.policy,minimum_direction_accuracy:value}).success).toBe(true);
  for(const value of ["1.000000000001","2","-0.1",0.5]) expect(historicalQualityPolicySchema.safeParse({...saved.policy,minimum_direction_accuracy:value}).success).toBe(false);
  for(const change of [{promotion_allowed:true},{checks:[saved.checks[0],...saved.checks]},{policy:{...saved.policy,minimum_samples:true}}]) {
    expect(qualityAssessmentResponseSchema.safeParse({...saved,...change}).success).toBe(false);
  }
  const request={policy_id:"historical-quality-policy-"+saved.policy_hash,candidate_evaluation_id:saved.candidate_evaluation_id};
  expect(qualityAssessmentRequestSchema.parse(request).reference_evaluation_id).toBeNull();
  expect(qualityAssessmentRequestSchema.safeParse({...request,policy:saved.policy}).success).toBe(false);
});
it("validates quality policy discovery filters and cursor membership", async()=>{
  const {qualityPolicyQuerySchema,qualityPolicyPageSchema}=await import("../src/index.js");
  expect(qualityPolicyQuerySchema.parse({limit:"2",horizon_sessions:"5"})).toEqual({limit:2,horizon_sessions:"5"});
  for(const input of [{limit:"51"},{limit:2},{horizon_sessions:"2"},{data_mode:"REPLAY"},{cursor:"bad"}]) expect(qualityPolicyQuerySchema.safeParse(input).success).toBe(false);
  const row={id:"historical-quality-policy-"+saved.policy_hash,content_hash:saved.policy_hash,policy:saved.policy};
  expect(qualityPolicyPageSchema.parse({items:[row],next_cursor:row.id}).items).toEqual([row]);
  expect(qualityPolicyPageSchema.safeParse({items:[row,row],next_cursor:null}).success).toBe(false);
  expect(qualityPolicyPageSchema.safeParse({items:[],next_cursor:row.id}).success).toBe(false);
});
