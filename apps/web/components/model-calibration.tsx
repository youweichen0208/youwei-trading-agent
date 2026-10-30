"use client";
import Link from "next/link";
import {useEffect,useState} from "react";
import {useRouter} from "next/navigation";
import {experimentalCalibrationIdSchema,calibrationReportResponseSchema,type CalibrationReportResponse} from "@yta/contracts";
import {api,dateTime,HttpError,type Me} from "../lib/api";

export function ModelCalibration({me,id}:{me:Me;id:string}) {
  const router=useRouter();
  const [saved,setSaved]=useState<CalibrationReportResponse>(),[pending,setPending]=useState(false),[error,setError]=useState("");
  const [reload,setReload]=useState(0),[samplePage,setSamplePage]=useState(0);
  useEffect(()=>{
    if(!me.experimental_models_enabled)return;
    const abort=new AbortController();setSaved(undefined);setPending(true);setError("");setSamplePage(0);
    void (async()=>{
      const identity=experimentalCalibrationIdSchema.parse(id);
      const result=calibrationReportResponseSchema.parse(await api<unknown>("/experimental-calibrations/"+identity,{signal:abort.signal}));
      if(result.id!==identity || result.report.data_mode!==me.data_mode)throw new Error("Invalid calibration identity");
      if(!abort.signal.aborted)setSaved(result);
    })().catch(e=>{
      if(abort.signal.aborted)return;
      if(e instanceof HttpError && e.status===401){router.replace("/login");return;}
      setError(e instanceof HttpError && e.status===404 ? "当前环境中找不到这份校准报告。" : "概率报告暂时无法读取，请重新加载。不会用其他报告替代。");
    }).finally(()=>{if(!abort.signal.aborted)setPending(false);});
    return()=>abort.abort();
  },[id,me.data_mode,me.experimental_models_enabled,reload,router]);
  const report=saved?.report,metrics=report?.reliability;
  return <main className="records-page experimental-reports-page">
    <div className="records-heading"><div><p className="eyebrow">独立校准 / 概率证据</p><h1>概率校准报告</h1><p className="muted">检查上涨概率与实际频率的差异，保留独立窗口和固定版本。</p></div><Link href={saved ? `/models/${saved.model_id}/evaluations` : "/models"}>{saved ? "← 该模型的评测" : "← 模型目录"}</Link></div>
    {!me.experimental_models_enabled ? <section className="empty-state"><h2>概率校准报告尚未启用</h2></section> : <>
      <aside className="model-scope"><span className="badge">UNVERIFIED</span><div><h2>历史概率证据，尚未取得正式资格</h2><p>上涨为正类，持平和下跌均为负类。{me.data_mode==="REPLAY" && "当前为合成 REPLAY 数据。"}样本可能相关，也可能被用于模型选择；报告不代表生产可靠性或晋升批准。</p></div></aside>
      {error && <div role="alert" className="alert">{error}<button onClick={()=>setReload(n=>n+1)}>重新加载</button></div>}
      {pending && <p role="status">正在重放并读取概率报告…</p>}
      {saved && report && metrics && <>
        <section className="evidence-card"><h2>独立评测窗口</h2><p><Link href={"/probability-checks/"+saved.id}>选择规则并核验概率质量 →</Link></p><dl className="model-metrics"><div><dt>Brier 分数</dt><dd>{metrics.brier ?? "无可用样本"}</dd></div><div><dt>ECE 校准误差</dt><dd>{metrics.ece ?? "无可用样本"}</dd></div><div><dt>评测样本 / 实际上涨</dt><dd>{metrics.sample_count} / {metrics.up_count}</dd></div></dl><p className="small muted">Brier 衡量概率与实际结果的平方误差；ECE 按分箱样本数汇总概率与上涨频率的差异。两者越低越好，但不单独构成通过门槛。以下数值保留报告原始十进制文本。</p></section>
        <section className="evidence-card"><h2>可靠性分箱</h2><p className="muted">按预测概率分成 10 箱。空箱保留为空，避免被误读成零误差。</p><div className="table-scroll" role="region" aria-label="概率可靠性分箱" tabIndex={0}><table><thead><tr><th>概率区间</th><th>样本数</th><th>平均预测概率</th><th>实际上涨频率</th></tr></thead><tbody>{metrics.bins.map((bin,i)=><tr key={i}><td>[{bin.lower}, {bin.upper}{bin.upper_inclusive ? "]" : ")"}</td><td>{bin.count}</td><td>{bin.mean_probability ?? "无样本"}</td><td>{bin.up_frequency ?? "无样本"}</td></tr>)}</tbody></table></div></section>
        <section className="evidence-card"><h2>校准与评测边界</h2><dl className="facts"><dt>预测跨度</dt><dd>{report.horizon_sessions} 个交易日</dd><dt>独立拟合样本</dt><dd>{report.calibration_ids.length}</dd><dt>后续评测样本</dt><dd>{report.evaluation_ids.length}</dd><dt>校准标签可用时间（美东）</dt><dd>{dateTime(report.calibration_available_at)}</dd><dt>报告信息截点（美东）</dt><dd>{dateTime(report.cutoff)}</dd><dt>间隔交易日</dt><dd>{report.embargo_sessions}</dd><dt>交易日历</dt><dd>{report.calendar_version}</dd><dt>校准方法</dt><dd>单调校准与线性插值</dd></dl><p className="small muted">超出拟合分数范围时使用最近端点概率。读取时由 Alpha 完整重放拟合、评测和原始证据。</p></section>
        <section className="evidence-card"><h2>固定评测样本</h2><div className="table-scroll" role="region" aria-label="概率逐样本结果" tabIndex={0}><table><thead><tr><th>样本身份</th><th>预测上涨概率</th><th>实际类别</th></tr></thead><tbody>{report.evaluation_ids.slice(samplePage*20,(samplePage+1)*20).map((labelId,i)=>{const index=samplePage*20+i;return <tr key={labelId}><td className="hash">{labelId}</td><td>{report.probabilities[index]}</td><td>{report.evaluation_targets[index]===1 ? "上涨（1）" : "持平或下跌（0）"}</td></tr>;})}</tbody></table></div><div className="model-pagination"><button disabled={samplePage===0} onClick={()=>setSamplePage(n=>n-1)}>上一页样本</button><span>第 {samplePage+1} 页</span><button disabled={(samplePage+1)*20>=report.evaluation_ids.length} onClick={()=>setSamplePage(n=>n+1)}>下一页样本</button></div></section>
        <section className="evidence-card"><h2>固定版本与复现依据</h2><dl className="facts"><dt>校准 ID</dt><dd className="hash">{saved.id}</dd><dt>模型 ID</dt><dd className="hash">{saved.model_id}</dd><dt>登记时间（美东）</dt><dd>{dateTime(saved.registered_at)}</dd><dt>登记内容哈希</dt><dd className="hash">{saved.content_hash}</dd><dt>报告哈希</dt><dd className="hash">{saved.report_hash}</dd></dl><details className="model-identity"><summary>核对拟合窗口、结点与代码身份</summary><pre className="report-manifest">{JSON.stringify({method:report.method,model_artifact_hash:report.model_artifact_hash,model_sha256:report.model_sha256,code_sha256:report.code_sha256,calibration_ids:report.calibration_ids,calibration_scores:report.calibration_scores,calibration_targets:report.calibration_targets,knots:report.knots},null,2)}</pre></details></section>
      </>}
    </>}
  </main>;
}
