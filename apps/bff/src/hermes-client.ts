import {createHash} from "node:crypto";
import {z} from "zod";
import {ApiError} from "./errors.js";

const runId=z.string().regex(/^run_[a-f0-9]{32}$/);
const key=z.string().min(1).max(128).regex(/^[A-Za-z0-9._:-]+$/);
const admission=z.object({run_id:runId});
const statusSchema=z.object({run_id:runId,status:z.enum(["queued","running","interrupted","stopping","completed","failed","cancelled","execution_unknown"])}).passthrough();
const eventSchema=z.object({run_id:runId,event:z.string().min(1).max(100),seq:z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER)}).passthrough();
export type HermesEvent=z.infer<typeof eventSchema>;
export type HermesConnection={origin:string;token:string;profile:"alpha-orchestrator";timeoutMs?:number;maxBytes?:number};

/** Private transport for the locked Runs protocol. No retry or execution authorization. */
export class HermesClient {
  readonly runtimeIdentity:string;
  private readonly origin:string;
  private readonly timeout:number;
  private readonly limit:number;
  constructor(private readonly connection:HermesConnection){
    const origin=new URL(connection.origin);
    if(!["http:","https:"].includes(origin.protocol) || origin.username || origin.password || origin.search || origin.hash || origin.pathname!=="/"
      || connection.profile!=="alpha-orchestrator" || !connection.token || /[\r\n]/.test(connection.token))throw new Error("Invalid private Hermes connection");
    this.runtimeIdentity=createHash("sha256").update(JSON.stringify({origin:origin.origin,profile:connection.profile,credential:connection.token})).digest("hex");
    this.origin=origin.origin;this.timeout=connection.timeoutMs??15000;this.limit=connection.maxBytes??4_000_000;
    if(!Number.isSafeInteger(this.timeout)||this.timeout<1||!Number.isSafeInteger(this.limit)||this.limit<1)throw new Error("Invalid Hermes transport bounds");
  }
  private failure(write:boolean){return new ApiError(write?"EXECUTION_UNKNOWN":"HERMES_UNAVAILABLE",write?"Hermes execution must be reconciled using the original request identity":"Hermes response unavailable",503);}
  private invalid(){return new ApiError("HERMES_PROTOCOL_INVALID","Hermes protocol response is invalid",502);}
  private async consume<T>(path:string,method:"GET"|"POST",body:unknown,headers:Record<string,string>,signal:AbortSignal|undefined,read:(response:Response)=>Promise<T>):Promise<T>{
    // Caller cancellation and the deadline cover headers AND consumption of the body.
    const abort=AbortSignal.any([AbortSignal.timeout(this.timeout),...(signal?[signal]:[])]);
    const write=method==="POST";
    try{
      const response=await fetch(this.origin+"/p/alpha-orchestrator"+path,{method,redirect:"error",signal:abort,
        headers:{Authorization:"Bearer "+this.connection.token,"Content-Type":"application/json",...headers},
        ...(body===undefined?{}:{body:JSON.stringify(body)})});
      if(!response.ok){await response.body?.cancel();throw this.failure(write);}
      return await read(response);
    }catch(error){
      if(write)throw this.failure(true);
      if(error instanceof ApiError)throw error;
      throw this.failure(false);
    }
  }
  private async json(response:Response):Promise<unknown>{
    const reader=response.body?.getReader();if(!reader)throw this.invalid();
    const decoder=new TextDecoder("utf-8",{fatal:true});let text="",bytes=0;
    try{
      while(true){const part=await reader.read();if(part.done)break;bytes+=part.value.byteLength;if(bytes>this.limit)throw this.invalid();text+=decoder.decode(part.value,{stream:true});}
      text+=decoder.decode();return JSON.parse(text);
    }finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
  }
  async submit(input:{requestKey:string;sessionKey:string;prompt:string;model:string},signal?:AbortSignal){
    const requestKey=key.parse(input.requestKey),sessionKey=key.parse(input.sessionKey);
    const body={input:z.string().min(1).max(32000).parse(input.prompt),model:z.string().min(1).max(200).parse(input.model)};
    return this.consume("/v1/runs","POST",body,{"Idempotency-Key":requestKey,"X-Hermes-Session-Key":sessionKey},signal,async response=>{
      if(response.status!==202)throw this.invalid();
      const value=admission.safeParse(await this.json(response));if(!value.success)throw this.invalid();return value.data;
    });
  }
  async status(identity:string,signal?:AbortSignal){
    const id=runId.parse(identity);
    return this.consume("/v1/runs/"+id,"GET",undefined,{},signal,async response=>{
      const value=statusSchema.safeParse(await this.json(response));
      if(!value.success||value.data.run_id!==id)throw this.invalid();return value.data;
    });
  }
  async stop(identity:string,signal?:AbortSignal){
    const id=runId.parse(identity);
    // A successful acknowledgement is not proof of terminal cancellation.
    return this.consume(`/v1/runs/${id}/stop`,"POST",{}, {},signal,response=>this.json(response));
  }
  async events(identity:string,cursor:number,receive:(event:HermesEvent)=>Promise<void>,signal?:AbortSignal){
    const id=runId.parse(identity);z.number().int().min(-1).max(Number.MAX_SAFE_INTEGER).parse(cursor);
    return this.consume(`/v1/runs/${id}/events`,"GET",undefined,cursor<0?{}:{"Last-Event-ID":String(cursor)},signal,async response=>{
      if(response.headers.get("content-type")?.split(";")[0]?.trim()!=="text/event-stream")throw this.invalid();
      const reader=response.body?.getReader();if(!reader)throw this.invalid();
      const decoder=new TextDecoder("utf-8",{fatal:true});let buffer="",bytes=0,last=cursor;
      const dispatch=async(frame:string)=>{
        const lines=frame.split("\n"),data=lines.filter(s=>s.startsWith("data:")).map(s=>s.slice(5).replace(/^ /,"")).join("\n");
        if(!data)return;
        const value:unknown=JSON.parse(data);
        if(typeof value==="object"&&value!==null&&"event" in value&&value.event==="replay.truncated")throw new ApiError("HERMES_EVENT_GAP","Hermes event history is incomplete; reconcile the saved run",503);
        const parsed=eventSchema.safeParse(value);
        const ids=lines.filter(s=>s.startsWith("id:")).map(s=>s.slice(3).trim());
        if(!parsed.success||parsed.data.run_id!==id||parsed.data.seq!==last+1||ids.length!==1||ids[0]!==String(parsed.data.seq))throw this.invalid();
        await receive(parsed.data);last=parsed.data.seq;
      };
      try{
        while(true){
          const part=await reader.read();if(part.done)break;
          bytes+=part.value.byteLength;if(bytes>this.limit)throw this.invalid();
          buffer+=decoder.decode(part.value,{stream:true});
          // Normalize CRLF only once its LF arrives, including across network chunks.
          buffer=buffer.replace(/\r\n/g,"\n");let boundary;
          while((boundary=buffer.indexOf("\n\n"))>=0){const frame=buffer.slice(0,boundary);buffer=buffer.slice(boundary+2);await dispatch(frame);}
        }
        buffer+=decoder.decode();if(buffer.trim())throw this.invalid();return last;
      }finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
    });
  }
}
