import {readFileSync} from "node:fs";
import {it,expect} from "vitest";
import {readModelReviewPage,readModelReview} from "../src/model-review.js";
import {hashContent} from "../src/experimental-report.js";
const saved=JSON.parse(readFileSync(new URL("../../../packages/contracts/fixtures/model-review.json",import.meta.url),"utf8"));
const row=saved.review;
const request={proposal_id:row.proposal_id,assessment_id:row.assessment_id,action:row.action,reason:row.reason,expected_previous_review_id:row.previous_review_id};
it("preserves fixed Python review identity and exact submitted actor/key/body",()=>{
  expect(readModelReview(saved,"REPLAY",{id:saved.id})).toEqual(saved);
  expect(readModelReview(saved,"REPLAY",{reviewer:row.reviewer_id,key:row.request_id,request})).toEqual(saved);
});
it("rejects review authority, request, evidence and content substitutions",()=>{
  for(const change of [{promotion_allowed:true},{disposition:"APPROVED"},{revision:2},{reason:"changed"},{reviewer_id:row.request_id},{previous_review_id:row.request_id}]) {
    const {content_hash:_,...body}={...row,...change};
    expect(()=>readModelReview({...saved,review:{...body,content_hash:hashContent(body)}},"REPLAY",{reviewer:row.reviewer_id,key:row.request_id,request})).toThrow();
  }
  expect(()=>readModelReview(saved,"LIVE_RESEARCH",{})).toThrow();
  expect(()=>readModelReview(saved,"REPLAY",{id:row.request_id})).toThrow();
  expect(()=>readModelReview(saved,"REPLAY",{key:row.reviewer_id})).toThrow();
  expect(()=>readModelReview({...saved,private:"secret"},"REPLAY",{})).toThrow();
});

it("validates review pages and contiguous newest-first predecessor links",()=>{
  const {content_hash:_,...body}=row;
  const nextBody={...body,revision:2,previous_review_id:saved.id};
  const next={...saved,id:row.request_id,review:{...nextBody,content_hash:hashContent(nextBody)}};
  const page={items:[next,saved],next_cursor:null};
  expect(readModelReviewPage(page,"REPLAY",row.proposal_id,{})).toEqual(page);
  expect(readModelReviewPage({items:[next],next_cursor:next.id},"REPLAY",row.proposal_id,{limit:1}).items).toEqual([next]);
  expect(readModelReviewPage({items:[],next_cursor:null},"REPLAY",row.proposal_id,{}).items).toEqual([]);
  for(const invalid of [{...page,items:[saved,next]}, {...page,items:[saved,saved]},
    {...page,next_cursor:saved.id},{items:[next],next_cursor:null},{items:[],next_cursor:saved.id},
    {items:[next],next_cursor:next.id}]) {
    expect(()=>readModelReviewPage(invalid,"REPLAY",row.proposal_id,{})).toThrow();
  }
  expect(()=>readModelReviewPage(page,"REPLAY",row.request_id,{})).toThrow();
  expect(()=>readModelReviewPage(page,"REPLAY",row.proposal_id,{limit:1})).toThrow();
  expect(()=>readModelReviewPage(page,"REPLAY",row.proposal_id,{cursor:next.id})).toThrow();
  expect(()=>readModelReviewPage(page,"LIVE_RESEARCH",row.proposal_id,{})).toThrow();
});

const combined=JSON.parse(readFileSync(new URL("../../../packages/contracts/fixtures/model-review-v2.json",import.meta.url),"utf8"));
it("binds combined review responses to exact gate and rejects cross-version substitution",()=>{
  const r=combined.review,q={proposal_id:r.proposal_id,quality_gate_id:r.quality_gate_id,action:r.action,reason:r.reason,expected_previous_review_id:r.previous_review_id};
  expect(readModelReview(combined,"REPLAY",{request:q,reviewer:r.reviewer_id,key:r.request_id})).toEqual(combined);
  expect(()=>readModelReview(combined,"REPLAY",{request:{...q,quality_gate_id:"model-quality-gate-"+"0".repeat(64)}})).toThrow();
  expect(()=>readModelReview(combined,"REPLAY",{request})).toThrow();
  expect(()=>readModelReview(saved,"REPLAY",{request:q})).toThrow();
  for(const change of [{reason:"substituted"},{promotion_allowed:true},{blocking_reasons:[]},{quality_gate_id:"model-quality-gate-"+"0".repeat(64),quality_gate_content_hash:"0".repeat(64)}]) {
    const {content_hash:_,...body}={...r,...change};
    expect(()=>readModelReview({...combined,review:{...body,content_hash:hashContent(body)}},"REPLAY",{request:q})).toThrow();
  }
});
it("checks predecessor continuity across mixed v1 and v2 pages",()=>{
  const {content_hash:_,...body}=combined.review;
  const nextBody={...body,proposal_id:row.proposal_id,revision:2,previous_review_id:saved.id};
  const next={...combined,review:{...nextBody,content_hash:hashContent(nextBody)}};
  const page={items:[next,saved],next_cursor:null};
  expect(readModelReviewPage(page,"REPLAY",row.proposal_id,{})).toEqual(page);
  expect(()=>readModelReviewPage({...page,items:[saved,next]},"REPLAY",row.proposal_id,{})).toThrow();
  expect(()=>readModelReviewPage(page,"DEMO",row.proposal_id,{})).toThrow();
});
