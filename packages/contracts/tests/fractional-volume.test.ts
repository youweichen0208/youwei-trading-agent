import {expect,it} from 'vitest';
import {marketEvidenceSchema} from '../src/index.js';
it('preserves decimal share volume and rejects numeric fractions',()=>{
 const snapshot={symbol:'NVDA',provider:'massive',source_id:'test',data_mode:'LIVE_RESEARCH',adjustment:'RAW',as_of:'2026-10-29T22:00:00Z',actions_verified_through:'2026-10-29',splits:[],bars:[{session:'2026-10-29',open:'1',high:'2',low:'1',close:'2',volume:'98684721.771355',available_at:'2026-10-29T22:00:00Z',retrieved_at:'2026-10-29T22:00:00Z',revision:'test'}]};
 const data={snapshot,data_snapshot_hash:'a'.repeat(64)};
 expect(marketEvidenceSchema.parse(data).snapshot.bars[0].volume).toBe('98684721.771355');
 for(const volume of [0.25,-1,'-0.25',true]) expect(marketEvidenceSchema.safeParse({...data,snapshot:{...snapshot,bars:[{...snapshot.bars[0],volume}]}}).success).toBe(false);
});
