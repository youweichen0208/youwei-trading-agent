import type {Pool,PoolClient} from "pg";
import {z} from "zod";
import {transaction,lock} from "./database.js";
import {hashContent} from "./experimental-report.js";
import {ApiError} from "./errors.js";
import {ResearchRepository,terminalTypes,type Run,type EventBody} from "./research-store.js";
import {hermesObservationSchema,replayHermesEvents} from "./hermes-events.js";
import {agentEventSchema} from "@yta/contracts";

const requestBody=z.strictObject({input:z.string().min(1).max(32000),model:z.string().min(1).max(200)});
const runtime=z.string().regex(/^[a-f0-9]{64}$/);
export type HermesRequest={requestKey:string;sessionKey:string;prompt:string;model:string;runtimeHash:string;upstreamRunId:string|null};

/** Private immutable send intent and receipt; an existing intent must never be resubmitted. */
export class HermesStore {
  constructor(private pool:Pool,private mode:Run["data_mode"]){ }
  private async requireRun(c:PoolClient,run:Run){
    const result=await c.query("SELECT r.*,s.data_mode,s.profile_id FROM yta.research_runs r JOIN yta.research_sessions s ON s.id=r.session_id WHERE r.id=$1 AND r.owner_id=$2 AND s.data_mode=$3",[run.id,run.owner_id,this.mode]);
    const actual=result.rows[0];
    if(!actual || actual.session_id!==run.session_id || actual.data_mode!==run.data_mode || actual.profile_id!==run.profile_id || actual.prompt!==run.prompt)throw new ApiError("NOT_FOUND","Research run not found",404);
  }
  private async saved(c:PoolClient,run:Run):Promise<HermesRequest|null>{
    const result=await c.query("SELECT q.*,s.session_key,p.upstream_run_id FROM yta.hermes_run_requests q JOIN yta.hermes_session_bindings s ON s.session_id=q.session_id LEFT JOIN yta.hermes_run_receipts p ON p.run_id=q.run_id WHERE q.run_id=$1",[run.id]);
    const row=result.rows[0];if(!row)return null;
    const body=requestBody.safeParse(row.body);
    if(!body.success||hashContent(body.data)!==row.request_hash||row.request_key!==`yta-run-${run.id}`||row.session_key!==`yta-session-${run.session_id}`||row.owner_id!==run.owner_id||row.session_id!==run.session_id)throw new ApiError("HERMES_BINDING_INVALID","Stored Hermes request is invalid",503);
    return {requestKey:row.request_key,sessionKey:row.session_key,prompt:body.data.input,model:body.data.model,runtimeHash:row.runtime_hash,upstreamRunId:row.upstream_run_id??null};
  }
  async get(run:Run){return transaction(this.pool,async c=>{await this.requireRun(c,run);return this.saved(c,run);});}
  async claim(run:Run,runtimeHash:string,body:z.infer<typeof requestBody>){
    runtime.parse(runtimeHash);body=requestBody.parse(body);
    return transaction(this.pool,async c=>{
      await lock(c,`hermes-session:${run.session_id}`);await this.requireRun(c,run);
      await c.query("INSERT INTO yta.hermes_session_bindings(session_id,owner_id,runtime_hash,session_key) VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING",[run.session_id,run.owner_id,runtimeHash,`yta-session-${run.session_id}`]);
      const session=(await c.query("SELECT runtime_hash FROM yta.hermes_session_bindings WHERE session_id=$1",[run.session_id])).rows[0];
      if(session.runtime_hash!==runtimeHash)throw new ApiError("HERMES_SCOPE_CHANGED","Hermes runtime or credential scope changed; reconciliation required",409);
      const existing=await this.saved(c,run);
      if(existing){
        if(existing.runtimeHash!==runtimeHash||existing.prompt!==body.input||existing.model!==body.model)throw new ApiError("IDEMPOTENCY_CONFLICT","Hermes request identity has different content",409);
        return {send:false,request:existing};
      }
      await c.query("INSERT INTO yta.hermes_run_requests(run_id,session_id,owner_id,runtime_hash,request_key,request_hash,body) VALUES($1,$2,$3,$4,$5,$6,$7)",[run.id,run.session_id,run.owner_id,runtimeHash,`yta-run-${run.id}`,hashContent(body),body]);
      await c.query("INSERT INTO yta.audit_events(actor_id,action,resource_id,payload) VALUES($1,'hermes.request_claimed',$2,$3)",[run.owner_id,run.id,{request_hash:hashContent(body),runtime_hash:runtimeHash}]);
      return {send:true,request:(await this.saved(c,run))!};
    });
  }
  private async observations(c:PoolClient,run:Run,runtimeHash:string){
    await this.requireRun(c,run);const request=await this.saved(c,run);
    if(!request?.upstreamRunId||request.runtimeHash!==runtimeHash)throw new ApiError("EXECUTION_UNKNOWN","Hermes receipt or runtime scope unavailable",503);
    const rows=(await c.query("SELECT h.*,a.event AS public_event FROM yta.hermes_run_events h LEFT JOIN yta.agent_events a ON a.event_id=h.public_event_id WHERE h.run_id=$1 ORDER BY h.sequence LIMIT 10001",[run.id])).rows;
    if(rows.length>10000)throw new ApiError("HERMES_EVENT_INVALID","Hermes event limit exceeded",503);
    const observations=rows.map(row=>{
      const value=hermesObservationSchema.parse(row.observation);
      if(value.seq!==row.sequence||value.run_id!==request.upstreamRunId||hashContent(value)!==row.content_hash||hashContent(row.observation)!==row.content_hash)throw new ApiError("HERMES_EVENT_INVALID","Stored Hermes event differs",503);
      return value;
    });
    const state=replayHermesEvents(observations);
    const canceled=Boolean((await c.query("SELECT 1 FROM yta.run_cancellations WHERE run_id=$1",[run.id])).rowCount);
    rows.forEach((row,index)=>{
      const body=state.projections[index];
      if(row.public_event_id){
        const event=agentEventSchema.parse(row.public_event);
        if(!body||event.run_id!==run.id||event.session_id!==run.session_id||hashContent({type:event.type,payload:event.payload})!==hashContent(body))throw new ApiError("HERMES_EVENT_INVALID","Public Hermes projection differs",503);
      }else if(body&&!canceled)throw new ApiError("HERMES_EVENT_INVALID","Missing Hermes public projection",503);
    });
    return {request,rows,observations,state};
  }
  async checkpoint(run:Run,runtimeHash:string){
    return transaction(this.pool,async c=>{
      const {state,request}=await this.observations(c,run,runtimeHash);
      const terminal=await c.query("SELECT type FROM yta.agent_events WHERE run_id=$1 AND type=ANY($2::text[])",[run.id,terminalTypes]);
      return {cursor:state.cursor,terminal:state.terminal,active:state.active,upstreamRunId:request.upstreamRunId!,applicationTerminal:terminal.rows[0]?.type as string|undefined};
    });
  }
  async ingest(run:Run,runtimeHash:string,source:unknown){
    const observation=hermesObservationSchema.parse(source),digest=hashContent(observation);
    return transaction(this.pool,async c=>{
      await lock(c,`hermes-session:${run.session_id}`);
      const saved=await this.observations(c,run,runtimeHash);
      if(observation.run_id!==saved.request.upstreamRunId)throw new ApiError("HERMES_EVENT_INVALID","Hermes run identity differs",503);
      const prior=saved.rows[observation.seq];
      if(prior){if(prior.content_hash!==digest)throw new ApiError("HERMES_EVENT_INVALID","Replayed event changed",503);return false;}
      const state=replayHermesEvents([...saved.observations,observation]);
      await lock(c,`events:${run.id}`);
      const terminal=await c.query("SELECT 1 FROM yta.agent_events WHERE run_id=$1 AND type=ANY($2::text[])",[run.id,terminalTypes]);
      if(terminal.rowCount)throw new ApiError("EXECUTION_UNKNOWN","Application run is already terminal",503);
      const body=state.projections.at(-1),repo=new ResearchRepository(this.pool,this.mode);
      const event=body?await repo.appendWithin(c,run,body):null;
      await c.query("INSERT INTO yta.hermes_run_events(run_id,sequence,observation,content_hash,public_event_id) VALUES($1,$2,$3,$4,$5)",[run.id,observation.seq,observation,digest,event?.event_id??null]);
      return true;
    });
  }
  async finish(run:Run,runtimeHash:string,bodies:EventBody[]){
    return transaction(this.pool,async c=>{
      await lock(c,`hermes-session:${run.session_id}`);
      const {state}=await this.observations(c,run,runtimeHash);
      if(state.terminal!=="completed"||state.active)throw new ApiError("EXECUTION_UNKNOWN","Hermes completion is not verified",503);
      await lock(c,`events:${run.id}`);
      const prior=await c.query("SELECT 1 FROM yta.agent_events WHERE run_id=$1 AND type=ANY($2::text[])",[run.id,terminalTypes]);
      if(prior.rowCount)return;
      const repo=new ResearchRepository(this.pool,this.mode);
      for(const body of bodies){
        if(!["message.completed","prediction.draft","canvas.command"].includes(body.type))throw new ApiError("RESEARCH_REPORT_INVALID","Invalid completion event",502);
        await repo.appendWithin(c,run,body);
      }
      await repo.appendWithin(c,run,{type:"run.completed",payload:{}},true);
    });
  }
  async receipt(run:Run,runtimeHash:string,upstreamRunId:string){
    runtime.parse(runtimeHash);z.string().regex(/^run_[a-f0-9]{32}$/).parse(upstreamRunId);
    return transaction(this.pool,async c=>{
      await lock(c,`hermes-session:${run.session_id}`);await this.requireRun(c,run);
      const request=await this.saved(c,run);
      if(!request||request.runtimeHash!==runtimeHash)throw new ApiError("HERMES_BINDING_INVALID","Hermes request scope differs",409);
      const inserted=await c.query("INSERT INTO yta.hermes_run_receipts(run_id,upstream_run_id) VALUES($1,$2) ON CONFLICT DO NOTHING RETURNING run_id",[run.id,upstreamRunId]);
      const result=(await this.saved(c,run))!;
      if(result.upstreamRunId!==upstreamRunId)throw new ApiError("HERMES_BINDING_CONFLICT","Hermes receipt conflicts",409);
      if(inserted.rowCount)await c.query("INSERT INTO yta.audit_events(actor_id,action,resource_id,payload) VALUES($1,'hermes.receipt_recorded',$2,$3)",[run.owner_id,run.id,{upstream_run_id:upstreamRunId}]);
      return result;
    });
  }
}
