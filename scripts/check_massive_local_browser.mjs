import {chromium,expect} from '@playwright/test';
import {readFileSync,writeFileSync,existsSync,mkdirSync} from 'node:fs';
import {randomUUID,randomBytes} from 'node:crypto';
import assert from 'node:assert/strict';
import {database} from '../apps/bff/dist/database.js';
import {hashPassword} from '../apps/bff/dist/password.js';
const state=JSON.parse(readFileSync('.local/massive-application-state.json','utf8'));
assert.match(state.database,/^yta_massive_[a-f0-9]{12}_test$/);
const pool=database('postgresql://yta_app:local-yta-app@127.0.0.1:54329/'+state.database);
const accessFile='.local/massive-access.json';
let access;
if(existsSync(accessFile)) access=JSON.parse(readFileSync(accessFile,'utf8'));
else {
 access={owner:randomUUID(),email:'local-massive@personal.test',password:randomBytes(24).toString('hex'),url:'http://localhost:3301'};
 await pool.query('BEGIN');
 try {
 await pool.query('INSERT INTO yta.users(id,email) VALUES($1,$2)',[access.owner,access.email]);
 await pool.query('INSERT INTO yta.login_credentials(owner_id,password_hash) VALUES($1,$2)',[access.owner,await hashPassword(access.password)]);
 await pool.query('COMMIT');
 writeFileSync(accessFile,JSON.stringify(access,null,2)+'\n',{mode:0o600,flag:'wx'});
 }catch(e){await pool.query('ROLLBACK');throw e;}
}
const browser=await chromium.launch();
try {
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 const errors=[],origins=new Set();
 page.on('pageerror',e=>errors.push(e.message));
 page.on('request',r=>origins.add(new URL(r.url()).origin));
 await page.goto(access.url+'/research');
 await expect(page).toHaveURL(/\/login$/);
 await page.getByLabel('邮箱').fill(access.email);
 await page.getByLabel('密码').fill(access.password);
 await page.getByRole('button',{name:'进入研究工作台'}).click();
 await expect(page).toHaveURL(/\/research$/);
 let freshness;
 for(let i=0;i<60;i++){
  freshness=await(await page.request.get(access.url+'/api/yta/v1/stocks/NVDA/freshness')).json();
  if(freshness.status==='FRESH')break;
  await new Promise(r=>setTimeout(r,500));
 }
 assert.equal(freshness.status,'FRESH');assert.equal(freshness.data_mode,'LIVE_RESEARCH');
 const barsResponse=await page.request.get(access.url+'/api/yta/v1/stocks/NVDA/bars');
 assert.equal(barsResponse.status(),200);
 const snapshot=await barsResponse.json();
 assert.equal(snapshot.provider,'massive');assert.equal(snapshot.data_mode,'LIVE_RESEARCH');
 assert.equal(snapshot.bars.length,500);
 assert.ok(snapshot.bars.some(b=>typeof b.volume==='string'&&b.volume.includes('.')));
 const indicators=await page.request.get(access.url+'/api/yta/v1/stocks/NVDA/indicators');
 assert.equal(indicators.status(),200);
 await expect(page.locator('.chart-source')).toContainText('massive');
 mkdirSync('.local/massive-browser',{recursive:true});
 await page.screenshot({path:'.local/massive-browser/desktop.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:'.local/massive-browser/mobile.png',fullPage:true});
 assert.deepEqual(errors,[]);
 assert.deepEqual([...origins],['http://localhost:3301']);
 writeFileSync('.local/massive-browser/result.json',JSON.stringify({login:true,freshness,bars:snapshot.bars.length,provider:snapshot.provider,mode:snapshot.data_mode,indicators:true,pageErrors:0,browserOrigins:[...origins],liveModelRun:'NOT_SUBMITTED_OUTSIDE_FREEZE_WINDOW',observedAt:new Date().toISOString()},null,2));
 console.log('Local LIVE login, 500 real bars, indicators and responsive browser passed');
}finally{await browser.close();await pool.end();}
