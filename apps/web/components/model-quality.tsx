"use client";
import Link from "next/link";
import {useEffect,useRef,useState} from "react";
import {useRouter} from "next/navigation";
import {experimentalEvaluationIdSchema,experimentalPriceReportSchema,qualityPolicyPageSchema,
  qualityAssessmentResponseSchema,qualityAssessmentIdSchema,
  type ExperimentalPriceReport,type QualityPolicyPage,type QualityAssessmentResponse} from "@yta/contracts";
import {api,HttpError,type Me} from "../lib/api";

const labels: Record<string,string>={minimum_samples:"最低样本数",maximum_mae:"最高平均绝对误差",
  minimum_direction_accuracy:"最低方向命中率",required_reference:"必须提供参考模型",
  "mae_improvement:zero-return-v1":"相对零收益的误差改善",
  "mae_improvement:momentum20-v1":"相对五日动量的误差改善","mae_improvement:reference":"相对参考模型的误差改善"};
const shown=(value: string|number|boolean|null)=>value===null ? "缺少证据" : typeof value==="boolean" ? value ? "是" : "否" : String(value);
export function ModelQuality({me,candidateId,referenceId,assessmentId,onAssessed}:{me:Me;candidateId?:string;referenceId?:string;assessmentId?:string;onAssessed?:(value:QualityAssessmentResponse|undefined)=>void}) {
  const Root=onAssessed ? "section" : "main", Heading=onAssessed ? "h2" : "h1";
  const router=useRouter(),active=useRef<AbortController|null>(null);
  const reference=referenceId && referenceId!=="baseline" ? referenceId : null;
  const [reports,setReports]=useState<ExperimentalPriceReport[]>([]);
  const [page,setPage]=useState<QualityPolicyPage>();
  const [trail,setTrail]=useState<string[]>([]),[selected,setSelected]=useState("");
  const [result,setResult]=useState<QualityAssessmentResponse>();
  const [loading,setLoading]=useState(true),[listing,setListing]=useState(false),[pending,setPending]=useState(false);
  const [error,setError]=useState(""),[listError,setListError]=useState(""),[submitError,setSubmitError]=useState("");
  const [reload,setReload]=useState(0),[listReload,setListReload]=useState(0);
  const horizon=reports[0]?.cohort.horizon_sessions,cursor=trail.at(-1);
  useEffect(()=>{onAssessed?.(result);},[result,onAssessed]);
  useEffect(()=>{
    if(!me.experimental_models_enabled)return;
    const abort=new AbortController();setLoading(true);setError("");
    async function load() {
      if(assessmentId) {
        const id=qualityAssessmentIdSchema.parse(assessmentId);
        const saved=qualityAssessmentResponseSchema.parse(await api<unknown>("/historical-quality-assessments/"+id,{signal:abort.signal}));
        if(id!=="historical-quality-assessment-"+saved.content_hash || saved.policy.data_mode!==me.data_mode)throw new Error("Invalid assessment");
        if(!abort.signal.aborted)setResult(saved);
      } else {
        const ids=[experimentalEvaluationIdSchema.parse(candidateId),...(reference ? [experimentalEvaluationIdSchema.parse(reference)] : [])];
        const values=await Promise.all(ids.map(async id=>{
          const report=experimentalPriceReportSchema.parse(await api<unknown>("/experimental-evaluations/"+id,{signal:abort.signal}));
          if(id!=="model-evaluation-experimental-"+report.content_hash || report.cohort.data_mode!==me.data_mode)throw new Error("Invalid report");
          return report;
        }));
        if(values.length===2 && (values[0]!.cohort_hash!==values[1]!.cohort_hash || values[0]!.model_id===values[1]!.model_id))throw new Error("Invalid reference");
        if(!abort.signal.aborted)setReports(values);
      }
    }
    void load().catch(e=>{if(abort.signal.aborted)return;if(e instanceof HttpError && e.status===401){router.replace("/login");return;}setError("无法核对固定报告或核验结果，请重新加载。");})
      .finally(()=>{if(!abort.signal.aborted)setLoading(false);});
    return()=>abort.abort();
  },[me.experimental_models_enabled,me.data_mode,candidateId,reference,assessmentId,reload,router]);
  useEffect(()=>{
    if(!me.experimental_models_enabled || !horizon || assessmentId)return;
    const abort=new AbortController();setListing(true);setPage(undefined);setSelected("");setResult(undefined);setListError("");setSubmitError("");
    const query=new URLSearchParams({horizon_sessions:String(horizon),limit:"10"});if(cursor)query.set("cursor",cursor);
    void api<unknown>("/historical-quality-policies?"+query,{signal:abort.signal}).then(value=>{
      const next=qualityPolicyPageSchema.parse(value);
      if(next.items.length>10 || next.items.some(item=>item.policy.data_mode!==me.data_mode || item.policy.horizon_sessions!==horizon || item.id===cursor)
        || (cursor && next.next_cursor===cursor))throw new Error("Invalid policies");
      if(!abort.signal.aborted)setPage(next);
    }).catch(e=>{if(abort.signal.aborted)return;if(e instanceof HttpError && e.status===401){router.replace("/login");return;}setListError("规则暂时无法读取，请重试。");})
      .finally(()=>{if(!abort.signal.aborted)setListing(false);});
    return()=>abort.abort();
  },[me.experimental_models_enabled,me.data_mode,horizon,cursor,listReload,assessmentId,router]);
  useEffect(()=>()=>active.current?.abort(),[]);
  async function assess() {
    const rule=page?.items.find(item=>item.id===selected);
    if(!rule || active.current || !candidateId || !horizon)return;
    const abort=new AbortController();active.current=abort;setPending(true);setResult(undefined);setSubmitError("");
    const body={policy_id:rule.id,candidate_evaluation_id:candidateId,reference_evaluation_id:reference};
    try {
      const value=qualityAssessmentResponseSchema.parse(await api<unknown>("/historical-quality-assessments",{body,csrf:me.csrf_token,signal:abort.signal}));
      if(value.policy.data_mode!==me.data_mode || value.policy.horizon_sessions!==horizon || value.policy_hash!==rule.content_hash
        || value.candidate_evaluation_id!==candidateId || value.reference_evaluation_id!==reference || value.cohort_hash!==reports[0]!.cohort_hash)throw new Error("Invalid result");
      if(!abort.signal.aborted)setResult(value);
    } catch(e) {
      if(abort.signal.aborted)return;
      if(e instanceof HttpError && e.status===401){router.replace("/login");return;}
      setSubmitError(e instanceof HttpError && e.code==="WRITE_ADMISSION_PAUSED" ? "核验登记入口暂时关闭。已保留规则选择，恢复后可重试。" : "暂时无法确认核验结果。已保留规则选择，重试会核对相同报告与规则。");
    } finally {if(!abort.signal.aborted){active.current=null;setPending(false);}}
  }
  return <Root className={onAssessed ? "review-quality" : "records-page experimental-reports-page"}>
    <div className="records-heading"><div><p className="eyebrow">固定报告 / 历史规则</p><Heading>{assessmentId ? "质量核验详情" : "历史质量核验"}</Heading><p className="muted">逐项核对已登记规则，保留当时的报告与判断依据。</p></div><Link href="/models">← 模型目录</Link></div>
    {!me.experimental_models_enabled ? <section className="empty-state"><h2>历史质量核验尚未启用</h2></section> : <>
      <aside className="model-scope"><span className="badge">尚未批准</span><div><h2>数值达标不代表模型已获批准</h2><p>历史数据资格、人工审批及冠军切换尚未完成。选择规则只用于本次核验，不会激活生产规则。{me.data_mode==="REPLAY" && "当前为合成 REPLAY 数据。"}</p></div></aside>
      {error && <div role="alert" className="alert">{error}<button onClick={()=>setReload(n=>n+1)}>重新加载</button></div>}
      {loading && <p role="status">正在核对固定证据…</p>}
      {reports.length>0 && <section className="evidence-card"><h2>本次报告</h2>{reports.map((report,i)=><p key={report.content_hash}><Link href={"/model-evaluations/model-evaluation-experimental-"+report.content_hash}>{i===0 ? "候选报告" : "参考报告"} · {report.cohort.horizon_sessions} 交易日 →</Link><span className="hash"> {report.model_id}</span></p>)}{!reference && <p className="muted">未指定参考模型。要求参考模型的规则将记录为未通过。</p>}</section>}
      {!assessmentId && horizon && <section className="evidence-card"><h2>选择已登记规则</h2><p className="muted">{horizon} 交易日 · {me.data_mode} · 阈值为原始十进制值</p>
        {listError && <div role="alert" className="alert">{listError}<button onClick={()=>setListReload(n=>n+1)}>重试规则列表</button></div>}
        {listing && <p role="status">正在读取规则…</p>}
        {page?.items.length===0 && <p>暂无匹配规则。需要先登记对应环境和跨度的规则。</p>}
        <fieldset disabled={pending} className="quality-rules"><legend>本次核验规则</legend>{page?.items.map(row=><label className="quality-rule" key={row.id}><input type="radio" name="quality-policy" value={row.id} checked={selected===row.id} onChange={()=>{setSelected(row.id);setResult(undefined);setSubmitError("");}}/><span><strong>{row.policy.policy_version}</strong><span className="small">最低样本 {row.policy.minimum_samples} · MAE ≤ {row.policy.maximum_mae} · 命中率 ≥ {row.policy.minimum_direction_accuracy} · 误差改善 ≥ {row.policy.minimum_mae_improvement}</span><span className="small">要求参考模型：{row.policy.require_reference ? "是" : "否"}</span><span className="hash small">{row.id}</span></span></label>)}</fieldset>
        <div className="model-pagination"><button disabled={pending||listing||!trail.length} onClick={()=>setTrail(old=>old.slice(0,-1))}>上一页</button><span>第 {trail.length+1} 页</span><button disabled={pending||listing||!page?.next_cursor} onClick={()=>{if(page?.next_cursor)setTrail(old=>[...old,page.next_cursor!]);}}>下一页</button></div>
        {submitError && <div role="alert" className="alert">{submitError}</div>}
        <button className="primary" disabled={pending||listing||!selected} onClick={()=>void assess()}>{pending ? "正在核验…" : submitError ? "重试本次核验" : "按所选规则核验"}</button>
      </section>}
      {result && <section className="evidence-card"><h2>{result.numeric_pass ? "数值门槛已满足 · 仍未批准" : "尚未满足所选数值门槛"}</h2><p>规则：{result.policy.policy_version}</p><div className="table-scroll" role="region" aria-label="质量核验明细" tabIndex={0}><table><thead><tr><th>检查项</th><th>观测值</th><th>规则要求</th><th>结果</th></tr></thead><tbody>{result.checks.map(check=><tr key={check.name}><td>{labels[check.name]}</td><td>{shown(check.observed)}</td><td>{shown(check.required)}</td><td>{check.passed ? "通过" : "未通过"}</td></tr>)}</tbody></table></div><p className="small muted">MAE要求不高于阈值；样本数、命中率和误差改善要求不低于阈值。误差改善为对照MAE减去候选MAE，负值表示候选更差。</p><dl className="facts"><dt>规则哈希</dt><dd className="hash">{result.policy_hash}</dd><dt>样本集合</dt><dd className="hash">{result.cohort_hash}</dd><dt>核验内容哈希</dt><dd className="hash">{result.content_hash}</dd></dl><p><Link href={"/model-evaluations/"+result.candidate_evaluation_id}>核对候选报告 →</Link></p>{!assessmentId && <Link href={"/model-quality/historical-quality-assessment-"+result.content_hash}>打开固定核验详情 →</Link>}</section>}
    </>}
  </Root>;
}
