"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { modelProposalPageSchema, type ModelProposalPage } from "@yta/contracts";
import { api, dateTime, HttpError, type Me } from "../lib/api";

export function ModelProposalHistory({me,reviewer=false}: {me: Me;reviewer?:boolean}) {
  const router = useRouter();
  const [page, setPage] = useState<ModelProposalPage>();
  const [trail, setTrail] = useState<string[]>([]);
  const [pending, setPending] = useState(true);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const cursor = trail.at(-1);
  useEffect(() => {
    if ((!me.experimental_models_enabled || (reviewer && !me.can_review_models))) return;
    const abort = new AbortController();
    setPage(undefined); setError(""); setPending(true);
    const query = new URLSearchParams({limit: "10"});
    if (cursor) query.set("cursor", cursor);
    void api<unknown>(`${reviewer ? "/model-review-proposals" : "/model-proposals"}?${query}`, {signal: abort.signal}).then(value => {
      const result = modelProposalPageSchema.parse(value);
      if (result.items.length > 10 || result.items.some(row => (!reviewer && row.proposal.owner_id !== me.user.id)
        || row.proposal.data_mode !== me.data_mode || row.id === cursor)
        || (cursor && result.next_cursor === cursor)) throw new Error("Invalid proposal history");
      if (!abort.signal.aborted) setPage(result);
    }).catch(e => {
      if (abort.signal.aborted) return;
      if (e instanceof HttpError && e.status === 401) {router.replace("/login"); return;}
      setError("提案历史暂时无法读取，请重新加载。");
    }).finally(() => {if (!abort.signal.aborted) setPending(false);});
    return () => abort.abort();
  }, [cursor, reload, me.user.id, me.data_mode, me.experimental_models_enabled, me.can_review_models, reviewer, router]);
  return <main className="records-page experimental-reports-page">
    <div className="records-heading"><div><p className="eyebrow">候选模型 / 提交记录</p><h1>{reviewer ? "审核模型提案" : "我的模型提案"}</h1><p className="muted">{reviewer ? "查看当前环境中所有已提交提案，进入详情核对证据与审核历史。" : "查看当前环境中自己提交的固定证据与理由。"}</p></div><Link href="/models">← 模型目录</Link></div>
    {(!me.experimental_models_enabled || (reviewer && !me.can_review_models)) ? <section className="empty-state"><h2>{reviewer ? "当前账户无法访问模型审核" : "实验模型提案尚未启用"}</h2></section> : <>
      <aside className="model-scope"><span className="badge">尚未批准</span><div><h2>提交记录不代表模型已获批准</h2><p>历史证据尚未取得晋升资格。列表包含所有提交记录，审核状态请进入详情查看。{me.data_mode === "REPLAY" && "当前使用合成 REPLAY 数据。"}</p></div></aside>
      {error && <div role="alert" className="alert">{error}<button onClick={() => setReload(n => n + 1)}>重新加载</button></div>}
      {pending && <p role="status">正在读取提案历史…</p>}
      {page && (page.items.length ? <section aria-label="提案列表">{page.items.map(row => <article className="evidence-card" key={row.id}>
        <div className="section-heading"><h2>提案 {row.id.slice(0, 8)}</h2><span className="badge">SUBMITTED · 未验收</span></div>
        <p className="small muted">登记时间（美东）：{dateTime(row.created_at)}</p>
        <p className="proposal-reason">{row.proposal.rationale}</p>
        <dl className="facts"><dt>候选模型</dt><dd className="hash">{row.proposal.candidate_model_id}</dd><dt>参考模型</dt><dd className="hash">{row.proposal.reference_model_id ?? "仅使用报告内基线"}</dd></dl>
        <Link href={(reviewer ? "/model-review-proposals/" : "/model-proposals/") + row.id}>查看提案 {row.id.slice(0, 8)} →</Link>
      </article>)}</section> : <section className="empty-state"><h2>暂无已提交提案</h2><p>可从模型评测报告或比较页面提出候选模型。</p><Link href="/models">浏览模型目录 →</Link></section>)}
      <div className="model-pagination"><button disabled={pending || !trail.length} onClick={() => setTrail(old => old.slice(0, -1))}>上一页</button><span>第 {trail.length + 1} 页</span><button disabled={pending || !page?.next_cursor} onClick={() => {if (page?.next_cursor) setTrail(old => [...old, page.next_cursor!]);}}>下一页</button></div>
    </>}
  </main>;
}
