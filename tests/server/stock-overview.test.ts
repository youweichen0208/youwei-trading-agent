import test from 'node:test';
import assert from 'node:assert/strict';
import {stockOverview} from '../../packages/server/src/stock-overview.js';
test('stock overview validates symbols and reports independent source failures', async () => {
 assert.throws(()=>stockOverview('../invalid'));
 const original=globalThis.fetch;
 const env={...process.env};
 try {
 process.env.MARKET_PROVIDER='alpaca';process.env.ALPACA_DATA_KEY='test';process.env.ALPACA_DATA_SECRET='test';process.env.ALPACA_DATA_FEED='iex';
 let calls=0;
 globalThis.fetch=async (input)=>{calls++;const url=String(input);
 if(url.includes('/news'))return Response.json({news:[{id:1,created_at:'2026-11-01T12:00:00Z',headline:'Test news',summary:'Summary',url:'https://example.com/news',symbols:['TEST'],source:'Fixture'}]});
 return new Response('',{status:503});};
 const result:any=await stockOverview('TEST');
 assert.equal(result.market.status,'unavailable');assert.equal(result.news.documents[0].payload.title,'Test news');
 const again=await stockOverview('TEST');assert.equal(again,result);assert.equal(calls,2);
 } finally {globalThis.fetch=original;process.env=env;}
});
