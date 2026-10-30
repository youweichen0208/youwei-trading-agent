import {readFileSync} from "node:fs";
import {it,expect} from "vitest";
import {readQualityGate} from "../src/quality-gate.js";
import {hashContent} from "../src/experimental-report.js";
const saved=JSON.parse(readFileSync(new URL("../../../packages/contracts/fixtures/model-quality-gate.json",import.meta.url),"utf8"));
const id="model-quality-gate-"+saved.content_hash;
const request={proposal_id:saved.proposal_id,price_assessment_id:saved.price_assessment_id,probability_assessment_id:saved.probability_assessment_id};
it("binds combined results to exact request, identity, mode and original content hash",()=>{
 expect(readQualityGate(saved,"REPLAY",{id})).toEqual(saved);
 expect(readQualityGate(saved,"REPLAY",{request})).toEqual(saved);
 expect(()=>readQualityGate(saved,"LIVE_RESEARCH",{id})).toThrow();
 expect(()=>readQualityGate(saved,"REPLAY",{id:"model-quality-gate-"+"0".repeat(64)})).toThrow();
 for(const field of ["proposal_id","price_assessment_id","probability_assessment_id"]) expect(()=>readQualityGate(saved,"REPLAY",{request:{...request,[field]:"wrong"}})).toThrow();
 expect(()=>readQualityGate({...saved,proposal_content_hash:"0".repeat(64)},"REPLAY",{})).toThrow();
 expect(()=>readQualityGate({...saved,private:"secret"},"REPLAY",{})).toThrow();
});
it("rejects rehashed contradictory combined states",()=>{
 for(const change of [{numeric_pass:!saved.numeric_pass},{promotion_allowed:true},{qualification:"VERIFIED"},{promotion_blockers:[]}]) {
   const {content_hash:_,...body}={...saved,...change};
   expect(()=>readQualityGate({...body,content_hash:hashContent(body)},"REPLAY",{})).toThrow();
 }
});
