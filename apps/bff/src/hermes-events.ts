import {z} from "zod";
import {ApiError} from "./errors.js";
import type {EventBody} from "./research-store.js";
const base={run_id:z.string().regex(/^run_[a-f0-9]{32}$/),seq:z.number().int().min(0).max(9999)};
const call={tool:z.enum(["mcp__yta_finance__market_snapshot","mcp__yta_finance__indicators"]),tool_call_id:z.string().min(1).max(200)};
// Zod objects strip previews, arbitrary tool results, reasoning text and unknown model fields.
export const hermesObservationSchema=z.discriminatedUnion("event",[
  z.object({...base,event:z.literal("message.delta"),delta:z.string().max(100000)}),
  z.object({...base,event:z.literal("message.interim"),text:z.string().max(100000),already_streamed:z.boolean()}),
  z.object({...base,...call,event:z.literal("tool.started")}),
  z.object({...base,...call,event:z.literal("tool.completed"),error:z.boolean()}),
  ...["run.queued","run.started","reasoning.available","run.completed","run.failed","run.cancelled","run.interrupted"].map(event=>z.object({...base,event:z.literal(event)})),
]);
export type Observation=z.infer<typeof hermesObservationSchema>;
export function replayHermesEvents(events:Observation[]){
  const active=new Map<string,string>(),seen=new Set<string>();let terminal:string|undefined;
  const projections:(EventBody|null)[]=[];
  for(const [index,e] of events.entries()){
    if(e.seq!==index || terminal)throw new ApiError("HERMES_EVENT_GAP","Stored Hermes sequence differs",503);
    let body:EventBody|null=null;
    if(e.event==="message.delta" && 'delta' in e)body={type:"message.delta",payload:{text:e.delta}};
    else if(e.event==="message.interim" && 'already_streamed' in e && !e.already_streamed)body={type:"message.delta",payload:{text:e.text}};
    else if('tool' in e){
      const tool=e.tool==="mcp__yta_finance__market_snapshot"?"market_snapshot":"indicators",id=e.tool_call_id;
      if(e.event==="tool.started"){
        if(seen.has(id))throw new ApiError("HERMES_EVENT_INVALID","Duplicate tool identity",503);
        seen.add(id);active.set(id,tool);body={type:"tool.started",payload:{tool,tool_call_id:id}};
      }else if('error' in e){
        if(active.get(id)!==tool)throw new ApiError("HERMES_EVENT_INVALID","Unmatched tool result",503);
        active.delete(id);body={type:"tool.completed",payload:{tool,tool_call_id:id,success:!e.error}};
      }
    }else if(["run.completed","run.failed","run.cancelled","run.interrupted"].includes(e.event))terminal=e.event.slice(4);
    projections.push(body);
  }
  return {cursor:events.length-1,terminal,active:active.size,projections};
}
