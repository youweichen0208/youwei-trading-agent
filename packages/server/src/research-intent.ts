/**
 * Every message row needs a task (messages.task_id is NOT NULL), but not every
 * message is stock research. Classify at admission so a question like
 * 「帮我看一下我的知识沉淀」 does not freeze evidence and dispatch a full
 * prediction/portfolio pipeline.
 *
 * Deliberately conservative: only clearly non-research intents are diverted.
 * Ambiguous input stays research — a false 研究 costs seconds, a false consult
 * silently drops a real forecast request.
 */
// Bare uppercase words are usually shouting or acronyms, not tickers.
const notTickers = /^(?:OK|YES|NO|HI|THX|PDF|API|URL|UI|UX|ID|IP|CSV|JSON|AI|FAQ)$/;

const equityContext =
  /(?:\b(?:[A-Z]{1,5})\b|股票|个股|股价|行情|走势|涨|跌|财报|业绩|指引|估值|市值|EPS|PE|营收|毛利率|回购|分红|拆股|板块|指数|纳指|标普|道指|纳指100|做多|做空|持仓|加仓|减仓|买入|卖出|止盈|止损|目标价|预测|复盘|回测)/;

const researchIntent =
  /(?:研究|分析|走势|预测|涨|跌|估值|财报|业绩|指引|新闻|影响|持仓|组合|风险|复盘|该不该|能不能买|值不值得|目标价|对比|比较|评估|怎么看|怎么样|\bresearch\b|\banaly[sz]e\b|\bforecast\b|\boutlook\b|\bvaluation\b|\bearnings\b|\bguidance\b|\bthesis\b)/i;

const consultIntent =
  /(?:看|查|列|显示|找|打开|统计|汇总|知道|了解)(?:一下)?(?:我的|我)?(?:知识|沉淀|研究记录|历史|功能|设置|账户|用法|刚才|上次)/;

/** True when the message is stock research rather than a product/self query. */
const hasTicker = (text: string) =>
  (text.match(/\b[A-Z]{1,5}\b/g) ?? []).some((word) => !notTickers.test(word));

export function isResearchRequest(input: string) {
  const text = input.trim();
  if (!text) return false;
  const equity =
    equityContext.test(text.replace(/\b[A-Z]{1,5}\b/g, (word) => (notTickers.test(word) ? "" : word))) ||
    hasTicker(text);
  // Order matters: 「我的研究记录」contains the word 研究 but asks about saved
  // records, so the self-query pattern must win unless an equity is named.
  if (consultIntent.test(text) && !equity) return false;
  return researchIntent.test(text) || equity;
}

/** Task role for a user message: research runs the pipeline, consult answers directly. */
export function roleFor(input: string): "research" | "consult" {
  return isResearchRequest(input) ? "research" : "consult";
}
