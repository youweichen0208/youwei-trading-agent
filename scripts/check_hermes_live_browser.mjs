// Start the browser only after the separately saved pi harness is ready.
import {execFileSync,spawn} from 'node:child_process';
import {openSync,closeSync} from 'node:fs';
import {once} from 'node:events';
import assert from 'node:assert/strict';
let ready=false;
for(let i=0;i<120;i++){
 try{if((await fetch('http://127.0.0.1:3310/api/yta/v1/ready',{signal:AbortSignal.timeout(1000)})).ok){ready=true;break;}}catch{}
 await new Promise(r=>setTimeout(r,500));
}
assert.ok(ready,'Private BFF must be ready before browser submission');
const env={...process.env,YTA_BFF_ORIGIN:'http://127.0.0.1:3310',YTA_PI_BROWSER_PROBE:'1'};
const log=openSync('.local/hermes-live-browser-build.log','w',0o600);
try{execFileSync('pnpm',['--filter','@yta/web','build'],{env,stdio:['ignore',log,log],timeout:180000});}finally{closeSync(log);}
const web=spawn(process.execPath,['apps/web/node_modules/next/dist/bin/next','start','apps/web','--hostname','127.0.0.1','--port','3300'],{env,stdio:'ignore'});
try{
 let webReady=false;
 for(let i=0;i<120;i++){
  try{if((await fetch('http://localhost:3300/login')).ok){webReady=true;break;}}catch{}
  await new Promise(r=>setTimeout(r,250));
 }
 assert.ok(webReady,'Web ready');
 const probe=spawn(process.execPath,['scripts/probe_mvp_container_browser.mjs'],{env,stdio:'inherit'});
 const [code]=await once(probe,'exit');assert.equal(code,0,'Browser probe failed; reconcile original run before resuming');
}finally{if(web.exitCode===null){web.kill('SIGTERM');await once(web,'exit');}}
