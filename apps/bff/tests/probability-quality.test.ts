import {readFileSync} from "node:fs";
import {it,expect} from "vitest";
import {readProbabilityPolicy,readProbabilityAssessment} from "../src/probability-quality.js";
import {hashContent} from "../src/experimental-report.js";
const saved=JSON.parse(readFileSync(new URL("../../../packages/contracts/fixtures/probability-quality-assessment.json",import.meta.url),"utf8"));
const policyId="probability-quality-policy-"+saved.policy_hash;
const id="probability-quality-assessment-"+saved.content_hash;
const request={policy_id:policyId,calibration_id:saved.calibration_id};
it("preserves Python quality rules and assessment hashes without recomputing financial metrics",()=>{
  expect(readProbabilityAssessment(saved,"REPLAY",{id})).toEqual(saved);
  expect(readProbabilityAssessment(saved,"REPLAY",{request})).toEqual(saved);
  const policy={id:policyId,content_hash:saved.policy_hash,policy:saved.policy};
  expect(readProbabilityPolicy(policy,policyId,"REPLAY")).toEqual(policy);
  expect(()=>readProbabilityPolicy(policy,policyId,"LIVE_RESEARCH")).toThrow();
  expect(()=>readProbabilityPolicy({...policy,content_hash:"0".repeat(64)},policyId,"REPLAY")).toThrow();
});
it("rejects wrong quality evidence, policy, mode, approval or unbound checks",()=>{
  for(const change of [{promotion_allowed:true},{qualification:"VERIFIED"},{numeric_pass:!saved.numeric_pass},
    {checks:saved.checks.slice(1)},{content_hash:"0".repeat(64)},{private:"secret"}]) {
    expect(()=>readProbabilityAssessment({...saved,...change},"REPLAY",{id})).toThrow();
  }
  const altered={...saved,policy:{...saved.policy,maximum_brier:"0.123"}};
  const {content_hash:_,...body}=altered;
  expect(()=>readProbabilityAssessment({...altered,content_hash:hashContent(body)},"REPLAY",{})).toThrow();
  expect(()=>readProbabilityAssessment(saved,"LIVE_RESEARCH",{})).toThrow();
  expect(()=>readProbabilityAssessment(saved,"REPLAY",{request:{...request,policy_id:"probability-quality-policy-"+"0".repeat(64)}})).toThrow();
  expect(()=>readProbabilityAssessment(saved,"REPLAY",{request:{...request,calibration_id:"calibration-experimental-"+"0".repeat(64)}})).toThrow();
});
it("validates discovered rules against mode, horizon, cursor, hash and page size", async()=>{
  const {readProbabilityPolicyPage}=await import("../src/probability-quality.js");
  const row={id:policyId,content_hash:saved.policy_hash,policy:saved.policy};
  const page={items:[row],next_cursor:policyId};
  expect(readProbabilityPolicyPage(page,"REPLAY",{limit:1,horizon_sessions:"5"})).toEqual(page);
  for(const input of [{cursor:policyId},{horizon_sessions:"3" as const}]) expect(()=>readProbabilityPolicyPage(page,"REPLAY",input)).toThrow();
  expect(()=>readProbabilityPolicyPage(page,"LIVE_RESEARCH",{})).toThrow();
  expect(()=>readProbabilityPolicyPage({items:[row,row],next_cursor:null},"REPLAY",{})).toThrow();
  expect(()=>readProbabilityPolicyPage({items:[{...row,policy:{...saved.policy,maximum_brier:"0"}}],next_cursor:null},"REPLAY",{})).toThrow();
});
