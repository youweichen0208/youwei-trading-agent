"use client";
import Link from "next/link";
import type { Me } from "../lib/api";
import { StockChart } from "./chart";
import { MarketFreshness } from "./market-freshness";

export function Overview({ me }: {me: Me}) {
  return <main className="records-page overview-page">
    <div className="records-heading"><div><p className="eyebrow">市场证据 / 研究起点</p><h1>NVDA · 研究概览</h1><p className="muted">先看行情依据，再留下可验证的判断。</p></div><Link className="primary button-link" href="/research">进入研究工作台 →</Link></div>
    <div className="overview-grid">
      <StockChart/>
      <aside className="overview-notes" aria-label="研究范围与使用说明">
        <MarketFreshness mode={me.data_mode}/>
        <section className="evidence-card"><p className="eyebrow">当前研究范围</p><h2>一个标的，一套核验口径</h2><dl className="facts"><dt>证券</dt><dd>NVDA · NVIDIA</dd><dt>行情频率</dt><dd>日线</dd><dt>预测跨度</dt><dd>5 个交易所交易日</dd><dt>预测方法</dt><dd>20 日动量规则基线</dd><dt>比较基准</dt><dd>零收益预测</dd><dt>收益口径</dt><dd>拆股可比价格收益，不含现金分红再投资</dd></dl></section>
        <section className="evidence-card"><p className="eyebrow">数据性质</p><h2>{me.data_mode === "REPLAY" ? "合成回放演练" : "前瞻研究环境"}</h2><p className="muted">{me.data_mode === "REPLAY" ? "此处行情用于验证研究与结算流程，不代表真实 NVDA 行情。演练记录独立保存，不计入正式前瞻成绩。" : "行情区显示实际来源、信息截点与可用状态。数据未就绪时，请等待核验后再开展研究。"}</p><p className="small muted">图表与指标共用同一快照；来源时间以美东时间显示。</p></section>
        <section className="evidence-card"><p className="eyebrow">从研究到核验</p><ol className="overview-steps"><li><strong>研究价格与证据</strong><span>在工作台提出问题，检查指标与预测草稿。</span></li><li><strong>确认后冻结</strong><span>检查基准日、目标日与来源，由你确认保存判断。</span></li><li><strong>到期回看</strong><span>结算结果追加保存，原判断和历史版本保留。</span></li></ol><Link href="/predictions">查看预测记录 →</Link></section>
      </aside>
    </div>
  </main>;
}
