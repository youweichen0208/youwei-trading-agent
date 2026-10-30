import {chromium,expect} from '@playwright/test';
import {readFileSync,mkdirSync} from 'node:fs';
import {randomUUID,randomBytes} from 'node:crypto';
import assert from 'node:assert/strict';
import {database} from '../apps/bff/dist/database.js';
import {hashPassword} from '../apps/bff/dist/password.js';
const realModel=process.env.YTA_PI_BROWSER_PROBE==='1';
const configured=process.env.YTA_CONFIGURED_HERMES_PROBE==='1';
assert.ok(!configured||realModel,'Configured probe requires real-model mode');
const state=JSON.parse(readFileSync(configured?'.local/hermes-configured-state.json':realModel?'.local/hermes-browser-state.json':'.local/mvp-container-state.json','utf8'));
assert.match(state.database,realModel?/^yta_pi_app_[a-f0-9]{12}_test$/:/^yta_candidate_[a-f0-9]{24}_test$/);
const pool=database('postgresql://yta_app:local-yta-app@127.0.0.1:54329/'+state.database);
const previous=(await pool.query('SELECT r.id,r.session_id,r.owner_id,u.email FROM yta.research_runs r JOIN yta.users u ON u.id=r.owner_id ORDER BY r.created_at')).rows;
assert.ok(previous.length<=1,'Reconcile unexpected prior runs');
const prior=previous[0];
const owner=prior?.owner_id??randomUUID(),email=prior?.email??owner+'@candidate.test',password=randomBytes(24).toString('hex');
let browser;
try{
 if(!prior)await pool.query('INSERT INTO yta.users(id,email) VALUES($1,$2)',[owner,email]);
 await pool.query('INSERT INTO yta.login_credentials(owner_id,password_hash) VALUES($1,$2)',[owner,await hashPassword(password)]);
 browser=await chromium.launch();const page=await browser.newPage({viewport:{width:1440,height:1000}});
 const errors=[],origins=new Set();page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>origins.add(new URL(r.url()).origin));
 await page.goto('http://localhost:3300/research');await expect(page).toHaveURL(/\/login$/);
 await page.getByLabel('邮箱').fill(email);await page.getByLabel('密码').fill(password);
 await page.getByRole('button',{name:'进入研究工作台'}).click();
 let runId=prior?.id;
 if(prior){
  await page.locator('.session-list button').first().click();
 }else{
  await page.getByLabel('研究问题').fill('验证 NVDA 五交易日规则基线、冻结和结算。');
  const admitted=page.waitForResponse(r=>r.request().method()==='POST'&&/\/sessions\/[^/]+\/messages$/.test(new URL(r.url()).pathname));
  await page.getByRole('button',{name:'发送 →'}).click();
  runId=(await (await admitted).json()).run_id;
 }
 await expect(page.getByText('研究已完成',{exact:true})).toBeVisible({timeout:300000});
 const run=await (await page.request.get('http://localhost:3300/api/yta/v1/runs/'+runId)).json();
 assert.equal(run.report.status,'DRAFT_READY');assert.equal(run.report.data_mode,'REPLAY');
 assert.equal(run.report.draft_id,run.draft.draft_id);
 if(realModel){
  await expect(page.getByText('合成行情 · 模型研究 · 不计入真实前瞻成绩')).toBeVisible();
  assert.equal((await pool.query('SELECT count(*)::int AS n FROM yta.hermes_run_requests')).rows[0].n,1);
  assert.equal((await pool.query('SELECT count(*)::int AS n FROM yta.research_tool_observations')).rows[0].n,2);
 }
 await expect(page.locator('.canvas-panel')).toContainText('0.50%');
 assert.equal((await pool.query('SELECT count(*)::int AS n FROM yta.predictions WHERE owner_id=$1',[owner])).rows[0].n,0);
 await page.getByRole('button',{name:'检查并冻结预测'}).click();
 await expect(page.getByRole('dialog')).toContainText('合成回放记录');
 await page.getByRole('button',{name:'确认冻结',exact:true}).click();
 await page.getByRole('link',{name:'查看冻结记录'}).click();
 await expect(page.getByRole('heading',{name:'预测详情'})).toBeVisible();
 await page.getByRole('button',{name:'模拟到期并结算'}).click();
 await expect(page.getByText('2.00%',{exact:true})).toBeVisible();
 await expect(page.getByText('1.50 个百分点')).toBeVisible();
 await page.reload();await expect(page.getByText('结算版本 1',{exact:true})).toBeVisible();
 const screenshots=configured?'.local/hermes-configured-browser':realModel?'.local/hermes-live-browser':'.local/mvp-container-browser';
 mkdirSync(screenshots,{recursive:true});
 await page.screenshot({path:screenshots+'/settled.png',fullPage:true});
 let streams=0;page.on('request',r=>{if(/\/runs\/[^/]+\/events/.test(r.url()))streams++;});
 await page.getByRole('link',{name:'研究工作台',exact:true}).click();
 await page.locator('.session-list button').first().click();
 await expect(page.locator('.message.assistant')).toContainText(run.report.summary);
 await page.locator('details.research-report > summary').click();
 await expect(page.locator('.report-sources > li')).toHaveCount(2);assert.equal(streams,0);
 await page.setViewportSize({width:390,height:844});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:screenshots+'/history-mobile.png',fullPage:true});
 await page.getByRole('button',{name:'退出',exact:true}).click();await expect(page).toHaveURL(/\/login$/);
 assert.deepEqual(errors,[]);assert.deepEqual([...origins],['http://localhost:3300']);
 const counts=(await pool.query('SELECT (SELECT count(*)::int FROM yta.predictions WHERE owner_id=$1) AS predictions,(SELECT count(*)::int FROM yta.prediction_outcomes) AS outcomes',[owner])).rows[0];
 assert.equal(counts.predictions,1);assert.equal(counts.outcomes,1);
 console.log(JSON.stringify({scope:realModel?'real pi model browser flow on synthetic REPLAY':'sealed local candidate synthetic REPLAY browser flow',run_id:runId,
  authenticated:true,csrf:true,no_auto_freeze:true,explicit_confirmation:true,settlement:true,
  refresh:true,history_no_sse:true,mobile_no_overflow:true,page_errors:0,same_origin_only:true,
  predictions:1,outcomes:1,real_model:realModel,live_market:false,logged_out:true}));
}finally{if(browser)await browser.close();await pool.end();}
