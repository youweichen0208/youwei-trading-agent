import {readFileSync} from "node:fs";
import {it,expect} from "vitest";
import {qualityGateRequestSchema,qualityGateResponseSchema,qualityGateIdSchema} from "../src/index.js";
const saved=JSON.parse(readFileSync(new URL("../fixtures/model-quality-gate.json",import.meta.url),"utf8"));
it("preserves combined evidence and rejects contradictory numeric or approval state",()=>{
  expect(qualityGateResponseSchema.parse(saved)).toEqual(saved);
  const request={proposal_id:saved.proposal_id,price_assessment_id:saved.price_assessment_id,probability_assessment_id:saved.probability_assessment_id};
  expect(qualityGateRequestSchema.parse(request)).toEqual(request);
  for(const extra of [{owner_id:saved.proposal_id},{data_mode:"REPLAY"},{numeric_pass:true}]) expect(qualityGateRequestSchema.safeParse({...request,...extra}).success).toBe(false);
  for(const change of [{numeric_pass:!saved.numeric_pass},{promotion_allowed:true},{qualification:"VERIFIED"},{promotion_blockers:[]},
    {price_numeric_pass:true,failed_price_checks:["minimum_samples"]},{probability_numeric_pass:true,failed_probability_checks:["maximum_ece"]},
    {failed_price_checks:["minimum_samples","minimum_samples"]}]) expect(qualityGateResponseSchema.safeParse({...saved,...change}).success).toBe(false);
  expect(qualityGateIdSchema.safeParse("model-quality-gate-"+saved.content_hash).success).toBe(true);
  expect(qualityGateIdSchema.safeParse(saved.content_hash).success).toBe(false);
});
