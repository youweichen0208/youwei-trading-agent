"use client";
import Link from "next/link";
import {useEffect,useRef,useState} from "react";
import {useRouter} from "next/navigation";
import {modelProposalKeySchema,modelProposalResponseSchema,modelReviewSubmissionSchema,combinedModelReviewRequestSchema,modelReviewResponseSchema,modelReviewPageSchema,
  type ModelProposalResponse,type ModelReviewResponse,type ModelReviewPage,type QualityGateResponse} from "@yta/contracts";
import {api,dateTime,HttpError,type Me} from "../lib/api";
import {CombinedReviewEvidence} from "./combined-review-evidence";

type Attempt={key:string;request:ReturnType<typeof modelReviewSubmissionSchema.parse>;resultId?:string;conflict?:boolean};
function ReviewRecord({record}:{record:ModelReviewResponse}) {
  const r=record.review;
  return <article className="evidence-card"><div className="section-heading"><h2>审核 #{r.revision}</h2><span className="badge">{r.disposition==="REJECTED" ? "已拒绝 · 提案关闭" : "晋升受阻 · 未批准"}</span></div>
    <p className="proposal-reason">{r.reason}</p><p className="small muted">{dateTime(record.created_at)} · 美东时间</p>
    <dl className="facts"><dt>审核人</dt><dd className="hash">{r.reviewer_id}</dd><dt>审核动作</dt><dd>{r.action==="REJECT" ? "拒绝提案" : "请求批准（未通过资格检查）"}</dd><dt>证据内容哈希</dt><dd className="hash">{r.schema_version==="model-review-record-v2" ? r.quality_gate_content_hash : r.assessment_content_hash}</dd><dt>审核内容哈希</dt><dd className="hash">{r.content_hash}</dd></dl>
    {r.blocking_reasons.length>0 && <p>历史证据尚未取得晋升资格。{r.blocking_reasons.some(reason=>reason.endsWith("NUMERIC_GATE_FAILED")) && "数值规则也未全部通过。"}</p>}
    <p><Link href={"/model-quality/"+(r.schema_version==="model-review-record-v2" ? r.price_assessment_id : r.assessment_id)}>查看固定质量核验 →</Link></p>{r.schema_version==="model-review-record-v2" && <><p><Link href={"/probability-quality/"+r.probability_assessment_id}>查看固定概率核验 →</Link></p><p className="small hash">组合核验：{r.quality_gate_id}</p></>}<Link href={"/model-reviews/"+record.id}>打开审核记录 →</Link>
  </article>;
}
export function ModelReview({me,proposalId,reviewId}:{me:Me;proposalId?:string;reviewId?:string}) {
  const router=useRouter(),active=useRef<AbortController|null>(null);
  const allowed=Boolean(me.experimental_models_enabled && me.can_review_models);
  const cacheKey=`yta:model-review:${me.user.id}:${me.data_mode}:${proposalId}`;
  const [proposal,setProposal]=useState<ModelProposalResponse>(),[history,setHistory]=useState<ModelReviewPage>();
  const [record,setRecord]=useState<ModelReviewResponse>(),[gate,setGate]=useState<QualityGateResponse>();
  const [attempt,setAttempt]=useState<Attempt>(),[reason,setReason]=useState(""),[action,setAction]=useState<"APPROVE"|"REJECT">("REJECT");
  const [trail,setTrail]=useState<string[]>([]),[reload,setReload]=useState(0),[ready,setReady]=useState(false);
  const [pending,setPending]=useState(false),[error,setError]=useState("");
  const cursor=trail.at(-1);
  useEffect(()=>{
    if(!allowed)return;
    const abort=new AbortController();setReady(false);setError("");setHistory(undefined);
    async function load(){
      if(reviewId){
        const id=modelProposalKeySchema.parse(reviewId);
        const result=modelReviewResponseSchema.parse(await api<unknown>("/model-reviews/"+id,{signal:abort.signal}));
        if(result.id!==id || result.review.data_mode!==me.data_mode)throw new Error("Invalid review");
        if(!abort.signal.aborted)setRecord(result);
      }else{
        const id=modelProposalKeySchema.parse(proposalId);
        const raw=sessionStorage.getItem(cacheKey);
        let restored:Attempt|undefined;
        if(raw){
          const value=JSON.parse(raw) as Attempt;
          restored={key:modelProposalKeySchema.parse(value.key),request:modelReviewSubmissionSchema.parse(value.request),
            ...(value.resultId ? {resultId:modelProposalKeySchema.parse(value.resultId)} : {}),...(value.conflict===true ? {conflict:true} : {})};
          if(restored.request.proposal_id!==id)throw new Error("Invalid attempt");
        }
        const query=new URLSearchParams({limit:"10"});if(cursor)query.set("cursor",cursor);
        const [p,h]=await Promise.all([api<unknown>("/model-review-proposals/"+id,{signal:abort.signal}),api<unknown>(`/model-proposals/${id}/reviews?${query}`,{signal:abort.signal})]);
        const saved=modelProposalResponseSchema.parse(p),page=modelReviewPageSchema.parse(h);
        if(saved.id!==id || saved.proposal.data_mode!==me.data_mode || page.items.some(x=>x.review.proposal_id!==id || x.review.data_mode!==me.data_mode || x.id===cursor))throw new Error("Invalid evidence");
        let result:ModelReviewResponse|undefined;
        if(restored?.resultId){
          result=modelReviewResponseSchema.parse(await api<unknown>("/model-reviews/"+restored.resultId,{signal:abort.signal}));
          if(result.id!==restored.resultId || result.review.proposal_id!==id || result.review.reviewer_id!==me.user.id || result.review.request_id!==restored.key || result.review.data_mode!==me.data_mode || !("quality_gate_id" in restored.request ? result.review.schema_version==="model-review-record-v2" && result.review.quality_gate_id===restored.request.quality_gate_id : result.review.schema_version==="model-review-record-v1" && result.review.assessment_id===restored.request.assessment_id) || result.review.action!==restored.request.action || result.review.reason!==restored.request.reason || result.review.previous_review_id!==restored.request.expected_previous_review_id)throw new Error("Invalid saved result");
        }
        if(!abort.signal.aborted){setProposal(saved);setHistory(page);setAttempt(restored);setRecord(result);if(restored){setReason(restored.request.reason);setAction(restored.request.action);}}
      }
      if(!abort.signal.aborted)setReady(true);
    }
    void load().catch(e=>{if(abort.signal.aborted)return;if(e instanceof HttpError && e.status===401){router.replace("/login");return;}setError("无法核对审核权限或固定证据，请重新加载。已保存请求不会被替换。");});
    return()=>abort.abort();
  },[allowed,me.data_mode,me.user.id,proposalId,reviewId,cacheKey,cursor,reload,router]);
  useEffect(()=>()=>active.current?.abort(),[]);
  const head=history?.items[0],closed=!cursor && head?.review.disposition==="REJECTED";
  async function submit(){
    if(!allowed || !ready || active.current || attempt?.resultId || attempt?.conflict || !proposal)return;
    let current=attempt;
    try{
      if(!current){
        if(cursor || closed || !gate || gate.proposal_id!==proposal.id || gate.proposal_content_hash!==proposal.content_hash)throw new Error("Evidence mismatch");
        current={key:crypto.randomUUID(),request:combinedModelReviewRequestSchema.parse({proposal_id:proposal.id,quality_gate_id:"model-quality-gate-"+gate.content_hash,action,reason,expected_previous_review_id:head?.id ?? null})};
        sessionStorage.setItem(cacheKey,JSON.stringify(current));setAttempt(current);
      }
    }catch{setError("请固定价格与概率组合证据、填写 1–2000 字审核理由，并允许保存本次请求。");return;}
    const abort=new AbortController();active.current=abort;setPending(true);setError("");
    try{
      const result=modelReviewResponseSchema.parse(await api<unknown>("/model-reviews",{body:current.request,key:current.key,csrf:me.csrf_token,signal:abort.signal}));
      const r=result.review,q=current.request;
      if(r.data_mode!==me.data_mode || r.reviewer_id!==me.user.id || r.request_id!==current.key || r.proposal_id!==q.proposal_id || !("quality_gate_id" in q ? r.schema_version==="model-review-record-v2" && r.quality_gate_id===q.quality_gate_id : r.schema_version==="model-review-record-v1" && r.assessment_id===q.assessment_id) || r.action!==q.action || r.reason!==q.reason || r.previous_review_id!==q.expected_previous_review_id)throw new Error("Invalid review response");
      if(abort.signal.aborted)return;
      const saved={...current,resultId:result.id};sessionStorage.setItem(cacheKey,JSON.stringify(saved));setAttempt(saved);setRecord(result);
    }catch(e){
      if(abort.signal.aborted)return;
      if(e instanceof HttpError && e.status===401){router.replace("/login");return;}
      if(e instanceof HttpError && ["MODEL_REVIEW_STALE","MODEL_REVIEW_CLOSED"].includes(e.code)){
        const conflict={...current,conflict:true};try{sessionStorage.setItem(cacheKey,JSON.stringify(conflict));}catch{}
        setAttempt(conflict);setError("提案审核状态已变化，本次请求未写入。请重新加载并重新核对后再决定。");
      }else setError("暂时无法确认审核结果。已保留原请求，请重试原审核；不会自动生成新请求。");
    }finally{if(!abort.signal.aborted){setPending(false);active.current=null;}}
  }
  function reset(){
    if(pending || (!attempt?.resultId && !attempt?.conflict))return;
    try{sessionStorage.removeItem(cacheKey);}catch{setError("无法清理已确认的本地请求，请检查浏览器存储。");return;}
    setAttempt(undefined);setRecord(undefined);setGate(undefined);setReason("");setTrail([]);setReload(n=>n+1);
  }
  return <main className="records-page experimental-reports-page">
    <div className="records-heading"><div><p className="eyebrow">人工审核 / 固定证据</p><h1>{reviewId ? "模型审核记录" : "审核提案"}</h1><p className="muted">核对证据、记录理由，保留每次决定。</p></div><Link href="/model-review-proposals">← 审核提案列表</Link></div>
    {!allowed ? <section className="empty-state"><h2>当前账户无法访问模型审核</h2></section> : <>
      <aside className="model-scope"><span className="badge">晋升尚未开放</span><div><h2>当前证据不能批准模型晋升</h2><p>请求批准会记录资格检查未通过的原因；拒绝会关闭当前提案。两种操作均不会切换冠军模型。</p></div></aside>
      {error && <div role="alert" className="alert">{error}{!ready && <button onClick={()=>setReload(n=>n+1)}>重新加载</button>}</div>}
      {!ready && !error && <p role="status">正在读取审核证据…</p>}
      {ready && record && !history?.items.some(item=>item.id===record.id) && <><ReviewRecord record={record}/><Link href={"/model-review-proposals/"+record.review.proposal_id}>返回提案审核 →</Link></>}
      {ready && proposal && <>
        <section className="evidence-card"><h2>固定提案</h2><p className="proposal-reason">{proposal.proposal.rationale}</p><dl className="facts"><dt>提交人</dt><dd className="hash">{proposal.proposal.owner_id}</dd><dt>候选模型</dt><dd className="hash">{proposal.proposal.candidate_model_id}</dd><dt>提案哈希</dt><dd className="hash">{proposal.content_hash}</dd></dl><Link href={"/model-evaluations/"+proposal.proposal.candidate_evaluation_id}>核对候选报告 →</Link>{proposal.proposal.reference_evaluation_id && <p><Link href={"/model-evaluations/"+proposal.proposal.reference_evaluation_id}>核对参考报告 →</Link></p>}</section>
        <section aria-label="审核历史"><h2>审核历史</h2>{history?.items.length===0 && <p>尚无审核记录。</p>}{history?.items.map(item=><ReviewRecord key={item.id} record={item}/>)}
          <div className="model-pagination"><button disabled={pending || !trail.length || Boolean(attempt && !attempt.resultId && !attempt.conflict)} onClick={()=>setTrail(t=>t.slice(0,-1))}>上一页</button><span>第 {trail.length+1} 页</span><button disabled={pending || !history?.next_cursor || Boolean(attempt && !attempt.resultId && !attempt.conflict)} onClick={()=>{if(history?.next_cursor)setTrail(t=>[...t,history.next_cursor!]);}}>下一页</button></div>
        </section>
        {closed && !attempt && <section className="empty-state"><h2>提案已拒绝，不能继续审核</h2><p>需要另行提交新提案。</p></section>}
        {!cursor && !closed && !attempt && <CombinedReviewEvidence key={proposal.id+":"+reload} me={me} proposal={proposal} onGate={setGate}/>}
        {!cursor && (!closed || attempt) && !attempt?.resultId && !attempt?.conflict && <section className="evidence-card"><h2>提交审核决定</h2>
          {attempt && <p>已固定原请求、理由与前驱版本。请重试核对结果。</p>}
          <label className="proposal-rationale">审核动作<select value={action} disabled={Boolean(attempt)} onChange={e=>setAction(e.target.value as "APPROVE"|"REJECT")}><option value="REJECT">拒绝提案（关闭当前提案）</option><option value="APPROVE">请求批准（当前资格会阻止晋升）</option></select></label>
          <label className="proposal-rationale">审核理由<textarea rows={4} disabled={Boolean(attempt)} value={reason} onChange={e=>setReason(e.target.value)}/></label>
          <p className="small muted">{[...reason].length} / 2000 字 · 操作结果将永久保留</p>
          <button className="primary" disabled={pending || (!attempt && (!gate || !/\S/u.test(reason) || [...reason].length>2000))} onClick={()=>void submit()}>{pending ? "正在核对审核…" : attempt ? "重试原审核" : "确认提交审核"}</button>
        </section>}
        {(attempt?.resultId || attempt?.conflict) && <button disabled={pending} onClick={reset}>重新加载最新状态并核对</button>}
      </>}
    </>}
  </main>;
}
