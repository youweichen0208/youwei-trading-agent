import {readFileSync} from "node:fs";
import {it,expect} from "vitest";
import {backtestResponseSchema} from "../src/backtest.js";
const fixture=(name:string)=>JSON.parse(readFileSync(new URL(`../fixtures/${name}.json`,import.meta.url),"utf8"));
const saved=fixture("backtest-report"),incomplete=fixture("backtest-report-incomplete");
it("preserves complete and incomplete reports and exact decimal text",()=>{
  for(const value of [saved,incomplete])expect(backtestResponseSchema.parse(value)).toEqual(value);
  for(const text of ["0E-12","-0E+4","1E-20","0.9999999999999999999999999999"]){
    const value=structuredClone(saved);value.report.costs.commission_rate=text;
    for(const row of value.report.rows)if(row.episode)row.episode.costs.commission_rate=text;
    expect(backtestResponseSchema.safeParse(value).success).toBe(true);
  }
});
it("rejects invalid ranges, numeric decimals, injected fields and missing-evidence changes",()=>{
  for(const text of ["1.000000000000000000000000001","1","-1E-20","NaN","1eBAD","1e9999999",0.5]){
    const value=structuredClone(saved);value.report.costs.commission_rate=text;
    expect(backtestResponseSchema.safeParse(value).success).toBe(false);
  }
  for(const mutate of [
    (r:any)=>{r.report.signal_sources.reverse();},
    (r:any)=>{r.report.rows[1].overlaps_signal_id=r.report.rows[1].signal_id;},
    (r:any)=>{r.report.computed_count=true;},
    (r:any)=>{r.report.data_mode="LIVE_RESEARCH";},
    (r:any)=>{r.report.realized_equity_curve=[];},
    (r:any)=>{r.input={secret:"private"};},
    (r:any)=>{r.code_sha256.extra="0".repeat(64);},
  ]){const value=structuredClone(saved);mutate(value);expect(backtestResponseSchema.safeParse(value).success).toBe(false);}
  const value=structuredClone(incomplete);value.report.strategy_net_return="0";
  expect(backtestResponseSchema.safeParse(value).success).toBe(false);
});

it("keeps catalog state counts, cursor, models and qualification consistent",async()=>{
  const {backtestPageSchema,backtestQuerySchema}=await import("../src/backtest.js");
  const page=fixture("backtest-page");expect(backtestPageSchema.parse(page)).toEqual(page);
  expect(backtestQuerySchema.parse({limit:"2",status:"INCOMPLETE"})).toEqual({limit:2,status:"INCOMPLETE"});
  for(const query of [{limit:"0"},{limit:"51"},{limit:["1","2"]},{status:"EMPTY"},{data_mode:"DEMO"},{cursor:"bad"}])
    expect(backtestQuerySchema.safeParse(query).success).toBe(false);
  for(const mutate of [
    (p:any)=>{p.items.push(p.items[0]);},(p:any)=>{p.next_cursor=p.items[0].id;},
    (p:any)=>{p.items[0].qualification="APPROVED";},(p:any)=>{p.items[0].computed_count=500;},
    (p:any)=>{p.items[0].model_ids.push(p.items[0].model_ids[0]);},(p:any)=>{p.items[0].verification="native-replayed";},
  ]){const value=structuredClone(page);mutate(value);expect(backtestPageSchema.safeParse(value).success).toBe(false);}
});
