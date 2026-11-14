export const researchSuggestions = [
  {
    label: "查一下 TSLA 最新走势",
    prompt:
      "查询特斯拉（TSLA）最近三个月走势，展示行情与 MA20、RSI 指标，并标明数据日期。",
  },
  {
    label: "对比 AAPL 与 MSFT",
    prompt:
      "对比 AAPL 与 MSFT 最近三个月的历史走势和技术指标，并说明数据来源。",
  },
  {
    label: "评估我的模拟持仓",
    prompt: "分析我选择的模拟持仓，说明估值日期、集中度和缺失数据。",
  },
  {
    label: "预测 NVDA 未来走势",
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
export function WelcomeHeading() {
  return (
    <div className="welcome-heading">
      <HermesMark />
      <h1>今天想研究哪只股票？</h1>
      <p>
        直接提问，行情、指标与研究结果会随分析过程出现在右侧工作区。
      </p>
    </div>
  );
}
