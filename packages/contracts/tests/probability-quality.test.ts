import {readFileSync} from "node:fs";
import {it,expect} from "vitest";
import {probabilityQualityPolicySchema,probabilityAssessmentResponseSchema,probabilityAssessmentRequestSchema} from "../src/index.js";
const saved=JSON.parse(readFileSync(new URL("../fixtures/probability-quality-assessment.json",import.meta.url),"utf8"));
it("accepts registered quality evidence with explicit decimal policy and fixed blocked promotion",()=>{
  expect(probabilityAssessmentResponseSchema.parse(saved)).toEqual(saved);
  for(const value of ["0","0.1","1","1.000"]) expect(probabilityQualityPolicySchema.safeParse({...saved.policy,maximum_brier:value}).success).toBe(true);
  for(const value of ["1.000000000001","2","-0.1",0.5]) expect(probabilityQualityPolicySchema.safeParse({...saved.policy,maximum_brier:value}).success).toBe(false);
  for(const change of [{promotion_allowed:true},{checks:[saved.checks[0],...saved.checks]},{policy:{...saved.policy,minimum_evaluation_samples:true}}]) {
    expect(probabilityAssessmentResponseSchema.safeParse({...saved,...change}).success).toBe(false);
  }
  const request={policy_id:"probability-quality-policy-"+saved.policy_hash,calibration_id:saved.calibration_id};
  expect(probabilityAssessmentRequestSchema.parse(request)).toEqual(request);
  expect(probabilityAssessmentRequestSchema.safeParse({...request,policy:saved.policy}).success).toBe(false);
});
it("validates quality policy discovery filters and cursor membership", async()=>{
  const {probabilityPolicyQuerySchema,probabilityPolicyPageSchema}=await import("../src/index.js");
  expect(probabilityPolicyQuerySchema.parse({limit:"2",horizon_sessions:"5"})).toEqual({limit:2,horizon_sessions:"5"});
  for(const input of [{limit:"51"},{limit:2},{horizon_sessions:"2"},{data_mode:"REPLAY"},{cursor:"bad"}]) expect(probabilityPolicyQuerySchema.safeParse(input).success).toBe(false);
  const row={id:"probability-quality-policy-"+saved.policy_hash,content_hash:saved.policy_hash,policy:saved.policy};
  expect(probabilityPolicyPageSchema.parse({items:[row],next_cursor:row.id}).items).toEqual([row]);
  expect(probabilityPolicyPageSchema.safeParse({items:[row,row],next_cursor:null}).success).toBe(false);
  expect(probabilityPolicyPageSchema.safeParse({items:[],next_cursor:row.id}).success).toBe(false);
});
