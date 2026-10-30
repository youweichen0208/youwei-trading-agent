"use client";
import {useEffect,useRef,useState} from "react";
import {experimentalPriceReportSchema,qualityGateResponseSchema,
  type ModelProposalResponse,type ExperimentalPriceReport,type QualityAssessmentResponse,
  type ProbabilityAssessmentResponse,type QualityGateResponse} from "@yta/contracts";
import {api,type Me} from "../lib/api";
import {ModelQuality} from "./model-quality";
import {ProbabilityQuality} from "./probability-quality";

export function CombinedReviewEvidence({me,proposal,onGate}:{me:Me;proposal:ModelProposalResponse;onGate:(gate:QualityGateResponse|undefined)=>void}) {
  const [report,setReport]=useState<ExperimentalPriceReport>(),[price,setPrice]=useState<QualityAssessmentResponse>();
  const [probability,setProbability]=useState<ProbabilityAssessmentResponse>(),[gate,setGate]=useState<QualityGateResponse>();
  const [error,setError]=useState(""),[loadError,setLoadError]=useState(""),[reload,setReload]=useState(0),[pending,setPending]=useState(false);
  const active=useRef<AbortController|null>(null),p=proposal.proposal;
  const eligible=Boolean(price && probability && report && price.candidate_evaluation_id===p.candidate_evaluation_id
    && price.reference_evaluation_id===p.reference_evaluation_id && price.cohort_hash===p.cohort_hash
    && probability.model_id===p.candidate_model_id && probability.calibration_id===report.calibration_id
    && probability.calibration_content_hash===report.calibration_content_hash);
  const current=eligible && gate && gate.price_assessment_id==="historical-quality-assessment-"+price?.content_hash
    && gate.probability_assessment_id==="probability-quality-assessment-"+probability?.content_hash ? gate : undefined;
  useEffect(()=>{setGate(undefined);setError("");},[price,probability]);
  useEffect(()=>{onGate(current);},[current,onGate]);
  useEffect(()=>{
    const abort=new AbortController();setReport(undefined);setLoadError("");
    void api<unknown>("/experimental-evaluations/"+p.candidate_evaluation_id,{signal:abort.signal}).then(raw=>{
      const value=experimentalPriceReportSchema.parse(raw);
      if(value.content_hash!==p.candidate_report_hash || value.model_id!==p.candidate_model_id
        || value.cohort_hash!==p.cohort_hash || value.cohort.data_mode!==me.data_mode)throw Error("Invalid candidate");
      if(!abort.signal.aborted)setReport(value);
    }).catch(()=>{if(!abort.signal.aborted)setLoadError("无法核对候选模型的固定校准引用，请重试。");});
    return()=>abort.abort();
  },[p.candidate_evaluation_id,p.candidate_report_hash,p.candidate_model_id,p.cohort_hash,me.data_mode,reload]);
  useEffect(()=>()=>active.current?.abort(),[]);
  async function combine(){
    if(!eligible || !price || !probability || !report || active.current)return;
    const abort=new AbortController();active.current=abort;setPending(true);setGate(undefined);setError("");
    const request={proposal_id:proposal.id,price_assessment_id:"historical-quality-assessment-"+price.content_hash,probability_assessment_id:"probability-quality-assessment-"+probability.content_hash};
    try {
      const value=qualityGateResponseSchema.parse(await api<unknown>("/model-review-quality-gates",{body:request,csrf:me.csrf_token,signal:abort.signal}));
      if(value.proposal_id!==proposal.id || value.proposal_content_hash!==proposal.content_hash || value.data_mode!==me.data_mode
        || value.price_assessment_id!==request.price_assessment_id || value.probability_assessment_id!==request.probability_assessment_id
        || value.candidate_evaluation_id!==p.candidate_evaluation_id || value.reference_evaluation_id!==p.reference_evaluation_id
        || value.candidate_model_id!==p.candidate_model_id || value.cohort_hash!==p.cohort_hash
        || value.calibration_id!==report.calibration_id || value.calibration_content_hash!==report.calibration_content_hash
        || value.probability_report_hash!==probability.report_hash || value.calibration_cohort_hash!==probability.calibration_cohort_hash
        || value.evaluation_cohort_hash!==probability.evaluation_cohort_hash
        || value.price_numeric_pass!==price.numeric_pass || value.probability_numeric_pass!==probability.numeric_pass
        || value.price_policy_hash!==price.policy_hash || value.probability_policy_hash!==probability.policy_hash)throw Error("Invalid gate");
      if(!abort.signal.aborted)setGate(value);
    }catch{if(!abort.signal.aborted)setError("暂时无法确认组合证据。已保留两份核验，可重试相同组合；此操作不提交审核决定。");}
    finally{if(!abort.signal.aborted){active.current=null;setPending(false);}}
  }
  return <section aria-label="组合审核证据">
    <h2>核对价格与概率证据</h2><p>分别选择规则并核验，再固定本次提案的组合证据。数值未通过也可记录审核意见，不能据此批准晋升。</p>
    {loadError && <div role="alert" className="alert">{loadError}<button onClick={()=>setReload(n=>n+1)}>重试候选证据</button></div>}
    <fieldset disabled={pending} style={{border:0,padding:0,minWidth:0}}><legend className="small muted">两类核验均需明确选择规则</legend>
      <div aria-label="价格证据"><ModelQuality me={me} candidateId={p.candidate_evaluation_id} referenceId={p.reference_evaluation_id ?? "baseline"} onAssessed={setPrice}/></div>
      {report && <div aria-label="概率证据"><ProbabilityQuality me={me} calibrationId={report.calibration_id} onAssessed={setProbability}/></div>}
    </fieldset>
    {error && <div role="alert" className="alert">{error}</div>}
    <button disabled={!eligible || pending || Boolean(current)} onClick={()=>void combine()}>{pending ? "正在固定组合证据…" : error ? "重试固定组合证据" : "固定组合证据"}</button>
    {current && <section className="evidence-card"><h3>组合证据已固定 · 尚未批准</h3><p>价格：{current.price_numeric_pass ? "数值通过" : "数值未通过"} · 概率：{current.probability_numeric_pass ? "数值通过" : "数值未通过"}</p><p className="hash">{"model-quality-gate-"+current.content_hash}</p><p>更换规则或重新核验将重新检查本次组合。</p></section>}
  </section>;
}
