"use client";
import Link from "next/link";
import {useEffect,useState} from "react";
import {useRouter} from "next/navigation";
import {backtestIdSchema,backtestResponseSchema,type BacktestResponse} from "@yta/contracts";
import {api,dateTime,HttpError,type Me} from "../lib/api";
const state={COMPUTED:"已计算",EVIDENCE_UNAVAILABLE:"证据不足",EXCLUDED_OVERLAP:"重叠排除"};
const reason:Record<string,string>={BACKTEST_MISSING_SESSION:"缺少所需交易日行情",BACKTEST_EXECUTION_UNAVAILABLE:"执行时点无有效成交量",NOT_DUE:"观察窗口尚未结束",FUTURE_INFORMATION:"存在截点后的信息",ACTIONS_UNVERIFIED:"公司行动尚未核验",BAR_NOT_FINAL:"日线尚未最终确认",NOT_TRADING_SESSION:"日期不属于交易日"};
export function BacktestReport({me,id}:{me:Me;id:string}) {
  const router=useRouter(),[saved,setSaved]=useState<BacktestResponse>(),[pending,setPending]=useState(false),[error,setError]=useState(""),[reload,setReload]=useState(0),[page,setPage]=useState(0);
  useEffect(()=>{
    if(!me.experimental_models_enabled)return;
    const abort=new AbortController();setSaved(undefined);setPending(true);setError("");setPage(0);
    void (async()=>{
      const identity=backtestIdSchema.parse(id);
      const result=backtestResponseSchema.parse(await api<unknown>("/backtest-reports/"+identity,{signal:abort.signal}));
      if(result.id!==identity || result.report.data_mode!==me.data_mode)throw new Error("Backtest identity differs");
      if(!abort.signal.aborted)setSaved(result);
    })().catch(e=>{
      if(abort.signal.aborted)return;
      if(e instanceof HttpError && e.status===401){router.replace("/login");return;}
      setError(e instanceof HttpError&&e.status===404?"当前模式中找不到这份固定回测报告。":"这份报告暂时无法重放，请重新加载。不会展示未经核对的旧结果。");
    }).finally(()=>{if(!abort.signal.aborted)setPending(false);});
    return()=>abort.abort();
  },[id,me.data_mode,me.experimental_models_enabled,reload,router]);
  const r=saved?.report,rows=r?.rows.slice(page*20,(page+1)*20),points=new Map(r?.realized_equity_curve.map(p=>[p.signal_id,p]));
  return <main className="records-page experimental-reports-page backtests-page">
    <div className="records-heading"><div><p className="eyebrow">固定版本 / 历史执行</p><h1>回测报告</h1><p className="muted">收益包含双边费用和滑点，按退出时点记录权益。</p></div><Link href="/backtests">← 回测目录</Link></div>
    {!me.experimental_models_enabled?<section className="empty-state"><h2>回测报告尚未启用</h2></section>:<>
      {pending&&<p role="status">正在重放模型信号与回测报告…</p>}
      {error&&<div role="alert" className="alert">{error}<button onClick={()=>setReload(n=>n+1)}>重新加载</button></div>}
      {saved&&r&&<>
        <aside className="model-scope"><span className="badge">UNVERIFIED</span><div><h2>{r.status==="COMPLETE"?"执行结果可计算，尚未取得正式资格":"证据不足，无法计算完整收益"}</h2><p>已重放登记模型的原生推理和回测。训练来源尚未重新训练核验，实际登记时间不证明历史上已部署。历史开盘价不保证真实成交，结果不构成交易或模型晋升批准。</p></div></aside>
        <section className="evidence-card"><h2>覆盖范围</h2><dl className="backtest-counts"><div><dt>全部信号</dt><dd>{r.signal_count}</dd></div><div><dt>已计算</dt><dd>{r.computed_count}</dd></div><div><dt>证据不足</dt><dd>{r.evidence_unavailable_count}</dd></div><div><dt>重叠排除</dt><dd>{r.overlap_count}</dd></div></dl>
          <dl className="model-metrics"><div><dt>策略净收益</dt><dd>{r.strategy_net_return??"无法计算"}</dd></div><div><dt>同窗口 LONG 基准净收益</dt><dd>{r.benchmark_net_return??"无法计算"}</dd></div><div><dt>最大已实现回撤</dt><dd>{r.maximum_realized_drawdown??"无法计算"}</dd></div></dl>
          <p className="small muted">收益率按原始十进制文本展示，0.01 表示 1%。基准使用相同调度窗口和费用，不是连续买入持有。回撤仅基于退出时点，不代表逐日盯市或盘中风险。任一已占用窗口缺乏证据后，总权益保持未知。</p>
        </section>
        <section className="evidence-card"><h2>执行与费用口径</h2><dl className="facts"><dt>证券 / 模式</dt><dd>{r.symbol} / {r.data_mode}</dd><dt>观察截点（美东）</dt><dd>{dateTime(r.cutoff)}</dd><dt>每侧佣金率</dt><dd>{r.costs.commission_rate}</dd><dt>每侧滑点率</dt><dd>{r.costs.slippage_rate}</dd><dt>入场与出场</dt><dd>决策后的下一交易日开盘入场；持有指定交易日数后开盘出场。</dd><dt>价格口径</dt><dd>只处理拆股，不含分红再投资。</dd><dt>资金调度</dt><dd>单资金槽，最早入场优先；持有现金也占用窗口，重叠信号保留并排除。</dd><dt>交易日历</dt><dd>{r.calendar_version}</dd></dl><p className="small muted">佣金和滑点均按每侧比例计入。持仓交易日数从入场起算，因此退出比同跨度的收盘预测标签目标晚一个交易日。</p></section>
        <section className="evidence-card"><h2>逐笔执行与退出权益</h2><p className="muted">初始权益为 1；按净收益逐笔复利。证据不足不补零、不延后交易日；重叠排除项不改变权益。</p>
          <div className="table-scroll" role="region" aria-label="回测逐笔执行" tabIndex={0}><table><thead><tr><th>信号 / 状态</th><th>动作 / 持仓</th><th>入场 → 退出（美东）</th><th>策略净收益</th><th>基准净收益</th><th>退出权益 / 基准权益</th></tr></thead><tbody>{rows?.map(row=>{const p=points.get(row.signal_id);return <tr key={row.signal_id}><td><code>{row.signal_id.slice(0,8)}</code><br/>{state[row.status]}</td><td>{row.action==="LONG"?"持有多头":"持有现金"}<br/>{row.holding_sessions} 交易日</td><td>{dateTime(row.entry_at)}<br/>→ {dateTime(row.exit_at)}</td><td>{row.episode?.strategy_net_return??(row.status==="EXCLUDED_OVERLAP"?"未纳入":"无法计算")}</td><td>{row.episode?.benchmark_net_return??(row.status==="EXCLUDED_OVERLAP"?"未纳入":"无法计算")}</td><td>{p?<>{p.equity??"未知"}<br/>{p.benchmark_equity??"未知"}</>:"未纳入"}</td></tr>;})}</tbody></table></div>
          <div className="model-pagination"><button disabled={page===0} onClick={()=>setPage(n=>n-1)}>上一页信号</button><span>第 {page+1} 页</span><button disabled={(page+1)*20>=r.rows.length} onClick={()=>setPage(n=>n+1)}>下一页信号</button></div>
          {rows?.map((row,index)=>{const s=r.signal_sources[page*20+index]!;return <details className="backtest-source" key={row.signal_id}><summary>信号 {row.signal_id.slice(0,8)}：{state[row.status]} · 查看执行依据</summary>
            {row.error_code&&<p>{reason[row.error_code]} <code>{row.error_code}</code></p>}{row.overlaps_signal_id&&<p>窗口已由信号 <span className="hash">{row.overlaps_signal_id}</span> 占用。</p>}
            <dl className="facts"><dt>完整信号身份</dt><dd className="hash">{row.signal_id}</dd><dt>来源模型</dt><dd className="hash"><Link href={"/models/"+s.model_id+"/evaluations"}>{s.model_id}</Link></dd><dt>决策时间（美东）</dt><dd>{dateTime(s.decision_at)}</dd><dt>训练截点（美东）</dt><dd>{dateTime(s.prediction.training_cutoff)}</dd><dt>实际模型登记（美东）</dt><dd>{dateTime(s.registered_at)}</dd><dt>模型预测收益</dt><dd>{s.prediction.expected_return}</dd><dt>动作规则</dt><dd>量化预测收益大于零时持有多头，否则持有现金。</dd>{row.episode&&<><dt>原始入场 / 出场开盘价</dt><dd>{row.episode.entry_open} / {row.episode.exit_open}</dd><dt>拆股因子</dt><dd>{row.episode.split_factor}</dd><dt>策略 / 基准毛收益</dt><dd>{row.episode.strategy_gross_return} / {row.episode.benchmark_gross_return}</dd></>}<dt>信号内容哈希</dt><dd className="hash">{s.content_hash}</dd><dt>输入快照哈希</dt><dd className="hash">{row.input_snapshot_hash}</dd></dl>
          </details>;})}
        </section>
        <section className="evidence-card"><h2>固定版本与复现依据</h2><dl className="facts"><dt>报告 ID</dt><dd className="hash">{saved.id}</dd><dt>登记内容哈希</dt><dd className="hash">{saved.content_hash}</dd><dt>报告内容哈希</dt><dd className="hash">{r.content_hash}</dd><dt>完整输入哈希</dt><dd className="hash">{saved.input_hash}</dd><dt>观察快照哈希</dt><dd className="hash">{r.observation_snapshot_hash}</dd></dl><details><summary>核对代码版本</summary><pre className="report-manifest">{JSON.stringify(saved.code_sha256,null,2)}</pre></details><p className="small muted">完整输入保留在私有服务中；公开的输入哈希不能单独重建登记身份。</p></section>
      </>}
    </>}
  </main>;
}
