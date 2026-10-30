import {readFileSync} from "node:fs";
import {it,expect} from "vitest";
import {readTrainingEvidencePage,readTrainingEvidence} from "../src/training-evidence.js";
import {hashContent} from "../src/experimental-report.js";
import {safeRoute} from "../src/observability.js";
const read=(name:string)=>JSON.parse(readFileSync(new URL(`../../../packages/contracts/fixtures/${name}.json`,import.meta.url),"utf8"));
const matched=read("training-evidence-matched"),mismatch=read("training-evidence-mismatch");
it("reads exact training identities and rejects changed hashes, mode or disclosure",()=>{
  for(const value of [matched,mismatch])expect(readTrainingEvidence(value,"REPLAY",value.id)).toEqual(value);
  expect(()=>readTrainingEvidence(matched,"DEMO",matched.id)).toThrow();
  expect(()=>readTrainingEvidence(matched,"REPLAY",mismatch.id)).toThrow();
  expect(()=>readTrainingEvidence({...matched,input:{private:true}},"REPLAY",matched.id)).toThrow();
  expect(()=>readTrainingEvidence({...matched,report:{...matched.report,verifier_code_sha256:"0".repeat(64)}},"REPLAY",matched.id)).toThrow();
  const value=structuredClone(mismatch);value.report.status="MATCHED";const {content_hash,...body}=value.report;
  value.report.content_hash=hashContent(body);expect(()=>readTrainingEvidence(value,"REPLAY",value.id)).toThrow();
  for(const prefix of ["/api/yta/v1","/internal/v1"])expect(safeRoute(prefix+"/training-evidence/"+matched.id+"?private=value")).toBe(prefix+"/training-evidence/:id");
});
it("matches Python canonical hashes for Unicode source keys, controls and own special keys",()=>{
  for(const vector of read("python-hash-vectors"))expect(hashContent(vector.value)).toBe(vector.sha256);
});
it("checks training catalog scope, bounded pages and non-repeating cursors",()=>{
  const page=read("training-evidence-page"),id=page.items[0].model_id;
  expect(readTrainingEvidencePage(page,"REPLAY",id,{})).toEqual(page);
  for(const [value,mode,model,query] of [
    [page,"DEMO",id,{}],[page,"REPLAY","lightgbm-experimental-"+"0".repeat(64),{}],
    [page,"REPLAY",id,{limit:1}],[page,"REPLAY",id,{cursor:page.items[0].id}],
    [{...page,next_cursor:page.items[0].id},"REPLAY",id,{}],
    [{...page,items:[page.items[0],page.items[0]]},"REPLAY",id,{}],
    [{...page,private:"secret"},"REPLAY",id,{}],
  ] as const)expect(()=>readTrainingEvidencePage(value,mode,model,query)).toThrow();
  expect(safeRoute("/api/yta/v1/experimental-models/"+id+"/training-evidence?cursor=secret")).toBe("/api/yta/v1/experimental-models/:id/training-evidence");
});
