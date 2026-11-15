export type SuggestIcon = "trend" | "compare" | "portfolio" | "forecast";
export const researchSuggestions: {
  label: string;
  hint: string;
  icon: SuggestIcon;
  prompt: string;
}[] = [
  {
    label: "查一下 TSLA 最新走势",
    hint: "行情、均线与 RSI",
    icon: "trend",
    prompt:
      "查询特斯拉（TSLA）最近三个月走势，展示行情与 MA20、RSI 指标，并标明数据日期。",
  },
  {
    label: "对比 AAPL 与 MSFT",
    hint: "两条走势的相同与不同",
    icon: "compare",
    prompt:
      "对比 AAPL 与 MSFT 最近三个月的历史走势和技术指标，并说明数据来源。",
  },
  {
    label: "评估我的模拟持仓",
    hint: "估值日期、集中度与缺失数据",
    icon: "portfolio",
    prompt: "分析我选择的模拟持仓，说明估值日期、集中度和缺失数据。",
  },
  {
    label: "预测 NVDA 未来走势",
    hint: "先研究，再给出可验证的判断",
    icon: "forecast",
    prompt:
      "结合 NVDA 的近期新闻、公司披露、行业变化和相关历史经验，完整研究后预测未来5和20个交易日的走势，保存具体判断与验证日期。",
  },
];
export function HermesMark() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="m3 21 9-19 9 19M7 13h10"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinejoin="round"
      />
    </svg>
  );
}
export function SuggestionIcon({ icon }: { icon: SuggestIcon }) {
  const paths: Record<SuggestIcon, string> = {
    trend: "M3 16.5 7.5 11l3.5 3L17 6M17 6h-4m4 0v4",
    compare: "M4 16V8m5 8V5m5 11v-6m4 6V7",
    portfolio: "M3 8.5h14v7H3Zm2.5-3h9v3h-9Z",
    forecast: "M3 17c3.5-.5 6-6 9-6s4 4 5 4M13 6.5l2.5 2.5L18 6.5",
  };
  return (
    <svg
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[icon]} />
    </svg>
  );
}
export function WelcomeHeading() {
  return (
    <div className="welcome-heading">
      <HermesMark />
      <h1>今天想研究哪只股票？</h1>
      <p>直接提问。行情、指标与研究过程会同时出现在工作区，并保留数据来源。</p>
    </div>
  );
}
