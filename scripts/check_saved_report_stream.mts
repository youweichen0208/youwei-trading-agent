import {readFileSync,writeFileSync} from 'node:fs';
import assert from 'node:assert/strict';
import {database} from '../apps/bff/src/database.js';
import {reportStreamSummary} from '../apps/web/lib/report-stream.js';
const state=JSON.parse(readFileSync('.local/hermes-browser-state.json','utf8'));
assert.match(state.database,/^yta_pi_app_[a-f0-9]+_test$/);
const pool=database('postgresql://yta_app:local-yta-app@127.0.0.1:54329/'+state.database);
try{
 const runs=await pool.query('SELECT id FROM yta.research_runs');assert.equal(runs.rows.length,1);
 const id=runs.rows[0].id;
 const report=(await pool.query("SELECT event->'payload'->'report' AS report FROM yta.agent_events WHERE run_id=$1 AND type='message.completed'",[id])).rows[0].report;
 const chunks=(await pool.query("SELECT event->'payload'->>'text' AS text FROM yta.agent_events WHERE run_id=$1 AND type='message.delta' ORDER BY sequence",[id])).rows;
 let raw='',shown='',updates=0;
 for(const row of chunks){raw+=row.text;const current=reportStreamSummary(raw);
  assert.ok(report.summary.startsWith(current));assert.ok(current.startsWith(shown));
  if(current!==shown)updates++;shown=current;
 }
 assert.equal(shown,report.summary);assert.ok(updates>1);
 const result={source:'saved actual pi model browser run',run_id:id,stream_chunks:chunks.length,
  progressive_summary_updates:updates,final_summary_matches_verified_report:true,new_model_calls:0};
 writeFileSync('infra/verification/report-stream-observation.json',JSON.stringify(result,null,2)+'\n');
 console.log(JSON.stringify(result));
}finally{await pool.end();}
