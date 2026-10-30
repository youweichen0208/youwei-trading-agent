"use client";
import Link from "next/link";
import {useEffect,useState} from "react";
import {useRouter} from "next/navigation";
import {backtestPageSchema,backtestQuerySchema,type BacktestPage} from "@yta/contracts";
import {api,dateTime,HttpError,type Me} from "../lib/api";
export function Backtests({me,model=""}:{me:Me;model?:string}) {
  const router=useRouter(),[status,setStatus]=useState(""),[trail,setTrail]=useState<string[]>([]);
  const [page,setPage]=useState<BacktestPage>(),[pending,setPending]=useState(false),[error,setError]=useState(""),[reload,setReload]=useState(0);
  const cursor=trail.at(-1);
  useEffect(()=>{
    if(!me.experimental_models_enabled)return;
    const abort=new AbortController();setPage(undefined);setPending(true);setError("");
    void (async()=>{
      const query=backtestQuerySchema.parse({limit:"10",...(model?{model_id:model}:{}),...(status?{status}:{}),...(cursor?{cursor}:{})});
      const params=new URLSearchParams({limit:String(query.limit)});
      if(query.model_id)params.set("model_id",query.model_id);if(query.status)params.set("status",query.status);if(query.cursor)params.set("cursor",query.cursor);
      const result=backtestPageSchema.parse(await api<unknown>("/backtest-reports?"+params,{signal:abort.signal}));
      if(result.items.length>10 || result.items.some(r=>r.data_mode!==me.data_mode || (model&&!r.model_ids.includes(model)) || (status&&r.status!==status) || r.id===cursor)
        || (cursor&&result.next_cursor===cursor))throw new Error("Backtest catalog scope differs");
      if(!abort.signal.aborted)setPage(result);
    })().catch(e=>{
      if(abort.signal.aborted)return;
      if(e instanceof HttpError && e.status===401){router.replace("/login");return;}
      setError("回测目录暂时无法读取，请重新加载或清除筛选。不会用其他模式的记录替代。");
    }).finally(()=>{if(!abort.signal.aborted)setPending(false);});
    return()=>abort.abort();
  },[me.experimental_models_enabled,me.data_mode,model,status,cursor,reload,router]);
  return <main className="records-page experimental-reports-page backtests-page">
    <div className="records-heading"><div><p className="eyebrow">历史执行 / 固定报告</p><h1>模型回测</h1><p className="muted">查看费用后的历史执行结果，保留未能计算的信号。</p></div><Link href="/models">← 实验模型</Link></div>
    {!me.experimental_models_enabled?<section className="empty-state"><h2>回测报告尚未启用</h2></section>:<>
      <aside className="model-scope"><span className="badge">未经正式验收</span><div><h2>先核对执行证据，再阅读收益</h2><p>历史开盘价是执行代理，不保证真实成交。目录只核对已存记录；打开固定报告时才重新执行模型推理和回测。目录可读不保证详情重放可用，也不构成模型晋升资格。</p></div></aside>
      {model&&<p className="backtest-model-filter">指定模型：<code>{model}</code> <Link href="/backtests">清除模型筛选</Link></p>}
      <div className="model-filter"><label htmlFor="backtest-status">报告完整性</label><select id="backtest-status" value={status} onChange={e=>{setStatus(e.target.value);setTrail([]);}}><option value="">全部报告</option><option value="COMPLETE">可完整计算</option><option value="INCOMPLETE">证据不足</option></select><span className="muted">按登记时间从新到旧 · {me.data_mode}</span></div>
      {error&&<div className="alert" role="alert">{error}<button onClick={()=>setReload(n=>n+1)}>重新加载</button></div>}
      {pending&&<p role="status">正在读取回测目录…</p>}
      {page&&(!page.items.length?<section className="empty-state"><h2>暂无匹配的回测报告</h2><p>当前模式和筛选条件下没有固定报告。</p>{status&&<button onClick={()=>{setStatus("");setTrail([]);}}>查看全部完整性状态</button>}</section>:page.items.map(r=><article className="evidence-card" key={r.id}>
        <div className="section-heading"><h2><Link href={"/backtests/"+r.id}>回测 {r.content_hash.slice(0,12)}</Link></h2><span className={r.status==="COMPLETE"?"chip":"badge"}>{r.status==="COMPLETE"?"可完整计算":"证据不足"}</span></div>
        <p className="muted small">登记 {dateTime(r.registered_at)} 美东 · 观察截点 {dateTime(r.cutoff)} 美东</p>
        <dl className="backtest-counts"><div><dt>全部信号</dt><dd>{r.signal_count}</dd></div><div><dt>已计算</dt><dd>{r.computed_count}</dd></div><div><dt>证据不足</dt><dd>{r.evidence_unavailable_count}</dd></div><div><dt>重叠排除</dt><dd>{r.overlap_count}</dd></div></dl>
        <p><Link className="button-link" href={"/backtests/"+r.id}>重放并查看报告 →</Link></p>
        <details><summary>查看模型与报告身份</summary><p className="hash">{r.id}</p>{r.model_ids.map(id=><p className="hash" key={id}><Link href={"/models/"+id+"/evaluations"}>{id}</Link></p>)}</details>
      </article>))}
      <div className="model-pagination"><button disabled={pending||!trail.length} onClick={()=>setTrail(t=>t.slice(0,-1))}>上一页</button><span>第 {trail.length+1} 页</span><button disabled={pending||!page?.next_cursor} onClick={()=>{if(page?.next_cursor)setTrail(t=>[...t,page.next_cursor!]);}}>下一页</button></div>
    </>}
  </main>;
}
