// Run only in the isolated pi application probe container. No fixture LLM responses.
import assert from 'node:assert/strict';
import {randomUUID,randomBytes} from 'node:crypto';
import {createBff} from '../apps/bff/dist/app.js';
import {readConfig} from '../apps/bff/dist/config.js';
import {database} from '../apps/bff/dist/database.js';
import {hashPassword} from '../apps/bff/dist/password.js';
import {AlphaClient} from '../apps/bff/dist/alpha.js';
import {HermesClient} from '../apps/bff/dist/hermes-client.js';
import {HermesStore} from '../apps/bff/dist/hermes-store.js';
import {HermesResearchDriver} from '../apps/bff/dist/hermes-driver.js';
import {HermesResearch} from '../apps/bff/dist/hermes-research.js';
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
assert.equal(process.env.YTA_PI_APPLICATION_PROBE,'1');
const config=readConfig();
assert.equal(config.YTA_DATA_MODE,'REPLAY');
assert.ok(new URL(config.YTA_DATABASE_URL).pathname.endsWith('_test'));
const pool=database(config.YTA_DATABASE_URL),alpha=new AlphaClient(config);
const client=new HermesClient({origin:'http://hermes:8642',token:process.env.YTA_HERMES_PROBE_TOKEN,
  profile:'alpha-orchestrator',timeoutMs:240000});
const store=new HermesStore(pool,'REPLAY'),research=new HermesResearch(alpha);
const driver=new HermesResearchDriver(store,client,process.env.YTA_PI_MODEL,
  (run,state)=>research.complete(run,state),(run,signal)=>research.prepare(run,signal));
let app;
try{
  assert.equal((await pool.query('SELECT count(*)::int AS n FROM yta.research_runs')).rows[0].n,0,'Fresh isolated database required before starting the queue');
  let ready=false;
  for(let i=0;i<120;i++){
    try{const r=await fetch('http://hermes:8642/p/alpha-orchestrator/v1/models',{
      headers:{Authorization:'Bearer '+process.env.YTA_HERMES_PROBE_TOKEN},signal:AbortSignal.timeout(1000)});
      if(r.ok){ready=true;break;}}catch{}await wait(500);
  }
  assert.ok(ready,'Hermes ready');
  const owner=randomUUID(),email=owner+'@pi-app.test',password=randomBytes(24).toString('hex');
  await pool.query('INSERT INTO yta.users(id,email) VALUES($1,$2)',[owner,email]);
  await pool.query('INSERT INTO yta.login_credentials(owner_id,password_hash) VALUES($1,$2)',[owner,await hashPassword(password)]);
  app=await createBff({config,pool,driver,log:()=>{}});
  await app.app.listen(0,'127.0.0.1');const base=(await app.app.getUrl())+'/api/yta/v1';
  let cookie='',csrf='';
  const request=async(path,body,key)=>{
    const response=await fetch(base+path,{method:body===undefined?'GET':'POST',
      headers:{'Content-Type':'application/json',Origin:config.YTA_WEB_ORIGIN,
        ...(cookie?{Cookie:cookie,'X-CSRF-Token':csrf}:{}),...(key?{'Idempotency-Key':key}:{})},
      ...(body===undefined?{}:{body:JSON.stringify(body)}),signal:AbortSignal.timeout(20000)});
    const value=await response.json();assert.ok(response.ok,`HTTP ${path}: ${response.status} ${value.error?.code??''}`);
    return {response,value};
  };
  const login=await request('/auth/login',{email,password});
  cookie=login.response.headers.get('set-cookie').split(';')[0];csrf=login.value.csrf_token;
  const session=(await request('/sessions',{profile_id:'alpha-orchestrator'},randomUUID())).value.session_id;
  const key=randomUUID(),message={text:'研究 NVDA 日线，给出规则基线研究摘要、证据和风险，等待我确认冻结。',request_id:key};
  const id=(await request(`/sessions/${session}/messages`,message)).value.run_id;
  console.log(JSON.stringify({stage:'submitted',run_id:id,session_id:session}));
  let result;
  for(let i=0;i<300;i++){
    result=(await request('/runs/'+id)).value;
    if(['COMPLETED','FAILED','CANCELED','EXECUTION_UNKNOWN'].includes(result.status))break;
    await wait(1000);
  }
  console.log(JSON.stringify({stage:'observed',run_id:id,status:result.status,error:result.error??null}));
  assert.equal(result.status,'COMPLETED','Application must finish with validated report');
  assert.equal(result.report.status,'DRAFT_READY');assert.equal(result.report.draft_id,result.draft.draft_id);
  const context=await alpha.researchContext(owner,id);
  assert.deepEqual(new Set(context.receipts.map(r=>r.tool)),new Set(['market_snapshot','indicators']));
  assert.equal((await pool.query('SELECT count(*)::int AS n FROM yta.predictions WHERE owner_id=$1',[owner])).rows[0].n,0);
  assert.equal((await request(`/sessions/${session}/messages`,message)).value.run_id,id);
  const saved=await store.get(await app.research.getRun(owner,id));assert.ok(saved.upstreamRunId);
  const calls=await pool.query("SELECT observation FROM yta.hermes_run_events WHERE run_id=$1 AND observation->>'event'='tool.completed'",[id]);
  assert.ok(calls.rows.length>=2);assert.ok(calls.rows.every(r=>r.observation.error===false));
  const frozen=(await request('/predictions',{draft_id:result.draft.draft_id,confirmed:true},randomUUID())).value;
  assert.ok(frozen.prediction_id);
  await request('/auth/logout',{});
  console.log(JSON.stringify({stage:'verified',run_id:id,real_model:true,data_mode:'REPLAY',
    report:true,tool_receipts:context.receipts.length,hermes_tool_completions:calls.rows.length,
    authenticated_bff:true,no_auto_freeze:true,explicit_test_confirmation:true,
    prediction_id:frozen.prediction_id,same_request_same_run:true}));
}finally{if(app)await app.close();await pool.end();}
