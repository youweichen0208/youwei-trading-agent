import { readFileSync } from "node:fs";
import { expect, it } from "vitest";
import { modelProposalRequestSchema } from "@yta/contracts";
import { readReviewProposal, readReviewProposalPage, readModelProposalPage, readModelProposal } from "../src/model-proposal.js";
import { hashContent } from "../src/experimental-report.js";

const saved = JSON.parse(readFileSync(new URL("../../../packages/contracts/fixtures/model-proposal.json", import.meta.url), "utf8"));
const p = saved.proposal;
const request = modelProposalRequestSchema.parse({candidate_evaluation_id:p.candidate_evaluation_id,
  reference_evaluation_id:p.reference_evaluation_id,rationale:p.rationale});
it("preserves registered Python proposal text and content hash across Unicode boundaries", () => {
  expect(readModelProposal(saved,p.owner_id,"REPLAY",{id:saved.id})).toEqual(saved);
  expect(readModelProposal(saved,p.owner_id,"REPLAY",{key:p.request_id,request})).toEqual(saved);
});
it("refuses wrong actor, mode, request, content, status or invented reference bindings", () => {
  for (const changes of [{owner_id:"00000000-0000-4000-8000-000000000001"}, {status:"APPROVED"},
    {data_mode:"LIVE_RESEARCH"}, {reference_report_hash:"0".repeat(64)}, {rationale:"changed"}]) {
    const proposal={...p,...changes};
    expect(() => readModelProposal({...saved,proposal,content_hash:hashContent(proposal)},p.owner_id,"REPLAY",{key:p.request_id,request})).toThrow();
  }
  expect(() => readModelProposal({...saved,content_hash:"0".repeat(64)},p.owner_id,"REPLAY",{})).toThrow();
  expect(() => readModelProposal({...saved,private:"secret"},p.owner_id,"REPLAY",{})).toThrow();
  expect(() => readModelProposal(saved,p.owner_id,"REPLAY",{id:"00000000-0000-4000-8000-000000000001"})).toThrow();
  expect(() => readModelProposal(saved,p.owner_id,"REPLAY",{key:"00000000-0000-4000-8000-000000000001"})).toThrow();
});

it("checks proposal history scope, hashes, page bounds and cursor progress", () => {
  const page={items:[saved],next_cursor:saved.id};
  expect(readModelProposalPage(page,p.owner_id,"REPLAY",{limit:1})).toEqual(page);
  expect(readModelProposalPage({items:[],next_cursor:null},p.owner_id,"REPLAY",{}).items).toEqual([]);
  for(const value of [{...page,items:[saved,saved]}, {...page,next_cursor:p.owner_id},
    {items:[],next_cursor:saved.id}, {...page,private:"secret"},
    {...page,items:[{...saved,content_hash:"0".repeat(64)}]}]) {
    expect(() => readModelProposalPage(value,p.owner_id,"REPLAY",{})).toThrow();
  }
  expect(() => readModelProposalPage(page,p.owner_id,"REPLAY",{cursor:saved.id})).toThrow();
  expect(() => readModelProposalPage(page,p.owner_id,"LIVE_RESEARCH",{})).toThrow();
  expect(() => readModelProposalPage(page,p.request_id,"REPLAY",{})).toThrow();
  const second={...saved,id:p.request_id};
  expect(() => readModelProposalPage({items:[saved,second],next_cursor:null},p.owner_id,"REPLAY",{limit:1})).toThrow();
});

it("privileged proposal discovery preserves evidence without requiring the reviewer to own it",()=>{
  const otherBody={...p,owner_id:p.request_id};
  const other={...saved,id:p.request_id,proposal:otherBody,content_hash:hashContent(otherBody)};
  const page={items:[saved,other],next_cursor:null};
  expect(readReviewProposalPage(page,"REPLAY",{})).toEqual(page);
  expect(readReviewProposal(other,"REPLAY",other.id)).toEqual(other);
  expect(()=>readModelProposal(other,p.owner_id,"REPLAY",{})).toThrow();
  expect(()=>readReviewProposal(other,"LIVE_RESEARCH")).toThrow();
  expect(()=>readReviewProposal(other,"REPLAY",saved.id)).toThrow();
  for(const value of [{...page,items:[saved,saved]}, {...page,next_cursor:saved.id},
    {...page,items:[{...saved,content_hash:"0".repeat(64)}]}, {...page,private:true}]) {
    expect(()=>readReviewProposalPage(value,"REPLAY",{})).toThrow();
  }
  expect(()=>readReviewProposalPage(page,"REPLAY",{limit:1})).toThrow();
  expect(()=>readReviewProposalPage(page,"REPLAY",{cursor:saved.id})).toThrow();
});
