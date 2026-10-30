"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { experimentalModelPageSchema, type ExperimentalModelSummary } from "@yta/contracts";
import { api, dateTime, HttpError, type Me } from "../lib/api";

const horizons = ["", "1", "3", "5", "10", "20"];
const pageSize = 10;
type ModelPage = {items: ExperimentalModelSummary[]; next_cursor: string | null};

export function Models({me}: {me: Me}) {
  const router = useRouter();
  const [horizon, setHorizon] = useState("");
  const [trail, setTrail] = useState<string[]>([]);
  const [page, setPage] = useState<ModelPage>();
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [pending, setPending] = useState(false);
  const cursor = trail.at(-1);
  useEffect(() => {
    if (!me.experimental_models_enabled) return;
    const abort = new AbortController();
    setPending(true); setPage(undefined); setError("");
    const query = new URLSearchParams({limit: String(pageSize)});
    if (horizon) query.set("horizon_sessions", horizon);
    if (cursor) query.set("cursor", cursor);
    void api<unknown>("/experimental-models?" + query, {signal: abort.signal}).then(value => {
      const parsed = experimentalModelPageSchema.safeParse(value);
      if (!parsed.success || parsed.data.items.length > pageSize
        || parsed.data.items.some(row => row.data_mode !== me.data_mode
          || (horizon && String(row.horizon_sessions) !== horizon) || row.id === cursor)
        || (cursor && parsed.data.next_cursor === cursor)) {
        throw new Error("模型目录格式或数据模式不一致，请重新加载。");
      }
      if (!abort.signal.aborted) setPage(parsed.data);
    }).catch(e => {
      if (abort.signal.aborted) return;
      if (e instanceof HttpError && e.status === 401) {router.replace("/login"); return;}
      setError(e instanceof HttpError && e.code === "EXPERIMENTAL_MODELS_DISABLED"
        ? "实验模型目录已关闭。可返回预测记录，查看已有研究。"
        : "模型目录暂时无法读取，请重新加载。已有预测记录不受影响。");
    }).finally(() => {if (!abort.signal.aborted) setPending(false);});
    return () => abort.abort();
  }, [cursor, horizon, reload, me.data_mode, me.experimental_models_enabled, router]);

  return <main className="records-page models-page">
    <div className="records-heading"><div><p className="eyebrow">版本登记 / 实验观察</p><h1>实验模型</h1><p className="muted">查看训练版本与保留窗口误差，追溯每一次模型登记。</p></div><Link href="/predictions">← 预测记录</Link></div>
    {!me.experimental_models_enabled ? <section className="empty-state"><h2>实验模型目录尚未启用</h2><p>当前可继续查看预测记录与基线评测。</p><Link href="/evaluations">查看基线评测 →</Link></section> : <>
      <aside className="model-scope" aria-label="实验指标口径"><span className="badge">未经正式验收</span><div><h2>保留窗口误差 ≠ 最终评测成绩</h2><p>这些窗口可能随后用于概率校准。不同版本的样本与时间范围可能不同，不能据此直接排名或选择冠军模型。{me.data_mode === "REPLAY" && " 当前为合成历史回放，不代表真实行情表现。"}</p></div></aside>
      <p><Link href="/model-proposals">我的模型提案 →</Link> · <Link href="/backtests">模型回测 →</Link></p>
      <div className="model-filter"><label htmlFor="model-horizon">预测跨度</label><select id="model-horizon" value={horizon} onChange={event => {setHorizon(event.target.value); setTrail([]);}}>{horizons.map(value => <option value={value} key={value}>{value ? `${value} 个交易日` : "全部跨度"}</option>)}</select><span className="muted">按登记时间从新到旧</span></div>
      {error && <div role="alert" className="alert">{error}<button onClick={() => setReload(n => n + 1)}>重新加载</button></div>}
      {pending && <p role="status">正在读取实验模型…</p>}
      {page && <>
        {!page.items.length ? <section className="empty-state"><h2>暂无匹配的实验模型</h2><p>当前数据模式和跨度下还没有登记记录。</p>{horizon && <button onClick={() => {setHorizon(""); setTrail([]);}}>查看全部跨度</button>}</section> : <div className="model-list">{page.items.map(model => <article className="model-entry" key={model.id} aria-label={`${model.horizon_sessions}交易日模型 ${model.registration_sha256.slice(0, 12)}`}>
          <div className="model-horizon"><strong>{model.horizon_sessions}</strong><span>交易日</span><span className="badge">实验模型</span></div>
          <div className="model-body"><div className="section-heading"><h2>LightGBM <code>{model.registration_sha256.slice(0, 12)}</code></h2><span className="chip">{model.data_mode}</span></div><p className="small muted">登记于 {dateTime(model.registered_at)} 美东 · 训练截点 {dateTime(model.training_cutoff)} 美东</p>
            <dl className="model-metrics"><div><dt>训练样本</dt><dd>{model.training_samples}</dd></div><div><dt>保留窗口样本</dt><dd>{model.reserved_window_samples}</dd></div><div><dt>模型平均绝对误差</dt><dd>{model.reserved_window_mae}</dd></div><div><dt>零收益对照误差</dt><dd>{model.reserved_window_zero_return_mae}</dd></div></dl><p className="small muted">误差按收益率原值展示：0.01 为 1 个百分点。样本数不代表独立样本数。</p>
            <Link className="button-link" href={`/models/${model.id}/evaluations`}>查看独立窗口评测 →</Link> <Link href={`/backtests?model_id=${model.id}`}>查看该模型回测 →</Link> <Link href={`/models/${model.id}/training-evidence`}>查看训练证据 →</Link>
            <details className="model-identity"><summary>核对完整版本与时间</summary><dl className="facts"><dt>模型 ID</dt><dd>{model.id}</dd><dt>模型 SHA256</dt><dd>{model.model_sha256}</dd><dt>登记 SHA256</dt><dd>{model.registration_sha256}</dd><dt>登记时间 UTC</dt><dd>{model.registered_at}</dd><dt>训练截点 UTC</dt><dd>{model.training_cutoff}</dd></dl></details>
          </div>
        </article>)}</div>}
      </>}
      <div className="model-pagination"><button disabled={pending || !trail.length} onClick={() => setTrail(old => old.slice(0, -1))}>上一页</button><span>第 {trail.length + 1} 页</span><button disabled={pending || !page?.next_cursor} onClick={() => {if (page?.next_cursor) setTrail(old => [...old, page.next_cursor!]);}}>下一页</button></div>
    </>}
  </main>;
}
