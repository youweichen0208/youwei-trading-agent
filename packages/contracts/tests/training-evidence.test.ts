import {trainingEvidencePageSchema,trainingEvidenceQuerySchema} from "../src/training-evidence.js";
import {readFileSync} from "node:fs";
import {it,expect} from "vitest";
import {trainingEvidenceResponseSchema} from "../src/training-evidence.js";
const read=(name:string)=>JSON.parse(readFileSync(new URL(`../fixtures/training-evidence-${name}.json`,import.meta.url),"utf8"));
const matched=read("matched"),mismatch=read("mismatch");
it("preserves matched and mismatched evidence without raw inputs or approval",()=>{
  for(const value of [matched,mismatch])expect(trainingEvidenceResponseSchema.parse(value)).toEqual(value);
});
it("rejects incomplete or contradictory field partitions, authority and bindings",()=>{
  for(const mutate of [
    (r:any)=>r.report.matched_fields.pop(),(r:any)=>r.report.matched_fields.reverse(),
    (r:any)=>r.report.matched_fields.push("model_text"),(r:any)=>r.report.mismatched_fields.push("config"),
    (r:any)=>r.report.matched_fields.push("unknown"),(r:any)=>r.report.status="MISMATCH",
    (r:any)=>r.report.promotion_allowed=0,(r:any)=>r.report.promotion_allowed=true,
    (r:any)=>r.report.expected_model_sha256="0".repeat(64),(r:any)=>r.report.expected_source_hashes.extra="0".repeat(64),
    (r:any)=>r.input_hash="0".repeat(64),(r:any)=>r.registration_content_hash="0".repeat(64),
    (r:any)=>r.input={snapshots:[]},(r:any)=>r.data_mode="DEMO",
  ]){const value=structuredClone(matched);mutate(value);expect(trainingEvidenceResponseSchema.safeParse(value).success).toBe(false);}
  const value=structuredClone(mismatch);value.report.status="MATCHED";
  expect(trainingEvidenceResponseSchema.safeParse(value).success).toBe(false);
});
it("validates stored-integrity pages and strict HTTP query spellings",()=>{
  const page=JSON.parse(readFileSync(new URL("../fixtures/training-evidence-page.json",import.meta.url),"utf8"));
  expect(trainingEvidencePageSchema.parse(page)).toEqual(page);
  for(const mutate of [
    (p:typeof page)=>p.items[0].verification="full-retraining-exact-artifact-comparison",
    (p:typeof page)=>p.items[0].qualification="QUALIFIED",
    (p:typeof page)=>p.items[0].content_hash="0".repeat(64),
    (p:typeof page)=>p.items[0].input={private:true},
    (p:typeof page)=>p.next_cursor=page.items[0].id,
    (p:typeof page)=>p.items.push(p.items[0]),
  ]){const copy=structuredClone(page);mutate(copy);expect(trainingEvidencePageSchema.safeParse(copy).success).toBe(false);}
  expect(trainingEvidenceQuerySchema.parse({limit:"50"})).toEqual({limit:50});
  for(const query of [{limit:1},{limit:"0"},{limit:"51"},{limit:"01"},{limit:["1"]},{data_mode:"DEMO"},{cursor:"bad"}])expect(trainingEvidenceQuerySchema.safeParse(query).success).toBe(false);
});
