"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { experimentalEvaluationIdSchema, experimentalPriceReportSchema, modelProposalKeySchema,
  modelProposalRequestSchema, modelProposalResponseSchema, resourceIdSchema,
  type ExperimentalPriceReport, type ModelProposalResponse } from "@yta/contracts";
import { api, HttpError, type Me } from "../lib/api";

type Attempt = {key: string; request: {candidate_evaluation_id: string; reference_evaluation_id: string | null; rationale: string}; resultId?: string};
export function ModelProposal({me, candidateId, referenceId, proposalId}: {me: Me; candidateId?: string; referenceId?: string; proposalId?: string}) {
  const router = useRouter();
  const reference = referenceId === "baseline" ? null : referenceId ?? null;
  const cacheKey = `yta:model-proposal:${me.user.id}:${me.data_mode}:${candidateId}:${reference}`;
  const [reports,setReports]=useState<ExperimentalPriceReport[]>([]);
  const [saved,setSaved]=useState<ModelProposalResponse>();
  const [attempt,setAttempt]=useState<Attempt>();
  const [rationale,setRationale]=useState("");
  const [pending,setPending]=useState(false);
  const [ready,setReady]=useState(false);
  const [error,setError]=useState("");
  const [retry,setRetry]=useState(0);
  const active=useRef<AbortController | null>(null);
  useEffect(() => {
    if (!me.experimental_models_enabled) return;
    const controller=new AbortController(); setReady(false);setError("");setSaved(undefined);setReports([]);
    async function load() {
      if(proposalId) {
        const id=resourceIdSchema.parse(proposalId).toLowerCase();
        const result=modelProposalResponseSchema.parse(await api<unknown>("/model-proposals/"+id,{signal:controller.signal}));
        if(result.id!==id || result.proposal.owner_id!==me.user.id || result.proposal.data_mode!==me.data_mode) throw new Error("Invalid proposal");
        if(!controller.signal.aborted) setSaved(result);
      } else {
        const candidate=experimentalEvaluationIdSchema.parse(candidateId);
        const ids=[candidate,...(reference ? [experimentalEvaluationIdSchema.parse(reference)] : [])];
        const raw=sessionStorage.getItem(cacheKey);
        if(raw) {
          const value=JSON.parse(raw) as Attempt;
          const key=modelProposalKeySchema.parse(value.key), request=modelProposalRequestSchema.parse(value.request);
          if(request.candidate_evaluation_id!==candidate || request.reference_evaluation_id!==reference) throw new Error("Invalid saved request");
          const restored={key,request,...(value.resultId ? {resultId:resourceIdSchema.parse(value.resultId)} : {})};
          if(!controller.signal.aborted) {setAttempt(restored);setRationale(request.rationale);}
        }
        const values=await Promise.all(ids.map(async id => {
          const report=experimentalPriceReportSchema.parse(await api<unknown>("/experimental-evaluations/"+id,{signal:controller.signal}));
          if("model-evaluation-experimental-"+report.content_hash!==id || report.cohort.data_mode!==me.data_mode) throw new Error("Invalid report");
          return report;
        }));
        if(values.length===2 && (values[0]!.cohort_hash!==values[1]!.cohort_hash || values[0]!.model_id===values[1]!.model_id)) throw new Error("Invalid reference");
        if(!controller.signal.aborted) setReports(values);
      }
      if(!controller.signal.aborted) setReady(true);
    }
    void load().catch(e => {
      if(controller.signal.aborted) return;
      if(e instanceof HttpError && e.status===401) {router.replace("/login");return;}
      setError("无法核对提案或报告。请重新加载，或返回选择相同样本的不同模型；已保存的提交身份不会被替换。");
    });
    return () => controller.abort();
  },[cacheKey,candidateId,reference,proposalId,me.experimental_models_enabled,me.user.id,me.data_mode,retry,router]);
  useEffect(() => () => active.current?.abort(),[]);
  async function submit() {
    if(active.current || !ready || !me.experimental_models_enabled || attempt?.resultId) return;
    let current=attempt;
    try {
      if(!current) {
        current={key:crypto.randomUUID(),request:modelProposalRequestSchema.parse({candidate_evaluation_id:candidateId,reference_evaluation_id:reference,rationale})};
        sessionStorage.setItem(cacheKey,JSON.stringify(current)); // Persist before dispatch; failure must prevent sending.
        setAttempt(current);
      }
    } catch {setError("请填写 1–2000 字的理由，并允许浏览器保存本次请求后再提交。");return;}
    const controller=new AbortController();active.current=controller;setPending(true);setError("");
    try {
      const result=modelProposalResponseSchema.parse(await api<unknown>("/model-proposals",{body:current.request,key:current.key,csrf:me.csrf_token,signal:controller.signal}));
      const p=result.proposal;
      if(p.owner_id!==me.user.id || p.data_mode!==me.data_mode || p.request_id!==current.key
        || p.candidate_evaluation_id!==current.request.candidate_evaluation_id || p.reference_evaluation_id!==current.request.reference_evaluation_id
        || p.rationale!==current.request.rationale) throw new Error("Invalid proposal");
      if(controller.signal.aborted) return;
      const completed={...current,resultId:result.id};
      sessionStorage.setItem(cacheKey,JSON.stringify(completed));setAttempt(completed);setSaved(result);
    } catch(e) {
      if(controller.signal.aborted) return;
      if(e instanceof HttpError && e.status===401) {router.replace("/login");return;}
      setError(e instanceof HttpError && e.code==="WRITE_ADMISSION_PAUSED"
        ? "提交入口暂时关闭。已保留原请求，恢复后点击“重试原提案”。"
        : "暂时无法确认提交结果。已保留原请求和理由，请点击“重试原提案”核对，避免重复提交。");
    } finally {if(!controller.signal.aborted) {setPending(false);active.current=null;}}
  }
  return <main className="records-page experimental-reports-page">
    <div className="records-heading"><div><p className="eyebrow">候选模型 / 固定证据</p><h1>{proposalId ? "模型提案详情" : "提出候选模型"}</h1><p className="muted">保留候选报告与提出理由，供后续审核。</p></div><Link href="/models">← 模型目录</Link></div>
    {!me.experimental_models_enabled ? <section className="empty-state"><h2>实验模型提案尚未启用</h2></section> : <>
      <p><Link href="/model-proposals">查看我的提案历史 →</Link></p>
      <aside className="model-scope"><span className="badge">尚未批准</span><div><h2>提交提案不会切换当前模型</h2><p>这里只登记候选证据。质量门槛、人工审批与冠军切换尚未开放。{me.data_mode==="REPLAY" && "当前使用合成 REPLAY 数据。"}</p></div></aside>
      {error && <div role="alert" className="alert">{error}{!ready && <button onClick={()=>setRetry(n=>n+1)}>重新加载</button>}</div>}
      {!ready && !error && <p role="status">正在核对固定证据…</p>}
      {!proposalId && ready && !saved && <section className="evidence-card"><h2>本次提案依据</h2>{reports.map((report,i)=><div key={report.content_hash}><h3>{i===0 ? "候选模型" : "参考模型"}</h3><p className="hash">{report.model_id}</p><p>{report.cohort.horizon_sessions} 交易日 · {report.manifest.length} 个历史样本</p><Link href={"/model-evaluations/model-evaluation-experimental-"+report.content_hash}>核对{ i===0 ? "候选" : "参考"}报告 →</Link></div>)}
        {!reference && <p className="muted">未指定参考模型；保留候选报告中的基线对照。</p>}
        <label className="proposal-rationale">提出理由<textarea value={rationale} disabled={Boolean(attempt)} onChange={e=>setRationale(e.target.value)} rows={5}/></label><p className="small muted">{[...rationale].length} / 2000 字</p>
        {attempt && <p className="small muted">本次理由已固定，重试会核对同一份提案。</p>}
        {attempt?.resultId ? <Link className="button-link" href={"/model-proposals/"+attempt.resultId}>查看已提交提案 →</Link> : <button className="primary" disabled={pending || (!attempt && (!/\S/u.test(rationale) || [...rationale].length>2000))} onClick={()=>void submit()}>{pending ? "正在核对提交…" : attempt ? "重试原提案" : "提交候选提案"}</button>}
      </section>}
      {saved && <p><Link href={`/models/quality/${saved.proposal.candidate_evaluation_id}/${saved.proposal.reference_evaluation_id ?? "baseline"}`}>核验本提案的历史质量 →</Link></p>}
      {saved && <section className="evidence-card"><h2>提案已提交</h2><p><span className="badge">SUBMITTED · 未验收</span></p><p className="proposal-reason">{saved.proposal.rationale}</p><dl className="facts"><dt>提案 ID</dt><dd className="hash">{saved.id}</dd><dt>登记时间（UTC）</dt><dd>{saved.created_at}</dd><dt>候选模型</dt><dd className="hash">{saved.proposal.candidate_model_id}</dd><dt>参考模型</dt><dd className="hash">{saved.proposal.reference_model_id ?? "仅使用报告内基线"}</dd><dt>样本集合</dt><dd className="hash">{saved.proposal.cohort_hash}</dd><dt>内容哈希</dt><dd className="hash">{saved.content_hash}</dd></dl><Link href={"/model-evaluations/"+saved.proposal.candidate_evaluation_id}>核对候选报告 →</Link>{!proposalId && <p><Link href={"/model-proposals/"+saved.id}>打开提案详情 →</Link></p>}</section>}
    </>}
  </main>;
}
