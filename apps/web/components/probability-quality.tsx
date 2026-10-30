"use client";
import Link from "next/link";
import {useEffect,useRef,useState} from "react";
import {useRouter} from "next/navigation";
import {experimentalCalibrationIdSchema,calibrationReportResponseSchema,probabilityPolicyPageSchema,
  probabilityAssessmentResponseSchema,probabilityAssessmentIdSchema,
  type CalibrationReportResponse,type ProbabilityPolicyPage,type ProbabilityAssessmentResponse} from "@yta/contracts";
import {api,HttpError,type Me} from "../lib/api";

const labels: Record<string,string>={minimum_calibration_samples:"最低拟合样本数",minimum_evaluation_samples:"最低评测样本数",
 minimum_evaluation_up:"最低上涨样本数",minimum_evaluation_non_up:"最低非上涨样本数",maximum_brier:"最高 Brier 分数",maximum_ece:"最高 ECE 误差"};
const shown=(value: string|number|boolean|null)=>value===null ? "缺少证据" : typeof value==="boolean" ? value ? "是" : "否" : String(value);
export function ProbabilityQuality({me,calibrationId,assessmentId,onAssessed}:{me:Me;calibrationId?:string;assessmentId?:string;onAssessed?:(value:ProbabilityAssessmentResponse|undefined)=>void}) {
  const Root=onAssessed ? "section" : "main", Heading=onAssessed ? "h2" : "h1";
  const router=useRouter(),active=useRef<AbortController|null>(null);
  const [report,setReport]=useState<CalibrationReportResponse>();
  const [page,setPage]=useState<ProbabilityPolicyPage>();
  const [trail,setTrail]=useState<string[]>([]),[selected,setSelected]=useState("");
  const [result,setResult]=useState<ProbabilityAssessmentResponse>();
  const [loading,setLoading]=useState(true),[listing,setListing]=useState(false),[pending,setPending]=useState(false);
  const [error,setError]=useState(""),[listError,setListError]=useState(""),[submitError,setSubmitError]=useState("");
  const [reload,setReload]=useState(0),[listReload,setListReload]=useState(0);
  useEffect(()=>{onAssessed?.(result);},[result,onAssessed]);
  const horizon=report?.report.horizon_sessions,cursor=trail.at(-1);
  useEffect(()=>{
    if(!me.experimental_models_enabled)return;
    const abort=new AbortController();setLoading(true);setError("");setResult(undefined);setReport(undefined);setTrail([]);setSelected("");setPage(undefined);
    async function load() {
      if(assessmentId) {
        const id=probabilityAssessmentIdSchema.parse(assessmentId);
        const saved=probabilityAssessmentResponseSchema.parse(await api<unknown>("/probability-quality-assessments/"+id,{signal:abort.signal}));
        if(id!=="probability-quality-assessment-"+saved.content_hash || saved.policy.data_mode!==me.data_mode)throw new Error("Invalid assessment");
        if(!abort.signal.aborted)setResult(saved);
      } else {
        const id=experimentalCalibrationIdSchema.parse(calibrationId);
        const value=calibrationReportResponseSchema.parse(await api<unknown>("/experimental-calibrations/"+id,{signal:abort.signal}));
        if(value.id!==id || value.report.data_mode!==me.data_mode)throw new Error("Invalid calibration report");
        if(!abort.signal.aborted)setReport(value);
      }
    }
    void load().catch(e=>{if(abort.signal.aborted)return;if(e instanceof HttpError && e.status===401){router.replace("/login");return;}setError("无法核对固定报告或核验结果，请重新加载。");})
      .finally(()=>{if(!abort.signal.aborted)setLoading(false);});
    return()=>abort.abort();
  },[me.experimental_models_enabled,me.data_mode,calibrationId,assessmentId,reload,router]);
  useEffect(()=>{
    if(!me.experimental_models_enabled || !horizon || assessmentId)return;
    const abort=new AbortController();setListing(true);setPage(undefined);setSelected("");setResult(undefined);setListError("");setSubmitError("");
    const query=new URLSearchParams({horizon_sessions:String(horizon),limit:"10"});if(cursor)query.set("cursor",cursor);
    void api<unknown>("/probability-quality-policies?"+query,{signal:abort.signal}).then(value=>{
      const next=probabilityPolicyPageSchema.parse(value);
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
    if(!rule || active.current || !calibrationId || !horizon || !report)return;
    const abort=new AbortController();active.current=abort;setPending(true);setResult(undefined);setSubmitError("");
    const body={policy_id:rule.id,calibration_id:calibrationId};
    try {
      const value=probabilityAssessmentResponseSchema.parse(await api<unknown>("/probability-quality-assessments",{body,csrf:me.csrf_token,signal:abort.signal}));
      if(value.policy.data_mode!==me.data_mode || value.policy.horizon_sessions!==horizon || value.policy_hash!==rule.content_hash
        || value.calibration_id!==calibrationId || value.model_id!==report.model_id || value.calibration_content_hash!==report.content_hash || value.report_hash!==report.report_hash)throw new Error("Invalid result");
      if(!abort.signal.aborted)setResult(value);
    } catch(e) {
      if(abort.signal.aborted)return;
      if(e instanceof HttpError && e.status===401){router.replace("/login");return;}
      setSubmitError(e instanceof HttpError && e.code==="WRITE_ADMISSION_PAUSED" ? "核验登记入口暂时关闭。已保留规则选择，恢复后可重试。" : "暂时无法确认核验结果。已保留规则选择，重试会核对相同报告与规则。");
    } finally {if(!abort.signal.aborted){active.current=null;setPending(false);}}
  }
  return <Root className={onAssessed ? "review-quality" : "records-page experimental-reports-page"}>
    <div className="records-heading"><div><p className="eyebrow">独立校准 / 概率规则</p><Heading>{assessmentId ? "概率核验详情" : "概率质量核验"}</Heading><p className="muted">逐项核对已登记规则，保留当时的报告与判断依据。</p></div><Link href="/models">← 模型目录</Link></div>
    {!me.experimental_models_enabled ? <section className="empty-state"><h2>概率质量核验尚未启用</h2></section> : <>
      <aside className="model-scope"><span className="badge">尚未批准</span><div><h2>数值达标不代表模型已获批准</h2><p>历史数据资格、人工审批及冠军切换尚未完成。选择规则只用于本次核验，不会激活生产规则。{me.data_mode==="REPLAY" && "当前为合成 REPLAY 数据。"}</p></div></aside>
      {error && <div role="alert" className="alert">{error}<button onClick={()=>setReload(n=>n+1)}>重新加载</button></div>}
      {loading && <p role="status">正在核对固定证据…</p>}
      {report && <section className="evidence-card"><h2>本次校准报告</h2><p><Link href={"/model-calibrations/"+report.id}>核对固定校准报告 · {report.report.horizon_sessions} 交易日 →</Link></p><p className="hash">{report.model_id}</p><p className="muted">上涨为正类，持平和下跌为负类。样本数量不代表独立样本量。</p></section>}
      {!assessmentId && horizon && <section className="evidence-card"><h2>选择已登记规则</h2><p className="muted">{horizon} 交易日 · {me.data_mode} · 阈值为原始十进制值</p>
        {listError && <div role="alert" className="alert">{listError}<button onClick={()=>setListReload(n=>n+1)}>重试规则列表</button></div>}
        {listing && <p role="status">正在读取规则…</p>}
        {page?.items.length===0 && <p>暂无匹配规则。需要先登记对应环境和跨度的规则。</p>}
        <fieldset disabled={pending} className="quality-rules"><legend>本次核验规则</legend>{page?.items.map(row=><label className="quality-rule" key={row.id}><input type="radio" name="probability-quality-policy" value={row.id} checked={selected===row.id} onChange={()=>{setSelected(row.id);setResult(undefined);setSubmitError("");}}/><span><strong>{row.policy.policy_version}</strong><span className="small">拟合 ≥ {row.policy.minimum_calibration_samples} · 评测 ≥ {row.policy.minimum_evaluation_samples} · 上涨 ≥ {row.policy.minimum_evaluation_up} · 非上涨 ≥ {row.policy.minimum_evaluation_non_up}</span><span className="small">Brier ≤ {row.policy.maximum_brier} · ECE ≤ {row.policy.maximum_ece}</span><span className="hash small">{row.id}</span></span></label>)}</fieldset>
        <div className="model-pagination"><button disabled={pending||listing||!trail.length} onClick={()=>setTrail(old=>old.slice(0,-1))}>上一页</button><span>第 {trail.length+1} 页</span><button disabled={pending||listing||!page?.next_cursor} onClick={()=>{if(page?.next_cursor)setTrail(old=>[...old,page.next_cursor!]);}}>下一页</button></div>
        {submitError && <div role="alert" className="alert">{submitError}</div>}
        <button className="primary" disabled={pending||listing||!selected} onClick={()=>void assess()}>{pending ? "正在核验…" : submitError ? "重试本次核验" : "按所选规则核验"}</button>
      </section>}
      {result && <section className="evidence-card"><h2>{result.numeric_pass ? "数值门槛已满足 · 仍未批准" : "尚未满足所选数值门槛"}</h2><p>规则：{result.policy.policy_version}</p><div className="table-scroll" role="region" aria-label="概率核验明细" tabIndex={0}><table><thead><tr><th>检查项</th><th>观测值</th><th>规则要求</th><th>结果</th></tr></thead><tbody>{result.checks.map(check=><tr key={check.name}><td>{labels[check.name]}</td><td>{shown(check.observed)}</td><td>{shown(check.required)}</td><td>{check.passed ? "通过" : "未通过"}</td></tr>)}</tbody></table></div><p className="small muted">四项样本数要求不低于阈值；Brier 与 ECE 要求不高于阈值。数值通过不证明真实数据资格或生产可靠性。</p><dl className="facts"><dt>规则哈希</dt><dd className="hash">{result.policy_hash}</dd><dt>拟合样本集合</dt><dd className="hash">{result.calibration_cohort_hash}</dd><dt>评测样本集合</dt><dd className="hash">{result.evaluation_cohort_hash}</dd><dt>校准登记哈希</dt><dd className="hash">{result.calibration_content_hash}</dd><dt>报告哈希</dt><dd className="hash">{result.report_hash}</dd><dt>核验内容哈希</dt><dd className="hash">{result.content_hash}</dd></dl><p><Link href={"/model-calibrations/"+result.calibration_id}>核对校准报告 →</Link></p>{!assessmentId && <Link href={"/probability-quality/probability-quality-assessment-"+result.content_hash}>打开固定核验详情 →</Link>}</section>}
    </>}
  </Root>;
}
