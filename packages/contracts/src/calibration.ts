import {z} from "zod";
import {experimentalCalibrationIdSchema,experimentalModelIdSchema} from "./requests.js";
const hash=z.string().regex(/^[a-f0-9]{64}$/),timestamp=z.iso.datetime({offset:true});
const decimal=z.string().regex(/^-?[0-9]+(?:\.[0-9]+)?(?:[Ee][+-]?[0-9]{1,6})?$/);
// Exact lexical range check, preserving Decimal's scientific notation and signed zero.
function unitInterval(text:string) {
  if(!/^-?[0-9]+(?:\.[0-9]+)?(?:[Ee][+-]?[0-9]{1,6})?$/.test(text))return false;
  const [mantissa,exponent="0"]=text.toLowerCase().split("e");
  const [integer,fraction=""]=mantissa!.replace(/^-/ ,"").split(".");
  const all=integer!+fraction,leading=all.search(/[1-9]/);
  if(leading<0)return true;
  if(mantissa!.startsWith("-"))return false;
  const position=BigInt(integer!.length-leading)+BigInt(exponent),digits=all.slice(leading);
  return position<=0n || (position===1n && /^10*$/.test(digits));
}
const probability=decimal.refine(unitInterval);
const count=z.number().int().nonnegative().safe(),target=z.union([z.literal(0),z.literal(1)]);
const bin=z.strictObject({lower:probability,upper:probability,upper_inclusive:z.boolean(),count,
  mean_probability:probability.nullable(),up_frequency:probability.nullable(),
}).refine(row=>row.count===0 ? row.mean_probability===null && row.up_frequency===null : row.mean_probability!==null && row.up_frequency!==null);
const reliability=z.strictObject({sample_count:count,up_count:count,bins:z.array(bin).length(10),brier:probability.nullable(),ece:probability.nullable(),
}).refine(row=>row.up_count<=row.sample_count && row.bins.reduce((sum,item)=>sum+item.count,0)===row.sample_count
  && row.bins.every((item,index)=>item.lower===(index===0 ? "0" : `0.${index}`)
    && item.upper===(index===9 ? "1" : `0.${index+1}`) && item.upper_inclusive===(index===9))
  && (row.sample_count===0 ? row.brier===null && row.ece===null : row.brier!==null && row.ece!==null));
export const calibrationReportSchema=z.strictObject({
  schema_version:z.literal("calibration-report-v1"),status:z.literal("EXPERIMENTAL"),qualification:z.literal("UNVERIFIED"),
  method:z.literal("isotonic-linear-decimal-v1"),positive_class:z.literal("UP; FLAT and DOWN are 0"),out_of_bounds:z.literal("endpoint_probability"),
  model_artifact_hash:hash,model_sha256:hash,data_mode:z.enum(["REPLAY","DEMO","LIVE_RESEARCH"]),
  horizon_sessions:z.union([z.literal(1),z.literal(3),z.literal(5),z.literal(10),z.literal(20)]),calendar_version:z.string(),
  calibration_ids:z.array(hash).min(1),evaluation_ids:z.array(hash).min(1),calibration_available_at:timestamp,cutoff:timestamp,
  embargo_sessions:count,knots:z.array(z.tuple([decimal,probability])).min(1),probabilities:z.array(probability),
  calibration_scores:z.array(decimal),calibration_targets:z.array(target),evaluation_targets:z.array(target),code_sha256:hash,reliability,
}).refine(row=>new Set(row.calibration_ids).size===row.calibration_ids.length
  && new Set(row.evaluation_ids).size===row.evaluation_ids.length
  && row.evaluation_ids.every(id=>!row.calibration_ids.includes(id))
  && row.calibration_scores.length===row.calibration_ids.length && row.calibration_targets.length===row.calibration_ids.length
  && row.probabilities.length===row.evaluation_ids.length && row.evaluation_targets.length===row.evaluation_ids.length
  && row.reliability.sample_count===row.evaluation_ids.length
  && row.reliability.up_count===row.evaluation_targets.filter(value=>value===1).length);
export const calibrationReportResponseSchema=z.strictObject({id:experimentalCalibrationIdSchema,model_id:experimentalModelIdSchema,
  registered_at:timestamp,content_hash:hash,report_hash:hash,report:calibrationReportSchema,
}).refine(row=>row.id==="calibration-experimental-"+row.content_hash);
export type CalibrationReportResponse=z.infer<typeof calibrationReportResponseSchema>;
