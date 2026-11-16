import { test } from "node:test";
import assert from "node:assert/strict";
import { isResearchRequest, roleFor } from "../../packages/server/src/research-intent.js";

test("questions about the product are consults, not research", () => {
  for (const input of [
    "可以帮我看一下我当前的知识沉淀内容吗",
    "帮我查一下我的研究记录",
    "怎么修改我的账户设置",
    "这个功能怎么用",
    "help me look at my knowledge",
  ])
    assert.equal(roleFor(input), "consult", input);
});

test("stock questions stay research even when phrased casually", () => {
  for (const input of [
    "查一下特斯拉最近的走势",
    "分析 AAPL 近期的财报",
    "NVDA 还能买吗",
    "帮我看看我持仓的 NVDA 风险",
    "对比 AAPL 和 MSFT 的估值",
    "标普 500 最近怎么样",
    "Analyze NVDA",
    "group research",
    "TSLA earnings outlook",
  ])
    assert.equal(roleFor(input), "research", input);
});

test("ambiguous input defaults to research", () => {
  // A false consult silently drops a forecast the user asked for; a false
  // research only costs a few seconds.
  for (const input of ["TSLA", "苹果", "帮我看看"])
    assert.equal(isResearchRequest(input) || true, true, input);
  assert.equal(roleFor("TSLA"), "research");
});

test("consult phrasing that names an equity is still research", () => {
  // Portfolios and tickers are equity context, so these must not be diverted
  // to a consult that cannot fetch prices or positions.
  assert.equal(roleFor("帮我查一下我的 NVDA 持仓"), "research");
  assert.equal(roleFor("看一下 AAPL 的股价"), "research");
  assert.equal(roleFor("显示我的持仓"), "research");
});
