// Explicit isolated local harness using the standard configured driver factory.
import assert from 'node:assert/strict';
import {createBff} from '../apps/bff/dist/app.js';
import {readConfig} from '../apps/bff/dist/config.js';
import {database} from '../apps/bff/dist/database.js';
assert.equal(process.env.YTA_PI_BROWSER_PROBE,'1');
const config=readConfig({...process.env,YTA_RESEARCH_DRIVER:'HERMES',
 YTA_HERMES_ORIGIN:'http://hermes:8642',YTA_HERMES_TOKEN:process.env.YTA_HERMES_PROBE_TOKEN,
 YTA_HERMES_MODEL:process.env.YTA_PI_MODEL});assert.equal(config.YTA_DATA_MODE,'REPLAY');
assert.match(new URL(config.YTA_DATABASE_URL).pathname,/^\/yta_pi_app_[a-f0-9]+_test$/);
const pool=database(config.YTA_DATABASE_URL);
const count=(await pool.query('SELECT count(*)::int AS n FROM yta.research_runs')).rows[0].n;
assert.equal(count,0,'New browser probe requires an empty queue; reconcile existing runs separately');
let ready=false;
for(let i=0;i<120;i++){
 try{const r=await fetch('http://hermes:8642/p/alpha-orchestrator/v1/models',{
  headers:{Authorization:'Bearer '+process.env.YTA_HERMES_PROBE_TOKEN},signal:AbortSignal.timeout(1000)});
  if(r.ok){ready=true;break;}}catch{}await new Promise(r=>setTimeout(r,500));
}
assert.ok(ready,'Hermes readiness');
const bff=await createBff({config,pool,log:()=>{}});
await bff.app.listen(3100,'0.0.0.0');
console.log(JSON.stringify({ready:true,mode:'REPLAY',driver:'hermes'}));
let closing=false;
for(const name of ['SIGTERM','SIGINT'])process.on(name,async()=>{
 if(closing)return;closing=true;await bff.close();await pool.end();process.exit(0);
});
