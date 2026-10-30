import {z} from "zod";
import {experimentalModelIdSchema} from "./requests.js";
const hash=z.string().regex(/^[a-f0-9]{64}$/),mode=z.enum(["REPLAY","DEMO","LIVE_RESEARCH"]);
const fields=["schema_version","status","library_version","symbol","data_mode","horizon_sessions","training_cutoff","calendar_version","config","dataset_hash","split_config","fold","source_hashes","model_sha256","model_text","test_predictions","test_mae","zero_return_mae","probability_up"] as const;
const field=z.enum(fields),sources=z.record(z.string(),hash);
export const trainingEvidenceIdSchema=z.string().regex(/^training-evidence-[a-f0-9]{64}$/);
function sameSources(a:Record<string,string>,b:Record<string,string>){return Object.keys(a).length===Object.keys(b).length && Object.entries(a).every(([k,v])=>Object.hasOwn(b,k) && b[k]===v);}
export const trainingReplayReportSchema=z.strictObject({
  schema_version:z.literal("training-replay-v1"),status:z.enum(["MATCHED","MISMATCH"]),qualification:z.literal("UNVERIFIED"),promotion_allowed:z.literal(false),
  verification:z.literal("full-retraining-exact-artifact-comparison"),expected_data_mode:mode,replayed_data_mode:mode,input_hash:hash,
  expected_artifact_content_hash:hash,replayed_artifact_content_hash:hash,expected_model_sha256:hash,replayed_model_sha256:hash,
  expected_source_hashes:sources,replayed_source_hashes:sources,matched_fields:z.array(field).max(19),mismatched_fields:z.array(field).max(19),
  verifier_code_sha256:hash,content_hash:hash,
}).refine(r=>{
  const all=[...r.matched_fields,...r.mismatched_fields];
  if(all.length!==fields.length || new Set(all).size!==fields.length
    || !r.matched_fields.every((v,i)=>i===0 || v>r.matched_fields[i-1]!)
    || !r.mismatched_fields.every((v,i)=>i===0 || v>r.mismatched_fields[i-1]!))return false;
  const matched=r.mismatched_fields.length===0;
  const equalities={data_mode:r.expected_data_mode===r.replayed_data_mode,model_sha256:r.expected_model_sha256===r.replayed_model_sha256,
    model_text:r.expected_model_sha256===r.replayed_model_sha256,source_hashes:sameSources(r.expected_source_hashes,r.replayed_source_hashes)};
  return (r.status==="MATCHED")===matched && (r.expected_artifact_content_hash===r.replayed_artifact_content_hash)===matched
    && Object.entries(equalities).every(([key,equal])=>equal===r.matched_fields.includes(key as keyof typeof equalities));
},{message:"Training comparison field partition or identity differs"});
export const trainingEvidenceResponseSchema=z.strictObject({id:trainingEvidenceIdSchema,content_hash:hash,model_id:experimentalModelIdSchema,data_mode:mode,
  registration_content_hash:hash,artifact_file_sha256:hash,input_hash:hash,report:trainingReplayReportSchema,
}).refine(r=>r.id==="training-evidence-"+r.content_hash && r.model_id==="lightgbm-experimental-"+r.registration_content_hash
  && r.input_hash===r.report.input_hash && r.data_mode===r.report.expected_data_mode && r.data_mode===r.report.replayed_data_mode);
export type TrainingEvidenceResponse=z.infer<typeof trainingEvidenceResponseSchema>;

export const trainingEvidenceQuerySchema=z.strictObject({
  cursor:trainingEvidenceIdSchema.optional(),
  limit:z.string().regex(/^(?:[1-9]|[1-4][0-9]|50)$/).transform(Number).optional(),
});
export const trainingEvidenceSummarySchema=z.strictObject({
  id:trainingEvidenceIdSchema,model_id:experimentalModelIdSchema,data_mode:mode,
  content_hash:hash,input_hash:hash,status:z.enum(["MATCHED","MISMATCH"]),
  registered_at:z.iso.datetime({offset:true}),qualification:z.literal("UNVERIFIED"),
  verification:z.literal("stored-integrity-checked-retraining-on-detail"),
}).refine(r=>r.id==="training-evidence-"+r.content_hash);
export const trainingEvidencePageSchema=z.strictObject({
  items:z.array(trainingEvidenceSummarySchema).max(50),next_cursor:trainingEvidenceIdSchema.nullable(),
}).refine(p=>new Set(p.items.map(r=>r.id)).size===p.items.length
  && (p.next_cursor===null || p.next_cursor===p.items.at(-1)?.id));
export type TrainingEvidencePage=z.infer<typeof trainingEvidencePageSchema>;
