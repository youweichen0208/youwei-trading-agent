import { useEffect, useState } from "react";
import { marketFreshnessSchema } from "@yta/contracts";
import { api, dateTime, type Me } from "../lib/api";

export function MarketFreshness({ mode }: {mode: Me["data_mode"]}) {
  const [value, setValue] = useState<ReturnType<typeof marketFreshnessSchema.parse>>();
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const check = () => {
      if (document.visibilityState === "visible") {
        setValue(undefined); setRefresh(count => count + 1);
      }
    };
    const interval = setInterval(check, 30000);
    document.addEventListener("visibilitychange", check);
    return () => { clearInterval(interval); document.removeEventListener("visibilitychange", check); };
  }, []);
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    setValue(undefined); setError(false); setLoading(true);
    api<unknown>("/stocks/NVDA/freshness", {signal: controller.signal}).then(raw => {
      const parsed = marketFreshnessSchema.parse(raw);
      if (parsed.data_mode !== mode) throw new Error("Wrong mode");
      if (active) setValue(parsed);
    }).catch(() => { if (active) setError(true); }).finally(() => {
      clearTimeout(timeout); if (active) setLoading(false);
    });
    return () => { active = false; clearTimeout(timeout); controller.abort(); };
  }, [mode, refresh]);
  const heading = !value ? "正在核对行情状态" : value.status === "STALE" ? "行情已过期"
    : value.status === "UNAVAILABLE" ? "行情不可用" : mode === "REPLAY" ? "回放数据可用" : "上次检查时行情可用";
  return <section className="evidence-card market-freshness" aria-label="行情可用状态">
    <p className="eyebrow">行情可用状态</p>
    <div role="status"><h2>{error ? "行情状态未知" : heading}</h2></div>
    {error && <p role="alert" className="alert">无法核对最新状态，请稍后重试。不能据此判断行情可用。</p>}
    {value?.status === "STALE" && <p className="alert">当前只保留上次有效证据。行情更新失败或已跨过新的交易日收盘，暂不能用于新的研究。</p>}
    {value?.status === "UNAVAILABLE" && <p className="alert">尚无可用的已核验行情，请等待数据就绪。</p>}
    {value?.status === "FRESH" && <p className="muted">{mode === "REPLAY" ? "合成数据用于流程演练，不代表真实行情。" : "这是日线数据状态，不代表实时行情；研究时仍会再次核验。"}</p>}
    {value?.as_of && <p className="small muted">{value.status === "STALE" ? "上次有效" : "行情"}信息截点：{dateTime(value.as_of)}（纽约时间）<br/>来源：{value.source_id}</p>}
    <p className="small muted">页面可见时每 30 秒检查，也可手动更新。</p>
    <button disabled={loading} onClick={() => { setValue(undefined); setRefresh(count => count + 1); }}>检查行情状态</button>
  </section>;
}
