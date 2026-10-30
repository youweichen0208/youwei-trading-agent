import {z} from "zod";
import {experimentalModelIdSchema as modelId} from "./requests.js";
const hash=z.string().regex(/^[a-f0-9]{64}$/),time=z.iso.datetime({offset:true}),day=z.iso.date();
const decimal=z.string().regex(/^-?[0-9]+(?:\.[0-9]+)?(?:[Ee][+-]?[0-9]{1,6})?$/);
// Classify exact decimal text without converting financial values to binary floats.
function magnitude(text:string) {
  if(!/^-?[0-9]+(?:\.[0-9]+)?(?:[Ee][+-]?[0-9]{1,6})?$/.test(text))return {sign:NaN,unit:NaN};
  const [mantissa,exponent="0"]=text.toLowerCase().split("e");
  const [integer,fraction=""]=mantissa!.replace(/^-/ ,"").split(".");
  const all=integer!+fraction,leading=all.search(/[1-9]/);
  if(leading<0)return {sign:0,unit:-1};
  const position=BigInt(integer!.length-leading)+BigInt(exponent),digits=all.slice(leading);
  return {sign:mantissa!.startsWith("-")?-1:1,unit:position<1n?-1:position>1n?1:/^10*$/.test(digits)?0:1};
}
const positive=decimal.refine(s=>magnitude(s).sign>0);
const nonnegative=decimal.refine(s=>magnitude(s).sign>=0);
const rate=decimal.refine(s=>magnitude(s).sign>=0 && magnitude(s).unit<0);
const unit=decimal.refine(s=>magnitude(s).sign>=0 && magnitude(s).unit<=0);
const returns=decimal.refine(s=>magnitude(s).sign>=0 || magnitude(s).unit<=0);
const mode=z.enum(["REPLAY","DEMO"]),action=z.enum(["LONG","CASH"]);
const holding=z.union([z.literal(1),z.literal(3),z.literal(5),z.literal(10),z.literal(20)]);
const count=z.number().int().nonnegative().safe();
const costs=z.strictObject({commission_rate:rate,slippage_rate:rate});
const prediction=z.strictObject({status:z.literal("EXPERIMENTAL"),model_kind:z.literal("TRAINED"),model_id:modelId,
  model_sha256:hash,data_snapshot_hash:hash,data_mode:mode,horizon_sessions:holding,as_of:time,training_cutoff:time,
  base_session:day,target_session:day,target_close_at:time,calendar_version:z.string(),expected_return:returns,
  direction:z.enum(["UP","DOWN","FLAT"]),probability_up:z.null(),protocol_version:z.literal("eod-price-v1"),return_basis:z.literal("split-only-price-return"),
}).refine(p=>p.direction===(magnitude(p.expected_return).sign>0?"UP":magnitude(p.expected_return).sign<0?"DOWN":"FLAT")
  && Date.parse(p.training_cutoff)<=Date.parse(p.as_of));
const source=z.strictObject({schema_version:z.literal("registered-backtest-signal-v1"),signal_id:z.uuid(),model_id:modelId,
  registration_content_hash:hash,artifact_file_sha256:hash,registered_at:time,
  model_availability_policy:z.literal("historical-training-cutoff-registration-time-disclosed"),qualification:z.literal("UNVERIFIED"),
  training_evidence_verification:z.literal("not-retrained"),mapping_policy:z.literal("positive-return-long-otherwise-cash-v1"),
  holding_policy:z.literal("holding-sessions-equal-model-horizon-after-next-open-entry"),decision_at:time,action,prediction,content_hash:hash,
}).refine(s=>s.model_id==="lightgbm-experimental-"+s.registration_content_hash && s.prediction.model_id===s.model_id
  && s.action===(magnitude(s.prediction.expected_return).sign>0?"LONG":"CASH") && Date.parse(s.decision_at)>=Date.parse(s.prediction.as_of));
const episode=z.strictObject({schema_version:z.literal("next-open-backtest-episode-v1"),metrics_kind:z.literal("BACKTEST"),qualification:z.literal("UNVERIFIED"),
  execution_assumption:z.literal("historical-open-proxy-no-fill-guarantee"),return_basis:z.literal("split-only-price-return-excluding-dividends"),
  symbol:z.literal("NVDA"),data_mode:mode,calendar_version:z.string(),action,holding_sessions:holding,decision_at:time,cutoff:time,
  entry_session:day,exit_session:day,entry_at:time,exit_at:time,entry_open:positive,exit_open:positive,split_factor:positive,costs,
  benchmark_gross_return:returns,benchmark_net_return:returns,strategy_gross_return:returns,strategy_net_return:returns,
  input_snapshot_hash:hash,observation_snapshot_hash:hash,content_hash:hash,
}).refine(e=>Date.parse(e.decision_at)<Date.parse(e.entry_at) && Date.parse(e.entry_at)<Date.parse(e.exit_at) && Date.parse(e.exit_at)<=Date.parse(e.cutoff));
const row=z.strictObject({signal_id:z.uuid(),source_model_id:modelId,source_signal_hash:hash,input_hash:hash,input_snapshot_hash:hash,
  observation_snapshot_hash:hash,entry_at:time,exit_at:time,action,holding_sessions:holding,episode:episode.nullable(),
  error_code:z.enum(["BACKTEST_MISSING_SESSION","BACKTEST_EXECUTION_UNAVAILABLE","NOT_DUE","FUTURE_INFORMATION","ACTIONS_UNVERIFIED","BAR_NOT_FINAL","NOT_TRADING_SESSION"]).nullable(),
  overlaps_signal_id:z.uuid().nullable(),status:z.enum(["COMPUTED","EVIDENCE_UNAVAILABLE","EXCLUDED_OVERLAP"]),
}).refine(r=>(r.episode!==null)===(r.status==="COMPUTED") && (r.error_code!==null)===(r.status==="EVIDENCE_UNAVAILABLE")
  && (r.overlaps_signal_id!==null)===(r.status==="EXCLUDED_OVERLAP") && Date.parse(r.entry_at)<Date.parse(r.exit_at));
const point=z.strictObject({signal_id:z.uuid(),at:time,equity:nonnegative.nullable(),benchmark_equity:nonnegative.nullable()});
export const backtestReportSchema=z.strictObject({schema_version:z.literal("registered-next-open-backtest-report-v2"),metrics_kind:z.literal("BACKTEST"),
  qualification:z.literal("UNVERIFIED"),signal_verification:z.literal("registered-native-inference-replayed"),observation_snapshot_hash:hash,data_mode:mode,
  symbol:z.literal("NVDA"),calendar_version:z.string(),cutoff:time,scheduling_policy:z.literal("single-capital-first-entry-reserve-cash-v1"),
  equity_policy:z.literal("compound-net-returns-round-half-even-12-at-each-exit"),benchmark:z.literal("same-scheduled-windows-long-with-same-costs"),
  status:z.enum(["COMPLETE","INCOMPLETE"]),costs,signal_count:count.min(1).max(500),computed_count:count,evidence_unavailable_count:count,overlap_count:count,
  rows:z.array(row).min(1).max(500),realized_equity_curve:z.array(point).max(500),strategy_net_return:returns.nullable(),benchmark_net_return:returns.nullable(),
  maximum_realized_drawdown:unit.nullable(),signal_sources:z.array(source).min(1).max(500),content_hash:hash,
}).refine(r=>{
  if(r.rows.length!==r.signal_count || r.signal_sources.length!==r.signal_count || new Set(r.rows.map(v=>v.signal_id)).size!==r.signal_count)return false;
  if(r.computed_count!==r.rows.filter(v=>v.status==="COMPUTED").length || r.evidence_unavailable_count!==r.rows.filter(v=>v.status==="EVIDENCE_UNAVAILABLE").length
    || r.overlap_count!==r.rows.filter(v=>v.status==="EXCLUDED_OVERLAP").length)return false;
  const complete=r.evidence_unavailable_count===0;
  if((r.status==="COMPLETE")!==complete || [r.strategy_net_return,r.benchmark_net_return,r.maximum_realized_drawdown].some(v=>(v!==null)!==complete))return false;
  let previous=-Infinity,reserve=-Infinity,reserveId:string|null=null,known=true,curveIndex=0;
  for(const [index,v] of r.rows.entries()) {
    const s=r.signal_sources[index]!,entry=Date.parse(v.entry_at);
    if(entry<=previous || s.signal_id!==v.signal_id || s.model_id!==v.source_model_id || s.content_hash!==v.source_signal_hash || s.action!==v.action
      || s.prediction.horizon_sessions!==v.holding_sessions || s.prediction.data_snapshot_hash!==v.input_snapshot_hash
      || s.prediction.data_mode!==r.data_mode || s.prediction.calendar_version!==r.calendar_version || v.observation_snapshot_hash!==r.observation_snapshot_hash
      || Date.parse(s.decision_at)>=entry || Date.parse(s.decision_at)>Date.parse(r.cutoff))return false;
    previous=entry;
    const overlap=entry<reserve;
    if(overlap!==(v.status==="EXCLUDED_OVERLAP") || (overlap && v.overlaps_signal_id!==reserveId))return false;
    if(overlap)continue;
    reserve=Date.parse(v.exit_at);reserveId=v.signal_id;
    const e=v.episode;
    if(e===null)known=false;
    else if(e.entry_at!==v.entry_at || e.exit_at!==v.exit_at || e.action!==v.action || e.holding_sessions!==v.holding_sessions
      || e.decision_at!==s.decision_at || e.cutoff!==r.cutoff || e.data_mode!==r.data_mode || e.calendar_version!==r.calendar_version
      || e.input_snapshot_hash!==v.input_snapshot_hash || e.observation_snapshot_hash!==r.observation_snapshot_hash
      || e.costs.commission_rate!==r.costs.commission_rate || e.costs.slippage_rate!==r.costs.slippage_rate)return false;
    const p=r.realized_equity_curve[curveIndex++];
    if(!p || p.signal_id!==v.signal_id || p.at!==v.exit_at || (p.equity!==null)!==known || (p.benchmark_equity!==null)!==known)return false;
  }
  return curveIndex===r.realized_equity_curve.length;
},{message:"Backtest scope, source, schedule or missing-evidence state differs"});
export const backtestIdSchema=z.string().regex(/^backtest-report-[a-f0-9]{64}$/);
export const backtestResponseSchema=z.strictObject({id:backtestIdSchema,content_hash:hash,input_hash:hash,
  code_sha256:z.strictObject({"backtest.py":hash,"backtest_report.py":hash,"backtest_signals.py":hash,"training.py":hash,
    "experimental_predictions.py":hash,"forecast.py":hash,"calendar.py":hash}),report:backtestReportSchema,
}).refine(r=>r.id==="backtest-report-"+r.content_hash);
export type BacktestResponse=z.infer<typeof backtestResponseSchema>;

export const backtestQuerySchema=z.strictObject({
  cursor:backtestIdSchema.optional(),model_id:modelId.optional(),status:z.enum(["COMPLETE","INCOMPLETE"]).optional(),
  limit:z.string().regex(/^(?:[1-9]|[1-4][0-9]|50)$/).transform(Number).optional(),
});
const summary=z.strictObject({id:backtestIdSchema,content_hash:hash,report_hash:hash,data_mode:mode,registered_at:time,cutoff:time,
  model_ids:z.array(modelId).min(1).max(500),status:z.enum(["COMPLETE","INCOMPLETE"]),signal_count:count.min(1).max(500),
  computed_count:count,evidence_unavailable_count:count,overlap_count:count,qualification:z.literal("UNVERIFIED"),
  verification:z.literal("stored-integrity-checked-native-replay-on-detail"),
}).refine(r=>r.id==="backtest-report-"+r.content_hash && r.signal_count===r.computed_count+r.evidence_unavailable_count+r.overlap_count
  && (r.status==="COMPLETE")===(r.evidence_unavailable_count===0) && r.model_ids.length<=r.signal_count
  && r.model_ids.every((id,i)=>i===0 || id>r.model_ids[i-1]!));
export const backtestPageSchema=z.strictObject({items:z.array(summary).max(50),next_cursor:backtestIdSchema.nullable()})
  .refine(p=>new Set(p.items.map(r=>r.id)).size===p.items.length && (p.next_cursor===null || p.next_cursor===p.items.at(-1)?.id));
export type BacktestPage=z.infer<typeof backtestPageSchema>;
