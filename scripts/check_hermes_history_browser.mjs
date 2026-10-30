// Render the restored real-model record without resubmitting any model request.
import {readFileSync,openSync,closeSync,mkdirSync} from 'node:fs';
import {chromium,expect} from '@playwright/test';
import {execFileSync,spawn} from 'node:child_process';
import {randomBytes} from 'node:crypto';
import {once} from 'node:events';
import assert from 'node:assert/strict';
import {createBff} from '../apps/bff/dist/app.js';
import {readConfig} from '../apps/bff/dist/config.js';
import {hashPassword} from '../apps/bff/dist/password.js';
const name=JSON.parse(readFileSync('.local/research-context-restore.json','utf8')).verification.restored_database;
assert.match(name,/^yta_restore_[a-f0-9]{32}_test$/);
const db='postgresql://yta_app:local-yta-app@127.0.0.1:54329/'+name;
const token=randomBytes(32).toString('hex'),password=randomBytes(24).toString('hex');
const code=`import socket,uvicorn,os
from pydantic import SecretStr
from yta_alpha.api import create_app
from yta_alpha.settings import Settings
sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(128)
print(sock.getsockname()[1],flush=True)
app=create_app(Settings(_env_file=None,env='test',alpha_token=SecretStr(os.environ['PROBE_TOKEN']),database_url=SecretStr(os.environ['PROBE_DB'])))
uvicorn.Server(uvicorn.Config(app,log_level='error',access_log=False)).run(sockets=[sock])`;
const child=spawn('.venv/bin/python',['-c',code],{env:{...process.env,PROBE_TOKEN:token,PROBE_DB:db},stdio:['ignore','pipe','pipe']});
let server,web,browser;
const origin='http://127.0.0.1:3300';
try{
 const port=await new Promise((resolve,reject)=>{
  const timer=setTimeout(()=>reject(Error('Alpha startup timeout')),15000);
  child.stdout.once('data',data=>{clearTimeout(timer);resolve(Number(data.toString().trim()));});
  child.once('exit',()=>{clearTimeout(timer);reject(Error('Alpha startup failed'));});
 });
 const alpha='http://127.0.0.1:'+port;
 for(let n=0;n<100;n++){try{if((await fetch(alpha+'/health')).ok)break;}catch{}await new Promise(r=>setTimeout(r,100));}
 server=await createBff({startRunner:false,log:()=>{},config:readConfig({YTA_ENV:'test',YTA_DATABASE_URL:db,
   YTA_ALPHA_TOKEN:token,YTA_ALPHA_URL:alpha,YTA_WEB_ORIGIN:origin})});
 await server.app.listen(0,'127.0.0.1');
 const row=(await server.pool.query('SELECT r.id,r.owner_id,u.email FROM yta.research_runs r JOIN yta.users u ON u.id=r.owner_id')).rows[0];
 const report=(await server.research.runSummary(row.owner_id,row.id)).report;assert.ok(report);
 await server.pool.query('INSERT INTO yta.login_credentials(owner_id,password_hash) VALUES($1,$2)',[row.owner_id,await hashPassword(password)]);
 const before=(await server.pool.query('SELECT count(*)::int AS n FROM yta.hermes_run_requests')).rows[0].n;
 const prediction=(await server.pool.query('SELECT id FROM yta.predictions WHERE owner_id=$1',[row.owner_id])).rows[0].id;
 const webEnv={...process.env,YTA_BFF_ORIGIN:await server.app.getUrl()};
 const log=openSync('.local/hermes-history-browser-build.log','w',0o600);
 try{execFileSync('pnpm',['--filter','@yta/web','build'],{env:webEnv,stdio:['ignore',log,log],timeout:180000});}finally{closeSync(log);}
 web=spawn(process.execPath,['apps/web/node_modules/next/dist/bin/next','start','apps/web','--hostname','127.0.0.1','--port','3300'],{env:webEnv,stdio:'ignore'});
 for(let n=0;n<120;n++){try{if((await fetch(origin+'/login')).ok)break;}catch{}await new Promise(r=>setTimeout(r,250));}
 browser=await chromium.launch();const page=await browser.newPage({viewport:{width:1440,height:1000}});
 let eventRequests=0;
 const errors=[],origins=new Set();page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{origins.add(new URL(r.url()).origin);if(/\/runs\/[^/]+\/events/.test(r.url()))eventRequests++;});
 await page.goto(origin+'/research');await expect(page).toHaveURL(/\/login$/);
 await page.getByLabel('邮箱').fill(row.email);await page.getByLabel('密码').fill(password);
 await page.getByRole('button',{name:'进入研究工作台'}).click();
 await page.locator('.session-list button').first().click();
 await expect(page.locator('.message.assistant')).toContainText(report.summary);
 assert.ok(!(await page.locator('.message.assistant').innerText()).includes('"schema_version"'));
 const panel=page.locator('details.research-report');await panel.locator(':scope > summary').click();
 await expect(panel.locator('.report-sources > li')).toHaveCount(2);
 assert.equal(eventRequests,0,'Completed history must not replay SSE');
 await expect(page.locator('.message.assistant')).not.toContainText('\"schema_version\"');
 for(const risk of report.risks)await expect(panel).toContainText(risk);
 mkdirSync('.local/hermes-history-browser',{recursive:true});
 await page.screenshot({path:'.local/hermes-history-browser/desktop.png',fullPage:true});
 await page.reload();await page.locator('.session-list button').first().click();
 await expect(page.locator('.message.assistant')).toContainText(report.summary);
 await page.setViewportSize({width:390,height:844});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:'.local/hermes-history-browser/mobile.png',fullPage:true});
 await page.goto(origin+'/predictions/'+prediction);
 await expect(page.getByText(prediction,{exact:false}).first()).toBeVisible();
 await page.screenshot({path:'.local/hermes-history-browser/prediction.png',fullPage:true});
 await page.getByRole('button',{name:'退出',exact:true}).click();await expect(page).toHaveURL(/\/login$/);
 assert.deepEqual(errors,[]);assert.deepEqual([...origins],[origin]);
 assert.equal((await server.pool.query('SELECT count(*)::int AS n FROM yta.hermes_run_requests')).rows[0].n,before);
 console.log(JSON.stringify({real_model_record:true,authenticated_browser:true,report_matches:true,
  refresh_matches:true,mobile_no_overflow:true,page_errors:0,same_origin_only:true,model_resubmissions:0,completed_history_sse_requests:eventRequests,
  prediction_detail:true,logged_out:true,database:name}));
}finally{
 if(browser)await browser.close();
 if(web&&web.exitCode===null){web.kill('SIGTERM');await once(web,'exit');}
 if(server)await server.close();
 if(child.exitCode===null){child.kill('SIGTERM');await once(child,'exit');}
}
