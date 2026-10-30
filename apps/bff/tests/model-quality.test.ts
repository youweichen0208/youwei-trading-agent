import {readFileSync} from "node:fs";
import {it,expect} from "vitest";
import {readQualityPolicy,readQualityAssessment} from "../src/model-quality.js";
import {hashContent} from "../src/experimental-report.js";
const saved=JSON.parse(readFileSync(new URL("../../../packages/contracts/fixtures/quality-assessment.json",import.meta.url),"utf8"));
const policyId="historical-quality-policy-"+saved.policy_hash;
const id="historical-quality-assessment-"+saved.content_hash;
const request={policy_id:policyId,candidate_evaluation_id:saved.candidate_evaluation_id,reference_evaluation_id:saved.reference_evaluation_id};
it("preserves Python quality rules and assessment hashes without recomputing financial metrics",()=>{
  expect(readQualityAssessment(saved,"REPLAY",{id})).toEqual(saved);
  expect(readQualityAssessment(saved,"REPLAY",{request})).toEqual(saved);
  const policy={id:policyId,content_hash:saved.policy_hash,policy:saved.policy};
  expect(readQualityPolicy(policy,policyId,"REPLAY")).toEqual(policy);
  expect(()=>readQualityPolicy(policy,policyId,"LIVE_RESEARCH")).toThrow();
  expect(()=>readQualityPolicy({...policy,content_hash:"0".repeat(64)},policyId,"REPLAY")).toThrow();
});
it("rejects wrong quality evidence, policy, mode, approval or unbound checks",()=>{
  for(const change of [{promotion_allowed:true},{qualification:"VERIFIED"},{numeric_pass:!saved.numeric_pass},
    {checks:saved.checks.slice(1)},{content_hash:"0".repeat(64)},{private:"secret"}]) {
    expect(()=>readQualityAssessment({...saved,...change},"REPLAY",{id})).toThrow();
  }
  const altered={...saved,policy:{...saved.policy,maximum_mae:"0.123"}};
  const {content_hash:_,...body}=altered;
  expect(()=>readQualityAssessment({...altered,content_hash:hashContent(body)},"REPLAY",{})).toThrow();
  expect(()=>readQualityAssessment(saved,"LIVE_RESEARCH",{})).toThrow();
  expect(()=>readQualityAssessment(saved,"REPLAY",{request:{...request,policy_id:"historical-quality-policy-"+"0".repeat(64)}})).toThrow();
  expect(()=>readQualityAssessment(saved,"REPLAY",{request:{...request,candidate_evaluation_id:"model-evaluation-experimental-"+"0".repeat(64)}})).toThrow();
});
it("validates discovered rules against mode, horizon, cursor, hash and page size", async()=>{
  const {readQualityPolicyPage}=await import("../src/model-quality.js");
  const row={id:policyId,content_hash:saved.policy_hash,policy:saved.policy};
  const page={items:[row],next_cursor:policyId};
  expect(readQualityPolicyPage(page,"REPLAY",{limit:1,horizon_sessions:"5"})).toEqual(page);
  for(const input of [{cursor:policyId},{horizon_sessions:"3" as const}]) expect(()=>readQualityPolicyPage(page,"REPLAY",input)).toThrow();
  expect(()=>readQualityPolicyPage(page,"LIVE_RESEARCH",{})).toThrow();
  expect(()=>readQualityPolicyPage({items:[row,row],next_cursor:null},"REPLAY",{})).toThrow();
  expect(()=>readQualityPolicyPage({items:[{...row,policy:{...saved.policy,maximum_mae:"0"}}],next_cursor:null},"REPLAY",{})).toThrow();
});
