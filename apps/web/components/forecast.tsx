"use client";
import type { Forecast } from "@yta/contracts";
import { dateTime, percent } from "../lib/api";

export function ForecastFacts({ forecast: f }: { forecast: Forecast }) {
  return <><div className="forecast-value"><div><span className="eyebrow">5 交易日 · 规则基线</span><strong>{percent(f.expected_return)}</strong></div><span className="badge">{f.data_mode}</span></div>
    <div className="forecast-timeline"><div><span>基准交易日</span><strong>{f.base_session}</strong><small>收盘 {f.base_close} USD</small></div><div className="timeline-link">5 个交易日 →</div><div><span>目标交易日</span><strong>{f.target_session}</strong><small>以目标收盘核验</small></div></div>
    <dl className="facts"><dt>信息截点（美东）</dt><dd>{dateTime(f.as_of)}</dd><dt>模型</dt><dd>{f.model_version}</dd><dt>收益口径</dt><dd>拆股可比价格收益 · 不含分红</dd></dl><p className="muted small">固定规则估计，不提供上涨概率或收益保证。</p></>;
}
