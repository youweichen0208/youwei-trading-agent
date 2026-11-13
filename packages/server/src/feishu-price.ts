import { DomainError } from "./tasks.js";
import { AlpacaProvider } from "../../market/src/alpaca.js";
const names: Record<string, string> = {
  英伟达: "NVDA",
  nvidia: "NVDA",
  // Verified 2026-11-13: https://ir.spacex.com/faqs-and-contact/
  spacex: "SPCX",
  太空探索技术公司: "SPCX",
  apple: "AAPL",
  microsoft: "MSFT",
  tesla: "TSLA",
  amazon: "AMZN",
  google: "GOOGL",
  英特尔: "INTC",
  苹果: "AAPL",
  微软: "MSFT",
  特斯拉: "TSLA",
  亚马逊: "AMZN",
  谷歌: "GOOGL",
  英伟達: "NVDA",
  輝達: "NVDA",
};
export function lookupTarget(input: string): string | null {
  let text = input.trim().replace(/[？?。!！]+$/u, "");
  // Explicit analysis, history and saved research are never reduced to a quote.
  if (
    /(分析|研究|预测|走势|历史|昨天|昨日|收盘|图|建议|买|卖|为什么|为何|对比)/u.test(
      text,
    )
  )
    return null;
  text = text.replace(
    /^(?:(?:你好[，, ]*)?)(?:请问|请|能不能|能否|可以)?(?:帮我|给我)?(?:查一下|查询一下|查询|查看|查查|查|看看|看一下)?\s*/u,
    "",
  );
  text = text
    .replace(
      /(?:现在|目前)?(?:的)?(?:股票|股价|价格|行情)(?:现在|目前)?(?:是多少|多少|怎么样|如何)?(?:吗|呢)?$/u,
      "",
    )
    .replace(/(?:现在|目前)?(?:多少钱|多少)(?:吗|呢)?$/u, "")
    .trim();
  if (names[text.toLowerCase()]) return names[text.toLowerCase()]!;
  if (
    /^[A-Za-z][A-Za-z0-9.-]{0,9}$/.test(text) &&
    /(查|看|股价|价格|行情|多少)/u.test(input)
  )
    return text.toUpperCase();
  return null;
}
export function priceSymbol(input: string): string | null {
  const target = lookupTarget(input);
  // Unresolved company-like text needs clarification, not an invented ticker.
  return target && !/^[A-Z]{6,}$/.test(target) ? target : null;
}
export async function priceReply(
  input: string,
  provider?: Pick<AlpacaProvider, "latestTrade">,
  previous?: { target: string; at: string } | null,
): Promise<string | null> {
  const followup =
    /^(?:股票行情|行情|股价|股票|价格|公司资料|公司介绍)[？?。!！]*$/.test(
      input.trim(),
    );
  const remembered =
    previous &&
    Date.now() - new Date(previous.at).getTime() >= 0 &&
    Date.now() - new Date(previous.at).getTime() < 10 * 60_000
      ? previous.target
      : null;
  if (followup) {
    if (!remembered)
      return "你想查询哪家公司或股票？请提供公司名称或股票代码。";
    if (/公司/.test(input))
      return `你刚才查询的是 ${remembered}。请发送“介绍 ${remembered}”，我会以这家公司为对象查询资料。`;
    const symbol = priceSymbol(`查 ${remembered} 股价`);
    if (!symbol)
      return `你要查询的是 ${remembered} 的股票行情。目前尚未确认这家公司对应的可用股票代码，请提供股票代码和上市市场；我不会改查其他股票。`;
    return priceReply(`查 ${symbol} 股价`, provider);
  }
  const target = lookupTarget(input);
  const symbol = priceSymbol(input);
  if (target && !symbol)
    return `${target}：你想查公司资料，还是股票行情？当前名称尚未确认对应的股票代码。查行情请提供交易所和股票代码；查公司资料请发送“介绍 ${target}”。`;
  if (!symbol) return null;
  if (!provider) return `${symbol}：行情服务暂不可用，本次未启动研究。`;
  try {
    const quote = await provider.latestTrade(symbol, AbortSignal.timeout(6000));
    return `${symbol === "SPCX" ? "SpaceX（SPCX）" : symbol} · 最近成交价：${quote.price} USD\n成交时间：${quote.time}\n来源：Alpaca ${quote.feed.toUpperCase()}${quote.feed === "iex" ? "（仅 IEX 市场覆盖）" : ""}\n这是该数据源最近可用成交，不是实时买卖报价；休市时可能是此前成交。本次仅查行情，未启动研究。`;
  } catch (error) {
    if (error instanceof DomainError && error.code === "market_http_404")
      return `${symbol}：当前数据源没有找到可用成交行情，请确认股票代码、上市市场及数据覆盖。这个结果不能用来判断公司是否上市；如果你想查公司资料，请发送“介绍 ${symbol}”。`;
    return `${symbol}：本次行情查询暂未成功，请稍后重试。未使用旧价格替代，也未启动研究。`;
  }
}
