import {HermesResearchDriver} from "../src/hermes-driver.js";
import {ResearchRunner,ConfirmedRunCancellation} from "../src/research-runner.js";
import {readConfig} from "../src/config.js";
import {createServer} from "node:http";
import {HermesClient} from "../src/hermes-client.js";
import {admitHermesRun} from "../src/hermes-admission.js";
import {randomUUID} from "node:crypto";
import {beforeAll,afterAll,describe,it,expect} from "vitest";
import {database} from "../src/database.js";
import {HermesStore} from "../src/hermes-store.js";
import {ResearchRepository,type Run} from "../src/research-store.js";
const url=process.env.YTA_TEST_DATABASE_URL;
describe.skipIf(!url)("Hermes persistent admission identities",()=>{
  let pool:ReturnType<typeof database>,repo:ResearchRepository,store:HermesStore;
  const runs:Run[]=[],runtime="a".repeat(64);
  beforeAll(()=>{if(!url||!new URL(url).pathname.endsWith('_test'))throw Error('Dedicated test database required');pool=database(url);repo=new ResearchRepository(pool,"REPLAY");store=new HermesStore(pool,"REPLAY");});
  afterAll(async()=>{try{for(const run of runs)await repo.append(run,{type:"run.canceled",payload:{}});}finally{await pool.end();}});
  async function run(){const owner=randomUUID();await pool.query('INSERT INTO yta.users(id,email) VALUES($1,$2)',[owner,owner+'@hermes.test']);const s=await repo.createSession(owner,'REPLAY',randomUUID());const r=await repo.submit(owner,s.session_id,randomUUID(),'private original user prompt','REPLAY');const value=await repo.getRun(owner,r.run_id);runs.push(value);return value;}
  it("commits exactly one send claim across concurrent clients and retains receipt across restart",async()=>{
    const r=await run(),body={input:r.prompt,model:'pi-selected'};
    const other=database(url!);try{
      const stores=[store,new HermesStore(other,'REPLAY')];
      const claims=await Promise.all(Array.from({length:8},(_,i)=>stores[i%2]!.claim(r,runtime,body)));
      expect(claims.filter(c=>c.send)).toHaveLength(1);expect(new Set(claims.map(c=>c.request.requestKey)).size).toBe(1);
      const upstream='run_'+randomUUID().replaceAll('-','');
      await Promise.all(stores.map(s=>s.receipt(r,runtime,upstream)));
      const recovered=await new HermesStore(other,'REPLAY').claim(r,runtime,body);
      expect(recovered.send).toBe(false);expect(recovered.request.upstreamRunId).toBe(upstream);
      const audits=await pool.query("SELECT action,count(*)::int AS n FROM yta.audit_events WHERE resource_id=$1 GROUP BY action",[r.id]);
      expect(audits.rows).toEqual(expect.arrayContaining([{action:'hermes.request_claimed',n:1},{action:'hermes.receipt_recorded',n:1}]));
    }finally{await other.end();}
  });
  it("never issues another send claim after crash before receipt and rejects key scope or content changes",async()=>{
    const r=await run(),body={input:r.prompt,model:'pi-selected'};await store.claim(r,runtime,body);
    const recovered=await new HermesStore(pool,'REPLAY').claim(r,runtime,body);
    expect(recovered.send).toBe(false);expect(recovered.request.upstreamRunId).toBe(null);
    await expect(store.claim(r,'b'.repeat(64),body)).rejects.toMatchObject({code:'HERMES_SCOPE_CHANGED'});
    await expect(store.claim(r,runtime,{...body,model:'other'})).rejects.toMatchObject({code:'IDEMPOTENCY_CONFLICT'});
    await expect(store.claim(r,runtime,{...body,input:'changed'})).rejects.toMatchObject({code:'IDEMPOTENCY_CONFLICT'});
  });
  it("checks authenticated ownership, mode and source run before any binding write",async()=>{
    const r=await run(),body={input:r.prompt,model:'pi-selected'};
    for(const changed of [{...r,owner_id:randomUUID()},{...r,session_id:randomUUID()},{...r,prompt:'forged'}])await expect(store.claim(changed,runtime,body)).rejects.toMatchObject({code:'NOT_FOUND'});
    await expect(new HermesStore(pool,'LIVE_RESEARCH').get(r)).rejects.toMatchObject({code:'NOT_FOUND'});
    expect((await pool.query('SELECT * FROM yta.hermes_session_bindings WHERE session_id=$1',[r.session_id])).rows).toHaveLength(0);
  });
  it("rejects conflicting receipt and one upstream run assigned to two application runs",async()=>{
    const first=await run(),second=await run(),upstream='run_'+randomUUID().replaceAll('-','');
    for(const r of [first,second])await store.claim(r,runtime,{input:r.prompt,model:'pi-selected'});
    await store.receipt(first,runtime,upstream);
    await expect(store.receipt(second,runtime,upstream)).rejects.toMatchObject({code:'HERMES_BINDING_CONFLICT'});
    await expect(store.receipt(first,runtime,'run_'+'0'.repeat(32))).rejects.toMatchObject({code:'HERMES_BINDING_CONFLICT'});
    expect((await store.get(second))?.upstreamRunId).toBe(null);
  });
  it("sends once through actual HTTP and never resends after an accepted response is lost",async()=>{
    let calls=0,drop=false;const upstream='run_'+randomUUID().replaceAll('-','');
    const server=createServer((req,res)=>{calls++;req.resume();req.on('end',()=>{if(drop){res.destroy();return;}res.writeHead(202);res.end(JSON.stringify({run_id:upstream}));});});
    await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));const address=server.address();if(!address||typeof address==='string')throw Error();
    const client=new HermesClient({origin:'http://127.0.0.1:'+address.port,token:'synthetic-admission-token',profile:'alpha-orchestrator'});
    try{
      const first=await run();expect(await admitHermesRun(store,client,first,'pi-selected',first.prompt)).toBe(upstream);
      expect(await admitHermesRun(new HermesStore(pool,'REPLAY'),client,first,'pi-selected',first.prompt)).toBe(upstream);expect(calls).toBe(1);
      drop=true;const lost=await run();await expect(admitHermesRun(store,client,lost,'pi-selected',lost.prompt)).rejects.toMatchObject({code:'EXECUTION_UNKNOWN'});
      await expect(admitHermesRun(new HermesStore(pool,'REPLAY'),client,lost,'pi-selected',lost.prompt)).rejects.toMatchObject({code:'EXECUTION_UNKNOWN'});expect(calls).toBe(2);
      expect((await store.get(lost))?.upstreamRunId).toBe(null);
    }finally{server.closeAllConnections();await new Promise<void>(r=>server.close(()=>r()));}
  });
  it("streams correlated public events and routes final output through trusted completion",async()=>{
    const upstream='run_'+randomUUID().replaceAll('-','');let submissions=0,finished=0,prepared=0;
    const server=createServer((req,res)=>{
      req.resume();req.on('end',()=>{
        if(req.method==='POST'){submissions++;res.writeHead(202);res.end(JSON.stringify({run_id:upstream}));}
        else if(req.url?.endsWith('/events')){
          res.writeHead(200,{'Content-Type':'text/event-stream'});
          const events=[{event:'tool.started',tool:'mcp__yta_finance__market_snapshot',tool_call_id:'call-1',preview:'private'},
            {event:'tool.completed',tool:'mcp__yta_finance__market_snapshot',tool_call_id:'call-1',error:false,preview:'private'},
            {event:'message.delta',delta:'真实模型输出协议样例'}, {event:'run.completed'}];
          events.forEach((e,seq)=>res.write(`id: ${seq}\ndata: ${JSON.stringify({...e,seq,run_id:upstream})}\n\n`));res.end();
        }else res.end(JSON.stringify({run_id:upstream,status:'completed',output:'private model output'}));
      });
    });
    await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));const a=server.address();if(!a||typeof a==='string')throw Error();
    try{
      const r=await run(),events:unknown[]=[];
      const client=new HermesClient({origin:'http://127.0.0.1:'+a.port,token:'synthetic-driver-token',profile:'alpha-orchestrator'});
      const driver=new HermesResearchDriver(store,client,'pi-selected',async(actual,status)=>{expect(actual.id).toBe(r.id);expect(status.status).toBe('completed');finished++;return [];},async(actual)=>{prepared++;return 'fixed-server-context:'+actual.prompt;});
      await driver.execute(r,async e=>{events.push(e);},new AbortController().signal);
      const savedEvents=(await repo.events(r.owner_id,r.id,0)).filter(e=>!e.type.startsWith('run.')).map(e=>({type:e.type,payload:e.payload}));
      expect(savedEvents).toEqual([{type:'tool.started',payload:{tool:'market_snapshot',tool_call_id:'call-1'}},
        {type:'tool.completed',payload:{tool:'market_snapshot',tool_call_id:'call-1',success:true}},
        {type:'message.delta',payload:{text:'真实模型输出协议样例'}}]);
      expect((await store.get(r))?.prompt).toBe('fixed-server-context:'+r.prompt);
      expect(finished).toBe(1);await driver.execute(r,async()=>{},new AbortController().signal);expect(submissions).toBe(1);expect(finished).toBe(1);expect(prepared).toBe(1);
    }finally{server.closeAllConnections();await new Promise<void>(r=>server.close(()=>r()));}
  });
  it("requires observed cancelled status after stop acknowledgement",async()=>{
    for(const terminal of ['cancelled','completed','execution_unknown']){
      const upstream='run_'+randomUUID().replaceAll('-','');let stops=0;
      const abort=new AbortController();
      const server=createServer((req,res)=>{req.resume();req.on('end',()=>{
        if(req.url?.endsWith('/stop')){stops++;res.end(JSON.stringify({status:'stopping'}));}
        else if(req.method==='POST'){res.writeHead(202);res.end(JSON.stringify({run_id:upstream}));}
        else if(req.url?.endsWith('/events')){res.writeHead(200,{'Content-Type':'text/event-stream'});res.write(': open\n\n');setTimeout(()=>abort.abort(new Error('cancel requested')),10);}
        else res.end(JSON.stringify({run_id:upstream,status:terminal}));
      });});
      await new Promise<void>(r=>server.listen(0,'127.0.0.1',r));const a=server.address();if(!a||typeof a==='string')throw Error();
      try{
        const r=await run(),client=new HermesClient({origin:'http://127.0.0.1:'+a.port,token:'synthetic-stop-token',profile:'alpha-orchestrator'});
        const driver=new HermesResearchDriver(store,client,'pi-selected',async()=>{throw Error('must not finish');});
        await expect(driver.execute(r,async()=>{},abort.signal)).rejects.toMatchObject({code:terminal==='cancelled'?'RUN_CANCELED':'EXECUTION_UNKNOWN'});expect(stops).toBe(1);
      }finally{server.closeAllConnections();await new Promise<void>(r=>server.close(()=>r()));}
    }
  });
  it("runner preserves unconfirmed remote cancellation as unknown",async()=>{
    for(const confirmed of [false,true]){
      const r=await run();let entered!:()=>void;const started=new Promise<void>(resolve=>entered=resolve);
      const driver={cancellationPolicy:'confirmed-upstream' as const,execute:async(_r:Run,_emit:unknown,signal:AbortSignal)=>{
        entered();await new Promise<void>((_resolve,reject)=>signal.addEventListener('abort',()=>reject(confirmed?new ConfirmedRunCancellation():signal.reason),{once:true}));
      }};
      const runner=new ResearchRunner(repo,driver,readConfig({YTA_ENV:'test',YTA_DATABASE_URL:url!,YTA_ALPHA_TOKEN:'synthetic-runner-service-token-123456'}));
      try{
        runner.launch(r);await started;
        await pool.query('INSERT INTO yta.run_cancellations(run_id,owner_id) VALUES($1,$2)',[r.id,r.owner_id]);
        let status='';for(let i=0;i<50;i++){status=(await repo.runSummary(r.owner_id,r.id)).status;if(['CANCELED','EXECUTION_UNKNOWN'].includes(status))break;await new Promise(resolve=>setTimeout(resolve,20));}
        expect(status).toBe(confirmed?'CANCELED':'EXECUTION_UNKNOWN');
      }finally{await runner.close();}
    }
  });
  it("does not turn a remote completion/cancel transaction race into confirmed cancellation",async()=>{
    const r=await run();await pool.query('INSERT INTO yta.run_cancellations(run_id,owner_id) VALUES($1,$2)',[r.id,r.owner_id]);
    expect((await repo.append(r,{type:'run.completed',payload:{}},true))?.type).toBe('run.execution_unknown');
  });
  it("commits upstream cursors and public events once and rejects changed replay or gaps",async()=>{
    const r=await run(),upstream='run_'+randomUUID().replaceAll('-','');
    await store.claim(r,runtime,{input:r.prompt,model:'pi-selected'});await store.receipt(r,runtime,upstream);
    const source={run_id:upstream,seq:0,event:'message.delta',delta:'one',preview:'private ignored'};
    const result=await Promise.all(Array.from({length:5},()=>store.ingest(r,runtime,source)));
    expect(result.filter(Boolean)).toHaveLength(1);
    expect((await repo.events(r.owner_id,r.id,0)).map(e=>e.type)).toEqual(['message.delta']);
    expect(await store.ingest(r,runtime,{...source,preview:'changed ignored preview'})).toBe(false);
    await expect(store.ingest(r,runtime,{...source,delta:'changed'})).rejects.toMatchObject({code:'HERMES_EVENT_INVALID'});
    await expect(store.ingest(r,runtime,{...source,seq:2})).rejects.toMatchObject({code:'HERMES_EVENT_GAP'});
    expect((await store.checkpoint(r,runtime)).cursor).toBe(0);
    await store.ingest(r,runtime,{run_id:upstream,seq:1,event:'reasoning.available',text:'private reasoning'});
    const raw=await pool.query('SELECT observation FROM yta.hermes_run_events WHERE run_id=$1 ORDER BY sequence',[r.id]);
    expect(JSON.stringify(raw.rows)).not.toContain('private');
    expect((await repo.events(r.owner_id,r.id,0))).toHaveLength(1);
  });
  it("atomically finalizes validated events and rolls back the whole completion on failure",async()=>{
    const r=await run(),upstream='run_'+randomUUID().replaceAll('-','');await store.claim(r,runtime,{input:r.prompt,model:'pi-selected'});await store.receipt(r,runtime,upstream);
    await store.ingest(r,runtime,{run_id:upstream,seq:0,event:'run.completed'});
    await expect(store.finish(r,runtime,[{type:'message.completed',payload:{text:'summary'}},{type:'run.started',payload:{}}])).rejects.toMatchObject({code:'RESEARCH_REPORT_INVALID'});
    expect(await repo.events(r.owner_id,r.id,0)).toHaveLength(0);
    const bodies=[{type:'message.completed' as const,payload:{text:'summary'}}];
    await Promise.all([store.finish(r,runtime,bodies),store.finish(r,runtime,bodies)]);
    expect((await repo.events(r.owner_id,r.id,0)).map(e=>e.type)).toEqual(['message.completed','run.completed']);
    expect((await repo.messages(r.owner_id,r.session_id)).items.filter(m=>m.role==='assistant').map(m=>m.content)).toEqual(['summary']);
  });
  it("persists completed prose instead of raw model JSON chunks in refreshed history",async()=>{
    const r=await run(),upstream='run_'+randomUUID().replaceAll('-','');
    await store.claim(r,runtime,{input:r.prompt,model:'pi-selected'});await store.receipt(r,runtime,upstream);
    await store.ingest(r,runtime,{run_id:upstream,seq:0,event:'message.delta',delta:'{"schema_version":"research-report-v1"}'});
    await store.ingest(r,runtime,{run_id:upstream,seq:1,event:'run.completed'});
    // Simulate the old immutable history row; read projection must repair display only.
    await pool.query("INSERT INTO yta.research_messages(session_id,run_id,role,content,partial) VALUES($1,$2,'assistant',$3,false)",[r.session_id,r.id,'{"schema_version":"research-report-v1"}']);
    await store.finish(r,runtime,[{type:'message.completed',payload:{text:'经过核验的研究摘要。'}}]);
    const messages=await repo.messages(r.owner_id,r.session_id);
    expect(messages.items.filter(m=>m.role==='assistant').map(m=>({content:m.content,partial:m.partial})))
      .toEqual([{content:'经过核验的研究摘要。',partial:false}]);
  });
  it("resumes a worker from the saved cursor without submitting or duplicating public text",async()=>{
    const r=await run(),upstream='run_'+randomUUID().replaceAll('-','');let submissions=0,streams=0;
    const server=createServer((req,res)=>{req.resume();req.on('end',()=>{
      if(req.method==='POST'){submissions++;res.writeHead(500);res.end();return;}
      if(req.url?.endsWith('/events')){
        streams++;expect(req.headers['last-event-id']).toBe('1');res.writeHead(200,{'Content-Type':'text/event-stream'});
        const events=[{event:'tool.completed',tool:'mcp__yta_finance__market_snapshot',tool_call_id:'saved-call',error:false},
          {event:'message.delta',delta:'second'}, {event:'run.completed'}];
        events.forEach((e,index)=>{const seq=index+2;res.write(`id: ${seq}\ndata: ${JSON.stringify({...e,seq,run_id:upstream})}\n\n`);});res.end();
      }else res.end(JSON.stringify({run_id:upstream,status:'completed'}));
    });});
    await new Promise<void>(resolve=>server.listen(0,'127.0.0.1',resolve));const address=server.address();if(!address||typeof address==='string')throw Error();
    const client=new HermesClient({origin:'http://127.0.0.1:'+address.port,token:'synthetic-resume-token',profile:'alpha-orchestrator'});
    const hash=client.runtimeIdentity;
    await store.claim(r,hash,{input:r.prompt,model:'pi-selected'});await store.receipt(r,hash,upstream);
    await repo.append(r,{type:'run.started',payload:{}});
    await store.ingest(r,hash,{run_id:upstream,seq:0,event:'message.delta',delta:'first'});
    await store.ingest(r,hash,{run_id:upstream,seq:1,event:'tool.started',tool:'mcp__yta_finance__market_snapshot',tool_call_id:'saved-call'});
    const driver=new HermesResearchDriver(new HermesStore(pool,'REPLAY'),client,'pi-selected',async()=>[{type:'message.completed',payload:{text:'firstsecond'}}]);
    const runner=new ResearchRunner(repo,driver,readConfig({YTA_ENV:'test',YTA_DATABASE_URL:url!,YTA_ALPHA_TOKEN:'synthetic-resume-alpha-token-123456'}));
    try{
      runner.launch(r);let status='';for(let i=0;i<100;i++){status=(await repo.runSummary(r.owner_id,r.id)).status;if(status==='COMPLETED'||status==='FAILED'||status==='EXECUTION_UNKNOWN')break;await new Promise(resolve=>setTimeout(resolve,20));}
      expect(status).toBe('COMPLETED');expect(submissions).toBe(0);expect(streams).toBe(1);
      const events=await repo.events(r.owner_id,r.id,0);expect(events.filter(e=>e.type==='run.started')).toHaveLength(1);
      expect(events.filter(e=>e.type==='message.delta').map(e=>e.payload.text)).toEqual(['first','second']);
      expect((await store.checkpoint(r,hash)).cursor).toBe(4);
      expect((await pool.query("SELECT content,partial FROM yta.research_messages WHERE run_id=$1 AND role='assistant'",[r.id])).rows).toEqual([{content:'firstsecond',partial:false}]);
    }finally{await runner.close();server.closeAllConnections();await new Promise<void>(resolve=>server.close(()=>resolve()));}
  });
  it("denies runtime mutation of requests, sessions and receipts",async()=>{
    const r=await run();await store.claim(r,runtime,{input:r.prompt,model:'pi-selected'});await store.receipt(r,runtime,'run_'+randomUUID().replaceAll('-',''));
    for(const table of ['hermes_session_bindings','hermes_run_requests','hermes_run_receipts','hermes_run_events']){
      const pk=table==='hermes_session_bindings'?'session_id':'run_id',id=table==='hermes_session_bindings'?r.session_id:r.id;
      await expect(pool.query(`UPDATE yta.${table} SET ${pk}=${pk} WHERE ${pk}=$1`,[id])).rejects.toMatchObject({code:'42501'});
      await expect(pool.query(`DELETE FROM yta.${table} WHERE ${pk}=$1`,[id])).rejects.toMatchObject({code:'42501'});
      await expect(pool.query(`TRUNCATE yta.${table}`)).rejects.toMatchObject({code:'42501'});
    }
  });
});
