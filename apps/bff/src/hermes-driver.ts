import {setTimeout as delay} from "node:timers/promises";
import {ApiError} from "./errors.js";
import {admitHermesRun} from "./hermes-admission.js";
import type {HermesClient} from "./hermes-client.js";
import type {HermesStore} from "./hermes-store.js";
import {ConfirmedRunCancellation,type ResearchDriver} from "./research-runner.js";
import type {Run,EventBody} from "./research-store.js";

type Emit=(body:EventBody)=>Promise<unknown>;
// Produce validated events; the store commits the entire completion atomically.
type Completion=(run:Run,status:Awaited<ReturnType<HermesClient["status"]>>)=>Promise<EventBody[]>;
export class HermesResearchDriver implements ResearchDriver {
  readonly runtime="hermes" as const;
  readonly cancellationPolicy="confirmed-upstream" as const;
  constructor(private store:HermesStore,private client:HermesClient,private model:string,private complete:Completion,
    private prepare?:(run:Run,signal:AbortSignal)=>Promise<string>){ }
  private unknown(){return new ApiError("EXECUTION_UNKNOWN","Hermes run requires reconciliation; prompt was not resubmitted",503);}
  async execute(run:Run,_emit:Emit,signal:AbortSignal){
    const saved=await this.store.get(run);
    const id=saved?.upstreamRunId??(saved?null:await admitHermesRun(this.store,this.client,run,this.model,
      this.prepare?await this.prepare(run,signal):run.prompt,signal));
    if(!id)throw this.unknown();
    return this.observe(run,id,signal);
  }
  async resume(run:Run,_emit:Emit,signal:AbortSignal){
    const saved=await this.store.get(run);
    if(!saved?.upstreamRunId)throw this.unknown();
    return this.observe(run,saved.upstreamRunId,signal);
  }
  private async observe(run:Run,id:string,signal:AbortSignal){
    // This also rejects credential/origin rotation before any network I/O.
    let checkpoint=await this.store.checkpoint(run,this.client.runtimeIdentity);
    if(checkpoint.applicationTerminal==="run.completed")return;
    if(checkpoint.applicationTerminal)throw this.unknown();
    try{
      signal.throwIfAborted();
      if(!checkpoint.terminal){
        await this.client.events(id,checkpoint.cursor,async event=>{await this.store.ingest(run,this.client.runtimeIdentity,event);},signal);
        checkpoint=await this.store.checkpoint(run,this.client.runtimeIdentity);
      }
      const state=await this.client.status(id,signal);
      if(state.status!==checkpoint.terminal)throw this.unknown();
      if(state.status==="cancelled")throw new ConfirmedRunCancellation();
      if(state.status==="failed")throw new ApiError("RESEARCH_FAILED","Hermes research failed",502);
      if(state.status!=="completed"||checkpoint.active)throw this.unknown();
      const bodies=await this.complete(run,state);signal.throwIfAborted();
      await this.store.finish(run,this.client.runtimeIdentity,bodies);
    }catch(error){
      if(error instanceof ConfirmedRunCancellation)throw error;
      if(signal.aborted){
        const reconciliation=AbortSignal.timeout(5000);
        try{
          await this.client.stop(id,reconciliation);
          while(!reconciliation.aborted){
            const state=await this.client.status(id,reconciliation);
            if(state.status==="cancelled")throw new ConfirmedRunCancellation();
            if(["completed","failed","interrupted","execution_unknown"].includes(state.status))throw this.unknown();
            await delay(100,undefined,{signal:reconciliation});
          }
        }catch(stopped){if(stopped instanceof ConfirmedRunCancellation)throw stopped;}
        throw this.unknown();
      }
      if(error instanceof ApiError && error.code==="RESEARCH_FAILED")throw error;
      throw this.unknown();
    }
  }
}
