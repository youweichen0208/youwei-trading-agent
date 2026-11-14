import { configuredMarketProvider } from '../../market/src/alpaca.js';
import { ResearchSources } from './research-sources.js';
import { symbol as symbolSchema } from './business.js';

// Public market data only. Personal research stays in the owner-scoped stocks API.
const cache = new Map<string, { until: number; result: Promise<unknown> }>();
export function stockOverview(input: string) {
  const symbol = symbolSchema.parse(input);
  const cached = cache.get(symbol);
  if (cached && cached.until > Date.now()) return cached.result;
  const result = (async () => {
    const now = Date.now();
    // Exclude today's potentially incomplete daily candle.
    const end = new Date(now - 86400000).toISOString().slice(0, 10);
    const start = new Date(now - 100 * 86400000).toISOString().slice(0, 10);
    const [prices, news] = await Promise.allSettled([
      Promise.resolve().then(() => {
        const provider = configuredMarketProvider();
        if (!provider) throw new Error('not_configured');
        return provider.bars({symbol, start, end, adjustment:'split'}, AbortSignal.timeout(15000))
          .then(bars => ({bars, source:provider.source, feed:provider.feed}));
      }),
      new ResearchSources().market_news({symbol, start:new Date(now - 14 * 86400000).toISOString().slice(0,10)}),
    ]);
    return {symbol, fetchedAt:new Date().toISOString(), start, end,
      market: prices.status === 'fulfilled' ? {...prices.value, status:'available'} : {status:'unavailable',bars:[]},
      news: news.status === 'fulfilled' ? news.value : {status:'unavailable', documents:[]},
    };
  })();
  if (cache.size >= 200) cache.delete(cache.keys().next().value!);
  cache.set(symbol, {until:Date.now()+60000,result});
  return result;
}
