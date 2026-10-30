import {ApiError} from "./errors.js";
import type {HermesClient} from "./hermes-client.js";
import type {HermesStore} from "./hermes-store.js";
import type {Run} from "./research-store.js";

/** Commit send intent before network I/O. A lost receipt never authorizes another POST. */
export async function admitHermesRun(store:HermesStore,client:HermesClient,run:Run,model:string,prompt:string,signal?:AbortSignal){
  signal?.throwIfAborted();
  const claim=await store.claim(run,client.runtimeIdentity,{input:prompt,model});
  if(!claim.send){
    if(claim.request.upstreamRunId)return claim.request.upstreamRunId;
    throw new ApiError("EXECUTION_UNKNOWN","Hermes submission was already claimed; reconcile the original request",503);
  }
  try{
    const admitted=await client.submit(claim.request,signal);
    await store.receipt(run,client.runtimeIdentity,admitted.run_id);
    return admitted.run_id;
  }catch{
    // Includes database failure after upstream acceptance: intent remains committed.
    throw new ApiError("EXECUTION_UNKNOWN","Hermes admission outcome is unknown; do not resubmit",503);
  }
}
