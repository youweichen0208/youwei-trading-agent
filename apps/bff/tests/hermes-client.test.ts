import {createServer,type Server,type IncomingMessage,type ServerResponse} from "node:http";
import {beforeAll,afterAll,it,expect} from "vitest";
import {HermesClient,type HermesEvent} from "../src/hermes-client.js";
const id="run_"+"a".repeat(32),other="run_"+"b".repeat(32),token="synthetic-hermes-private-token";
let server:Server,origin:string,calls=0;
let route:(req:IncomingMessage,res:ServerResponse)=>void;
const client=(timeoutMs=1000,maxBytes=10000)=>new HermesClient({origin,token,profile:"alpha-orchestrator",timeoutMs,maxBytes});
beforeAll(async()=>{
  server=createServer((req,res)=>{calls++;expect(req.headers.authorization).toBe("Bearer "+token);expect(req.url).toMatch(/^\/p\/alpha-orchestrator\/v1\/runs/);route(req,res);});
  await new Promise<void>(r=>server.listen(0,"127.0.0.1",r));const address=server.address();if(!address||typeof address==="string")throw Error();origin="http://127.0.0.1:"+address.port;
});
afterAll(async()=>{server.closeAllConnections();await new Promise<void>(r=>server.close(()=>r()));});
it("pins profile and preserves original request/session keys without resending history",async()=>{
  let body="";route=(req,res)=>{expect(req.headers["idempotency-key"]).toBe("request-1");expect(req.headers["x-hermes-session-key"]).toBe("session-1");req.on("data",c=>body+=c);req.on("end",()=>{res.writeHead(202);res.end(JSON.stringify({run_id:id,replayed:false}));});};
  expect(await client().submit({requestKey:"request-1",sessionKey:"session-1",prompt:"研究 NVDA",model:"pi-model"})).toEqual({run_id:id});
  expect(JSON.parse(body)).toEqual({input:"研究 NVDA",model:"pi-model"});
});
it("treats lost, rejected, redirected and invalid admissions as unknown without automatic retry",async()=>{
  for(const behavior of [
    (_:IncomingMessage,res:ServerResponse)=>res.destroy(),
    (_:IncomingMessage,res:ServerResponse)=>{res.writeHead(500);res.end(token);},
    (_:IncomingMessage,res:ServerResponse)=>{res.writeHead(302,{Location:origin+"/leak"});res.end();},
    (_:IncomingMessage,res:ServerResponse)=>{res.writeHead(202);res.end(JSON.stringify({run_id:"invalid",secret:token}));},
  ]){
    route=behavior;const before=calls;
    await expect(client().submit({requestKey:"original",sessionKey:"session",prompt:"test",model:"pi"})).rejects.toMatchObject({code:"EXECUTION_UNKNOWN",message:expect.not.stringContaining(token)});
    expect(calls).toBe(before+1);
  }
});
it("validates status identity and does not turn stop acknowledgement into cancellation",async()=>{
  route=(_,res)=>res.end(JSON.stringify({run_id:other,status:"completed"}));await expect(client().status(id)).rejects.toMatchObject({code:"HERMES_PROTOCOL_INVALID"});
  route=(_,res)=>res.end(JSON.stringify({run_id:id,status:"execution_unknown"}));expect((await client().status(id)).status).toBe("execution_unknown");
  route=(req,res)=>{expect(req.url).toBe(`/p/alpha-orchestrator/v1/runs/${id}/stop`);res.end(JSON.stringify({status:"stopping"}));};
  expect(await client().stop(id)).toEqual({status:"stopping"});
});
it("bounds decoded response bytes and headers/body deadline",async()=>{
  route=(_,res)=>{res.writeHead(200);res.write('"'+'中'.repeat(1000));};
  await expect(client(1000,128).status(id)).rejects.toMatchObject({code:"HERMES_PROTOCOL_INVALID"});
  route=(_,res)=>{res.writeHead(202);res.flushHeaders();};const before=calls;
  await expect(client(30).submit({requestKey:"deadline",sessionKey:"session",prompt:"x",model:"pi"})).rejects.toMatchObject({code:"EXECUTION_UNKNOWN"});expect(calls).toBe(before+1);
});
it("parses split UTF8 and CRLF frames with correlated cursor and awaited consumers",async()=>{
  route=(req,res)=>{
    expect(req.headers["last-event-id"]).toBe("4");res.writeHead(200,{"Content-Type":"text/event-stream"});
    const data=Buffer.from(': keepalive\r\n\r\nid: 5\r\ndata: '+JSON.stringify({run_id:id,event:"text.delta",seq:5,text:"中"})+'\r\n\r\nid: 6\r\ndata: '+JSON.stringify({run_id:id,event:"run.completed",seq:6})+'\r\n\r\n');
    for(const byte of data)res.write(Buffer.of(byte));res.end();
  };
  const events:HermesEvent[]=[];
  expect(await client().events(id,4,async e=>{await new Promise(r=>setTimeout(r,2));events.push(e);})).toBe(6);
  expect(events.map(e=>e.seq)).toEqual([5,6]);expect(events[0]?.text).toBe("中");
});
it("rejects truncated history, gaps, duplicate sequences, identity mismatch and partial frames",async()=>{
  const frame=(seq:number,r=id)=>`id: ${seq}\ndata: ${JSON.stringify({run_id:r,event:"tool.started",seq})}\n\n`;
  for(const [body,code] of [
    ['data: '+JSON.stringify({run_id:id,event:"replay.truncated",oldest_retained_seq:10})+'\n\n',"HERMES_EVENT_GAP"],
    [frame(1),"HERMES_PROTOCOL_INVALID"],[frame(0)+frame(0),"HERMES_PROTOCOL_INVALID"],
    [frame(0,other),"HERMES_PROTOCOL_INVALID"],[frame(0).slice(0,-1),"HERMES_PROTOCOL_INVALID"],
  ]){route=(_,res)=>{res.writeHead(200,{"Content-Type":"text/event-stream"});res.end(body);};await expect(client().events(id,-1,async()=>{})).rejects.toMatchObject({code});}
});
it("rejects local path/key injection before network and aborts a running stream",async()=>{
  const before=calls;await expect(client().status('../secret')).rejects.toThrow();
  await expect(client().submit({requestKey:"bad\nkey",sessionKey:"ok",prompt:"x",model:"pi"})).rejects.toThrow();expect(calls).toBe(before);
  expect(()=>new HermesClient({origin:origin+'/p/other',token,profile:"alpha-orchestrator"})).toThrow();
  route=(_,res)=>{res.writeHead(200,{"Content-Type":"text/event-stream"});res.write(': open\n\n');};
  const abort=new AbortController(),promise=client().events(id,-1,async()=>{},abort.signal);setTimeout(()=>abort.abort(),20);
  await expect(promise).rejects.toMatchObject({code:"HERMES_UNAVAILABLE"});
});
