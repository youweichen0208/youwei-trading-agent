import {safeRoute} from "../src/observability.js";
import {readFileSync} from "node:fs";
import {it,expect} from "vitest";
import {readBacktestReport} from "../src/backtest-report.js";
import {hashContent} from "../src/experimental-report.js";
const fixture=(name:string)=>JSON.parse(readFileSync(new URL(`../../../packages/contracts/fixtures/${name}.json`,import.meta.url),"utf8"));
const saved=fixture("backtest-report"),incomplete=fixture("backtest-report-incomplete");
function rehash(value:any){const {content_hash,...body}=value;value.content_hash=hashContent(body);}
it("reads fixed complete and incomplete identities without inventing missing metrics",()=>{
  for(const value of [saved,incomplete])expect(readBacktestReport(value,"REPLAY",value.id)).toEqual(value);
  expect(()=>readBacktestReport(saved,"DEMO",saved.id)).toThrow();
  expect(()=>readBacktestReport(saved,"REPLAY","backtest-report-"+"0".repeat(64))).toThrow();
});
it("checks nested hashes even if the outer report hash has been recomputed",()=>{
  expect(()=>readBacktestReport({...saved,report:{...saved.report,content_hash:"0".repeat(64)}},"REPLAY",saved.id)).toThrow();
  for(const mutate of [
    (r:any)=>{r.report.signal_sources[0].artifact_file_sha256="0".repeat(64);},
    (r:any)=>{r.report.rows[0].episode.strategy_net_return="0";},
  ]){const value=structuredClone(saved);mutate(value);rehash(value.report);expect(()=>readBacktestReport(value,"REPLAY",value.id)).toThrow();}
});

it("redacts backtest identities and query values from known route observations",()=>{
  for(const prefix of ["/api/yta/v1","/internal/v1"])expect(safeRoute(prefix+"/backtest-reports/"+saved.id+"?secret=value")).toBe(prefix+"/backtest-reports/:id");
});

it("binds catalog responses to mode, filters, page size and cursor",async()=>{
  const {readBacktestPage}=await import("../src/backtest-report.js");
  const page=fixture("backtest-page");
  expect(readBacktestPage(page,"REPLAY",{})).toEqual(page);
  for(const query of [{limit:1},{cursor:page.items[0].id},{model_id:"lightgbm-experimental-"+"0".repeat(64)},{status:"COMPLETE" as const}])
    expect(()=>readBacktestPage(page,"REPLAY",query)).toThrow();
  expect(()=>readBacktestPage(page,"DEMO",{})).toThrow();
  expect(readBacktestPage({items:[],next_cursor:null},"REPLAY",{})).toEqual({items:[],next_cursor:null});
});
