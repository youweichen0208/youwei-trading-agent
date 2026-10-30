import { randomBytes, randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { createServer, type Server } from "node:http";
import { setTimeout as delay } from "node:timers/promises";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { createBff } from "../src/app.js";
import type { Observation } from "../src/observability.js";
import { hashContent } from "../src/experimental-report.js";
import { Auth, digest, hashPassword } from "../src/auth.js";
import * as passwords from "../src/password.js";
import { ApiError } from "../src/errors.js";
import { readConfig } from "../src/config.js";
import { database } from "../src/database.js";
import { ResearchRepository } from "../src/research-store.js";
import type { ResearchDriver } from "../src/research-runner.js";

const url = process.env.YTA_TEST_DATABASE_URL;
const origin = "http://localhost:3000";
const token = "integration-service-token-not-live-123456789";
const password = "synthetic-password-not-live";
const forecast = JSON.parse(readFileSync(new URL("../../../packages/contracts/fixtures/forecast.json", import.meta.url), "utf8"));
const evidenceId = randomUUID();
const evidence = {data_snapshot_hash: forecast.data_snapshot_hash, snapshot: {
  symbol: "NVDA", provider: "synthetic-fixture", source_id: "synthetic-evidence", data_mode: "REPLAY",
  adjustment: "RAW", as_of: forecast.as_of, actions_verified_through: forecast.base_session,
  bars: [{session: forecast.base_session, open: "100", high: "102", low: "99", close: "101.1234567890123456789",
    volume: 100, available_at: forecast.as_of, retrieved_at: forecast.as_of, revision: "fixture-v1"}], splits: [],
}};
let evidenceResponse: unknown = evidence;
const freshness = {status: "FRESH", data_mode: "REPLAY", cache_key: "fixture:NVDA", ttl_seconds: 30,
  source_id: "fixture", data_snapshot_hash: forecast.data_snapshot_hash, as_of: forecast.as_of, reason: null};
let freshnessResponse: unknown = freshness;
const modelPage = JSON.parse(readFileSync(new URL("../../../packages/contracts/fixtures/experimental-model-page.json", import.meta.url), "utf8"));
let modelPageResponse: unknown = modelPage;
const priceReport = JSON.parse(readFileSync(new URL("../../../packages/contracts/fixtures/experimental-price-report.json", import.meta.url), "utf8"));
let priceReportResponse: unknown = priceReport;
const reportPage = JSON.parse(readFileSync(new URL("../../../packages/contracts/fixtures/experimental-evaluation-page.json", import.meta.url), "utf8"));
let reportPageResponse: unknown = reportPage;
const comparison = JSON.parse(readFileSync(new URL("../../../packages/contracts/fixtures/experimental-report-comparison.json", import.meta.url), "utf8"));
let comparisonResponse: unknown = comparison;
const calibrationFixture=JSON.parse(readFileSync(new URL("../../../packages/contracts/fixtures/calibration-report.json",import.meta.url),"utf8"));
let calibrationResponse:unknown=calibrationFixture;
const backtestFixture=JSON.parse(readFileSync(new URL("../../../packages/contracts/fixtures/backtest-report.json",import.meta.url),"utf8"));
let backtestResponse:unknown=backtestFixture;
const trainingFixture=JSON.parse(readFileSync(new URL("../../../packages/contracts/fixtures/training-evidence-matched.json",import.meta.url),"utf8"));
let trainingResponse:unknown=trainingFixture;
const trainingPageFixture=JSON.parse(readFileSync(new URL("../../../packages/contracts/fixtures/training-evidence-page.json",import.meta.url),"utf8"));
let trainingPageResponse:unknown=trainingPageFixture;
const backtestPageFixture=JSON.parse(readFileSync(new URL("../../../packages/contracts/fixtures/backtest-page.json",import.meta.url),"utf8"));
let backtestPageResponse:unknown=backtestPageFixture;
const proposalFixture = JSON.parse(readFileSync(new URL("../../../packages/contracts/fixtures/model-proposal.json", import.meta.url), "utf8"));
const qualityFixture=JSON.parse(readFileSync(new URL("../../../packages/contracts/fixtures/quality-assessment.json",import.meta.url),"utf8"));
const qualityPolicy={id:"historical-quality-policy-"+qualityFixture.policy_hash,content_hash:qualityFixture.policy_hash,policy:qualityFixture.policy};
const reviewFixture=JSON.parse(readFileSync(new URL("../../../packages/contracts/fixtures/model-review.json",import.meta.url),"utf8"));
const reviewV2Fixture=JSON.parse(readFileSync(new URL("../../../packages/contracts/fixtures/model-review-v2.json",import.meta.url),"utf8"));
let reviewResponse: unknown=reviewFixture;
const probabilityFixture=JSON.parse(readFileSync(new URL("../../../packages/contracts/fixtures/probability-quality-assessment.json",import.meta.url),"utf8"));
const probabilityPolicy={id:"probability-quality-policy-"+probabilityFixture.policy_hash,content_hash:probabilityFixture.policy_hash,policy:probabilityFixture.policy};
const gateFixture=JSON.parse(readFileSync(new URL("../../../packages/contracts/fixtures/model-quality-gate.json",import.meta.url),"utf8"));
let gateResponse:unknown=gateFixture;
let probabilityResponse:unknown=probabilityFixture;
let probabilityPageResponse:unknown={items:[probabilityPolicy],next_cursor:probabilityPolicy.id};
let qualityResponse: unknown=qualityFixture;
let qualityPageResponse: unknown={items:[qualityPolicy],next_cursor:qualityPolicy.id};
let proposalResponse: unknown = proposalFixture;
let proposalPageResponse: unknown = {items:[],next_cursor:null};

type Login = { cookie: string; csrf: string; id: string; email: string };

describe.skipIf(!url)("BFF / PostgreSQL authorization and streaming", () => {
  let server: Awaited<ReturnType<typeof createBff>>;
  let alpha: Server;
  let endpoint: string;
  let alphaOrigin: string;
  let user: Login;
  let other: Login;
  let alphaReady = true;
  let invalidReportHash = false;
  const observations: Observation[] = [];
  const ownerA = randomUUID(), ownerB = randomUUID();
  const emailA = `${ownerA}@yta.test`, emailB = `${ownerB}@yta.test`;
  const calls: { path: string; owner: string | undefined; body: unknown; key: string | undefined }[] = [];
  const drafts = new Map<string, string>();

  async function request(path: string, options: { login?: Login; method?: string; body?: unknown; key?: string; csrf?: string; origin?: string } = {}) {
    const headers: Record<string, string> = { "Content-Type": "application/json", Origin: options.origin ?? origin };
    if (options.login) { headers.Cookie = options.login.cookie; headers["X-CSRF-Token"] = options.csrf ?? options.login.csrf; }
    if (options.key) headers["Idempotency-Key"] = options.key;
    return fetch(`${endpoint}/api/yta/v1${path}`, { method: options.method ?? (options.body === undefined ? "GET" : "POST"), headers, body: options.body === undefined ? undefined : JSON.stringify(options.body) });
  }
  async function login(email: string): Promise<Login> {
    const response = await request("/auth/login", { body: { email, password } });
    expect(response.status).toBe(200);
    const body = await response.json();
    const cookie = response.headers.get("set-cookie")!;
    expect(cookie).toContain("HttpOnly"); expect(cookie).toContain("SameSite=Strict");
    return { cookie: cookie.split(";")[0]!, csrf: body.csrf_token, id: body.user.id, email };
  }
  async function session() {
    const response = await request("/sessions", { login: user, body: { profile_id: "alpha-orchestrator" }, key: randomUUID() });
    expect(response.status).toBe(201);
    return (await response.json()).session_id as string;
  }

  it("scopes report discovery to the requested model and validates its pages", async () => {
    const path = `/experimental-models/${priceReport.model_id}/evaluations`;
    const before = calls.length;
    expect((await request(path)).status).toBe(401);
    for (const query of ["limit=51", "owner_id=forged", "cursor=invalid", "limit=1&limit=2"]) {
      expect((await request(path + "?" + query, {login: user})).status).toBe(422);
    }
    expect(calls).toHaveLength(before);
    const response = await request(path + "?limit=2", {login: user});
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(reportPage);
    const row = reportPage.items[0];
    try {
      for (const changed of [{model_id: "lightgbm-experimental-" + "0".repeat(64)},
        {data_mode: "LIVE_RESEARCH"}, {secret: token}, {content_hash: "0".repeat(64)}]) {
        reportPageResponse = {items: [{...row, ...changed}], next_cursor: null};
        const invalid = await request(path, {login: user});
        expect(invalid.status).toBe(502);
        expect(await invalid.text()).not.toContain(token);
      }
      reportPageResponse = reportPage;
      expect((await request(path + "?cursor=" + row.id, {login: user})).status).toBe(502);
    } finally {reportPageResponse = reportPage;}
  });

  it("authenticates and validates fixed experimental reports before exposing evidence", async () => {
    const id = "model-evaluation-experimental-" + priceReport.content_hash;
    const path = "/experimental-evaluations/" + id;
    const before = calls.length;
    expect((await request(path)).status).toBe(401);
    expect((await request(path + "?data_mode=LIVE_RESEARCH", {login: user})).status).toBe(422);
    expect((await request("/experimental-evaluations/not-an-id", {login: user})).status).toBe(422);
    expect(calls).toHaveLength(before);
    const response = await request(path, {login: user});
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(priceReport);
    expect(calls.at(-1)?.owner).toBe(user.id);
    try {
      priceReportResponse = {...priceReport, secret: token};
      const invalid = await request(path, {login: user});
      expect(invalid.status).toBe(502);
      expect(await invalid.text()).not.toContain(token);
      priceReportResponse = {...priceReport, model_sha256: "0".repeat(64)};
      expect((await request(path, {login: user})).status).toBe(502);
    } finally {priceReportResponse = priceReport;}
    const foreignId = "/experimental-evaluations/model-evaluation-experimental-" + "0".repeat(64);
    expect((await request(foreignId, {login: user})).status).toBe(502);
  });

  it("authenticates model catalog reads and rejects client mode or pagination overrides", async () => {
    const path = "/experimental-models";
    expect((await (await request("/me", {login: user})).json()).experimental_models_enabled).toBe(true);
    const before = calls.length;
    expect((await request(path)).status).toBe(401);
    for (const query of ["data_mode=LIVE_RESEARCH", "owner_id=" + other.id, "limit=51", "limit=1&limit=2",
      "horizon_sessions=5.0", "cursor=../model", "limit=01"]) {
      expect((await request(path + "?" + query, {login: user})).status).toBe(422);
    }
    expect(calls).toHaveLength(before);
    const result = await request(path + "?limit=2&horizon_sessions=5", {login: user});
    expect(result.status).toBe(200);
    expect(await result.json()).toEqual(modelPage);
    expect(calls.at(-1)).toMatchObject({owner: user.id, path: "/internal/v1/experimental-models?limit=2&horizon_sessions=5"});
    // System model metadata is shared among authenticated users, unlike owned research facts.
    expect((await request(path, {login: other})).status).toBe(200);
  });

  it("rejects unsafe or inconsistent upstream model metadata without leaking extra fields", async () => {
    const row = modelPage.items[0];
    try {
      for (const invalid of [
        {...modelPage, secret: token}, {items: [{...row, model_text: token}], next_cursor: null},
        {items: [{...row, data_mode: "LIVE_RESEARCH"}], next_cursor: null},
        {items: [{...row, horizon_sessions: 3}], next_cursor: null},
        {items: [{...row, reserved_window_mae: 0.1}], next_cursor: null},
        {items: [{...row, status: "CHAMPION"}], next_cursor: null},
        {items: [{...row, registration_sha256: "0".repeat(64)}], next_cursor: null},
        {items: [row, row], next_cursor: null}, {items: [], next_cursor: row.id},
      ]) {
        modelPageResponse = invalid;
        const result = await request("/experimental-models?horizon_sessions=5", {login: user});
        expect(result.status).toBe(502);
        expect(await result.text()).not.toContain(token);
      }
      modelPageResponse = modelPage;
      expect((await request("/experimental-models?cursor=" + row.id, {login: user})).status).toBe(502);
    } finally { modelPageResponse = modelPage; }
  });

  it("requires authenticated same-origin comparison and rejects altered or reordered upstream reports", async () => {
    const path = "/experimental-evaluations/compare", body = {evaluation_ids: comparison.evaluation_ids};
    const before = calls.length;
    expect((await request(path, {body})).status).toBe(401);
    expect((await request(path, {body, login: user, csrf: "wrong"})).status).toBe(403);
    expect((await request(path, {body, login: user, origin: "https://other.invalid"})).status).toBe(403);
    expect((await request(path, {body: {...body, data_mode: "REPLAY"}, login: user})).status).toBe(422);
    expect((await request(path + "?data_mode=REPLAY", {body, login: user})).status).toBe(422);
    expect(calls).toHaveLength(before);
    const result = await request(path, {body, login: user});
    expect(result.status).toBe(200); expect(await result.json()).toEqual(comparison);
    expect(calls.at(-1)).toMatchObject({path: "/internal/v1/experimental-evaluations/compare", owner: user.id, body});
    try {
      for (const invalid of [{...comparison, evaluation_ids: [...comparison.evaluation_ids].reverse()},
        {...comparison, secret: token}, {...comparison, cohort_hash: "0".repeat(64)}]) {
        comparisonResponse = invalid;
        const failed = await request(path, {body, login: user});
        expect(failed.status).toBe(502); expect(await failed.text()).not.toContain(token);
      }
    } finally {comparisonResponse = comparison;}
  });

  it("binds proposals to session owner, UUID retry identity and exact submitted evidence", async () => {
    const key=randomUUID(), p=proposalFixture.proposal;
    const body={candidate_evaluation_id:p.candidate_evaluation_id,reference_evaluation_id:p.reference_evaluation_id,rationale:p.rationale};
    const path="/model-proposals", before=calls.length;
    expect((await request(path,{body,key})).status).toBe(401);
    expect((await request(path,{body,key,login:user,csrf:"wrong"})).status).toBe(403);
    expect((await request(path,{body,key,login:user,origin:"https://other.invalid"})).status).toBe(403);
    expect((await request(path,{body,key:"not-uuid",login:user})).status).toBe(422);
    expect((await request(path,{body:{...body,owner_id:other.id},key,login:user})).status).toBe(422);
    expect(calls).toHaveLength(before);
    const proposal={...p,owner_id:user.id,request_id:key};
    const saved={...proposalFixture,proposal,content_hash:hashContent(proposal)};
    proposalResponse=saved;
    try {
      const result=await request(path,{body,key:key.toUpperCase(),login:user});
      expect(result.status).toBe(200);expect(await result.json()).toEqual(saved);
      expect(calls.at(-1)).toMatchObject({path:"/internal/v1/model-proposals",owner:user.id,key,body});
      expect(await (await request(path+"/"+saved.id,{login:user})).json()).toEqual(saved);
      expect((await request(path+"/"+saved.id,{login:other})).status).toBe(502);
      proposalResponse={...saved,private:token};
      const invalid=await request(path,{body,key,login:user});
      expect(invalid.status).toBe(502);expect(await invalid.text()).not.toContain(token);
    } finally {proposalResponse=proposalFixture;}
  });

  it("discovers proposal history with session scope and validated pagination", async () => {
    const path="/model-proposals", before=calls.length;
    expect((await request(path)).status).toBe(401);
    for(const query of ["?limit=51","?owner_id="+other.id,"?data_mode=LIVE_RESEARCH","?cursor=bad","?limit=1&limit=2"]) {
      expect((await request(path+query,{login:user})).status).toBe(422);
    }
    expect(calls).toHaveLength(before);
    const proposal={...proposalFixture.proposal,owner_id:user.id};
    const saved={...proposalFixture,proposal,content_hash:hashContent(proposal)};
    const page={items:[saved],next_cursor:saved.id};
    proposalPageResponse=page;
    try {
      const response=await request(path+"?limit=1",{login:user});
      expect(response.status).toBe(200);expect(await response.json()).toEqual(page);
      expect(calls.at(-1)).toMatchObject({path:"/internal/v1/model-proposals?limit=1",owner:user.id});
      expect((await request(path,{login:other})).status).toBe(502);
      expect((await request(path+"?cursor="+saved.id,{login:user})).status).toBe(502);
      proposalPageResponse={items:[],next_cursor:null};
      expect(await (await request(path+"?limit=1&cursor="+saved.id.toUpperCase(),{login:user})).json()).toEqual(proposalPageResponse);
      expect(calls.at(-1)?.path).toBe("/internal/v1/model-proposals?limit=1&cursor="+saved.id);
      proposalPageResponse={...page,private:token};
      const invalid=await request(path,{login:user});
      expect(invalid.status).toBe(502);expect(await invalid.text()).not.toContain(token);
    } finally {proposalPageResponse={items:[],next_cursor:null};}
  });

  it("binds quality assessment requests to authenticated session and exact evidence", async()=>{
    const path="/historical-quality-assessments";
    const body={policy_id:qualityPolicy.id,candidate_evaluation_id:qualityFixture.candidate_evaluation_id,reference_evaluation_id:qualityFixture.reference_evaluation_id};
    const before=calls.length;
    expect((await request(path,{body})).status).toBe(401);
    expect((await request(path,{body,login:user,csrf:"wrong"})).status).toBe(403);
    expect((await request(path,{body,login:user,origin:"https://other.invalid"})).status).toBe(403);
    expect((await request(path,{body:{...body,data_mode:"LIVE_RESEARCH"},login:user})).status).toBe(422);
    expect((await request(path+"?policy=other",{body,login:user})).status).toBe(422);
    expect(calls).toHaveLength(before);
    expect(await (await request(path,{body,login:user})).json()).toEqual(qualityFixture);
    expect(calls.at(-1)).toMatchObject({owner:user.id,path:"/internal/v1/historical-quality-assessments",body});
    const id="historical-quality-assessment-"+qualityFixture.content_hash;
    expect(await (await request(path+"/"+id,{login:user})).json()).toEqual(qualityFixture);
    expect(await (await request("/historical-quality-policies/"+qualityPolicy.id,{login:user})).json()).toEqual(qualityPolicy);
    expect((await request(path+"/"+id+"?data_mode=REPLAY",{login:user})).status).toBe(422);
    expect((await request(path+"/invalid",{login:user})).status).toBe(422);
    try {
      qualityResponse={...qualityFixture,private:token};
      const failed=await request(path,{body,login:user});
      expect(failed.status).toBe(502);expect(await failed.text()).not.toContain(token);
    } finally {qualityResponse=qualityFixture;}
  });

  it("discovers quality rules with bounded scope and rejects repeated cursors", async()=>{
    const path="/historical-quality-policies",before=calls.length;
    expect((await request(path)).status).toBe(401);
    for(const query of ["?data_mode=REPLAY","?limit=51","?horizon_sessions=2","?cursor=bad"])
      expect((await request(path+query,{login:user})).status).toBe(422);
    expect(calls).toHaveLength(before);
    const query="?limit=1&horizon_sessions="+qualityFixture.policy.horizon_sessions;
    expect(await (await request(path+query,{login:user})).json()).toEqual(qualityPageResponse);
    expect(calls.at(-1)).toMatchObject({owner:user.id,path:"/internal/v1/historical-quality-policies"+query});
    expect((await request(path+"?cursor="+qualityPolicy.id,{login:user})).status).toBe(502);
    try {
      qualityPageResponse={items:[],next_cursor:null};
      expect(await (await request(path+"?cursor="+qualityPolicy.id,{login:user})).json()).toEqual(qualityPageResponse);
    } finally {qualityPageResponse={items:[qualityPolicy],next_cursor:qualityPolicy.id};}
  });

  it("binds probability assessment requests to authenticated session and exact evidence", async()=>{
    const path="/probability-quality-assessments";
    const body={policy_id:probabilityPolicy.id,calibration_id:probabilityFixture.calibration_id};
    const before=calls.length;
    expect((await request(path,{body})).status).toBe(401);
    expect((await request(path,{body,login:user,csrf:"wrong"})).status).toBe(403);
    expect((await request(path,{body,login:user,origin:"https://other.invalid"})).status).toBe(403);
    expect((await request(path,{body:{...body,data_mode:"LIVE_RESEARCH"},login:user})).status).toBe(422);
    expect((await request(path+"?policy=other",{body,login:user})).status).toBe(422);
    expect(calls).toHaveLength(before);
    expect(await (await request(path,{body,login:user})).json()).toEqual(probabilityFixture);
    expect(calls.at(-1)).toMatchObject({owner:user.id,path:"/internal/v1/probability-quality-assessments",body});
    const id="probability-quality-assessment-"+probabilityFixture.content_hash;
    expect(await (await request(path+"/"+id,{login:user})).json()).toEqual(probabilityFixture);
    expect(await (await request("/probability-quality-policies/"+probabilityPolicy.id,{login:user})).json()).toEqual(probabilityPolicy);
    expect((await request(path+"/"+id+"?data_mode=REPLAY",{login:user})).status).toBe(422);
    expect((await request(path+"/invalid",{login:user})).status).toBe(422);
    try {
      probabilityResponse={...probabilityFixture,private:token};
      const failed=await request(path,{body,login:user});
      expect(failed.status).toBe(502);expect(await failed.text()).not.toContain(token);
    } finally {probabilityResponse=probabilityFixture;}
  });

  it("restricts cross-owner quality registration to configured reviewers with write admission",async()=>{
    const body={proposal_id:gateFixture.proposal_id,price_assessment_id:gateFixture.price_assessment_id,probability_assessment_id:gateFixture.probability_assessment_id};
    const path="/model-review-quality-gates",id="model-quality-gate-"+gateFixture.content_hash;
    expect((await request(path,{body,login:user})).status).toBe(403);
    const config=readConfig({YTA_ENV:"test",YTA_DATABASE_URL:url!,YTA_ALPHA_URL:alphaOrigin,YTA_ALPHA_TOKEN:token,
      YTA_WEB_ORIGIN:origin,YTA_EXPERIMENTAL_MODELS:"ENABLED",YTA_MODEL_REVIEWER_IDS:user.id});
    const enabled=await createBff({pool:server.pool,config,startRunner:false,log:()=>{}});
    try {
      await enabled.app.listen(0,"127.0.0.1");
      const base=(await enabled.app.getUrl())+"/api/yta/v1"+path;
      const headers={Cookie:user.cookie,Origin:origin,"X-CSRF-Token":user.csrf,"Content-Type":"application/json"};
      const post=(h:Record<string,string>,value:unknown=body,suffix="")=>fetch(base+suffix,{method:"POST",headers:h,body:JSON.stringify(value)});
      const before=calls.length;
      expect((await post({"Content-Type":"application/json"})).status).toBe(401);
      expect((await post({...headers,Cookie:other.cookie,"X-CSRF-Token":other.csrf,"X-Yta-Owner-Id":user.id})).status).toBe(403);
      expect((await fetch(base+"/"+id,{headers:{Cookie:other.cookie}})).status).toBe(403);
      expect((await post({...headers,"X-CSRF-Token":"wrong"})).status).toBe(403);
      expect((await post({...headers,Origin:"https://other.invalid"})).status).toBe(403);
      expect((await post(headers,{...body,owner_id:other.id})).status).toBe(422);
      expect((await post(headers,body,"?data_mode=DEMO")).status).toBe(422);
      expect(calls).toHaveLength(before);
      const response=await post(headers);expect(response.status).toBe(200);expect(await response.json()).toEqual(gateFixture);
      expect(calls.at(-1)).toMatchObject({owner:user.id,body,path:"/internal/v1"+path});
      expect(await (await fetch(base+"/"+id,{headers})).json()).toEqual(gateFixture);
      gateResponse={...gateFixture,private:token};
      const bad=await post(headers);expect(bad.status).toBe(502);expect(await bad.text()).not.toContain(token);gateResponse=gateFixture;
      const paused=await createBff({pool:server.pool,config:{...config,YTA_WRITE_ADMISSION:"PAUSED"},startRunner:false,log:()=>{}});
      try {
        await paused.app.listen(0,"127.0.0.1");
        const pausedBase=(await paused.app.getUrl())+"/api/yta/v1"+path,before=calls.length;
        expect((await fetch(pausedBase,{method:"POST",headers,body:JSON.stringify(body)})).status).toBe(503);
        expect(calls).toHaveLength(before);
        expect((await fetch(pausedBase+"/"+id,{headers})).status).toBe(200);
      } finally {await paused.close();}
    } finally {gateResponse=gateFixture;await enabled.close();}
  });

  it("binds combined gate requests to authenticated session and exact evidence", async()=>{
    const path="/model-quality-gates";
    const body={proposal_id:gateFixture.proposal_id,price_assessment_id:gateFixture.price_assessment_id,probability_assessment_id:gateFixture.probability_assessment_id};
    const before=calls.length;
    expect((await request(path,{body})).status).toBe(401);
    expect((await request(path,{body,login:user,csrf:"wrong"})).status).toBe(403);
    expect((await request(path,{body,login:user,origin:"https://other.invalid"})).status).toBe(403);
    expect((await request(path,{body:{...body,data_mode:"LIVE_RESEARCH"},login:user})).status).toBe(422);
    expect((await request(path+"?policy=other",{body,login:user})).status).toBe(422);
    expect((await request(path,{body:{...body,owner_id:user.id},login:user})).status).toBe(422);
    expect(calls).toHaveLength(before);
    expect(await (await request(path,{body,login:user})).json()).toEqual(gateFixture);
    expect(calls.at(-1)).toMatchObject({owner:user.id,path:"/internal/v1/model-quality-gates",body});
    const id="model-quality-gate-"+gateFixture.content_hash;
    expect(await (await request(path+"/"+id,{login:user})).json()).toEqual(gateFixture);
    expect((await request(path+"/"+id+"?data_mode=REPLAY",{login:user})).status).toBe(422);
    expect((await request(path+"/invalid",{login:user})).status).toBe(422);
    try {
      gateResponse={...gateFixture,private:token};
      const failed=await request(path,{body,login:user});
      expect(failed.status).toBe(502);expect(await failed.text()).not.toContain(token);
    } finally {gateResponse=gateFixture;}
  });

  it("discovers probability rules with bounded scope and rejects repeated cursors", async()=>{
    const path="/probability-quality-policies",before=calls.length;
    expect((await request(path)).status).toBe(401);
    for(const query of ["?data_mode=REPLAY","?limit=51","?horizon_sessions=2","?cursor=bad"])
      expect((await request(path+query,{login:user})).status).toBe(422);
    expect(calls).toHaveLength(before);
    const query="?limit=1&horizon_sessions="+probabilityFixture.policy.horizon_sessions;
    expect(await (await request(path+query,{login:user})).json()).toEqual(probabilityPageResponse);
    expect(calls.at(-1)).toMatchObject({owner:user.id,path:"/internal/v1/probability-quality-policies"+query});
    expect((await request(path+"?cursor="+probabilityPolicy.id,{login:user})).status).toBe(502);
    try {
      probabilityPageResponse={items:[],next_cursor:null};
      expect(await (await request(path+"?cursor="+probabilityPolicy.id,{login:user})).json()).toEqual(probabilityPageResponse);
    } finally {probabilityPageResponse={items:[probabilityPolicy],next_cursor:probabilityPolicy.id};}
  });

  it("model review authority comes only from current server configuration and authenticated session",async()=>{
    const config=readConfig({YTA_ENV:"test",YTA_DATABASE_URL:url!,YTA_ALPHA_URL:alphaOrigin,YTA_ALPHA_TOKEN:token,
      YTA_WEB_ORIGIN:origin,YTA_EXPERIMENTAL_MODELS:"ENABLED",YTA_MODEL_REVIEWER_IDS:user.id.toUpperCase()});
    const auth=new Auth(server.pool,config);
    const headers={cookie:user.cookie,origin,"x-csrf-token":user.csrf};
    expect((await auth.modelReviewMutation({headers})).ownerId).toBe(user.id);
    await expect(auth.modelReviewMutation({headers:{}})).rejects.toMatchObject({status:401});
    await expect(auth.modelReviewMutation({headers:{...headers,origin:"https://other.invalid"}})).rejects.toMatchObject({status:403});
    await expect(auth.modelReviewMutation({headers:{...headers,"x-csrf-token":"wrong"}})).rejects.toMatchObject({status:403});
    await expect(auth.modelReviewMutation({headers:{cookie:other.cookie,origin,"x-csrf-token":other.csrf,
      "x-yta-owner-id":user.id,"x-yta-role":"reviewer","x-model-reviewer":"true"}})).rejects.toMatchObject({code:"MODEL_REVIEW_FORBIDDEN"});
    const removed=new Auth(server.pool,{...config,YTA_MODEL_REVIEWER_IDS:[]});
    await expect(removed.modelReviewMutation({headers})).rejects.toMatchObject({code:"MODEL_REVIEW_FORBIDDEN"});
    const disabled=new Auth(server.pool,{...config,YTA_EXPERIMENTAL_MODELS:"DISABLED"});
    await expect(disabled.modelReviewMutation({headers})).rejects.toMatchObject({code:"MODEL_REVIEW_FORBIDDEN"});
    const isolatedToken=randomBytes(32).toString("base64url"),tokenHash=digest(isolatedToken);
    await server.pool.query("INSERT INTO yta.login_sessions(token_hash,owner_id,credential_id,expires_at) SELECT $1,$2,max(id),now()+interval '1 hour' FROM yta.login_credentials WHERE owner_id=$2",[tokenHash,user.id]);
    const isolatedHeaders={cookie:auth.cookieName+"="+isolatedToken,origin,"x-csrf-token":digest("csrf:"+isolatedToken)};
    expect((await auth.modelReviewMutation({headers:isolatedHeaders})).ownerId).toBe(user.id);
    await server.pool.query("INSERT INTO yta.login_revocations(token_hash) VALUES($1)",[tokenHash]);
    await expect(auth.modelReviewMutation({headers:isolatedHeaders})).rejects.toMatchObject({status:401});
    const enabled=await createBff({pool:server.pool,config,startRunner:false,log:()=>{}});
    try {
      await enabled.app.listen(0,"127.0.0.1");
      for(const account of [user,other]) {
        const response=await fetch(`${await enabled.app.getUrl()}/api/yta/v1/me`,{headers:{Cookie:account.cookie}});
        expect(response.status).toBe(200);
        expect((await response.json()).can_review_models).toBe(account.id===user.id);
      }
      expect((await (await request("/me",{login:user})).json()).can_review_models).toBe(false);
    } finally {await enabled.close();}
  });

  it.each([reviewFixture,reviewV2Fixture])("requires configured reviewers on public review routes and preserves exact review identity",async(fixture)=>{
    const config=readConfig({YTA_ENV:"test",YTA_DATABASE_URL:url!,YTA_ALPHA_URL:alphaOrigin,YTA_ALPHA_TOKEN:token,
      YTA_WEB_ORIGIN:origin,YTA_EXPERIMENTAL_MODELS:"ENABLED",YTA_MODEL_REVIEWER_IDS:user.id});
    const enabled=await createBff({pool:server.pool,config,startRunner:false,log:()=>{}});
    const key=randomUUID(),r=fixture.review;
    const body={proposal_id:r.proposal_id,...(r.schema_version==="model-review-record-v2" ? {quality_gate_id:r.quality_gate_id} : {assessment_id:r.assessment_id}),action:r.action,reason:r.reason,expected_previous_review_id:r.previous_review_id};
    const raw={...r,reviewer_id:user.id,request_id:key};delete raw.content_hash;
    const saved={...fixture,review:{...raw,content_hash:hashContent(raw)}};
    reviewResponse=saved;
    try {
      await enabled.app.listen(0,"127.0.0.1");
      const base=(await enabled.app.getUrl())+"/api/yta/v1/model-reviews";
      const headers={Cookie:user.cookie,Origin:origin,"X-CSRF-Token":user.csrf,"Idempotency-Key":key,"Content-Type":"application/json"};
      const post=(h:Record<string,string>,payload:unknown=body,suffix="")=>fetch(base+suffix,{method:"POST",headers:h,body:JSON.stringify(payload)});
      const before=calls.length;
      expect((await post({Origin:origin,"Content-Type":"application/json"})).status).toBe(401);
      expect((await post({...headers,Cookie:other.cookie,"X-CSRF-Token":other.csrf,"X-YTA-Owner-ID":user.id})).status).toBe(403);
      expect((await fetch(base+"/"+saved.id,{headers:{Cookie:other.cookie}})).status).toBe(403);
      expect((await post({...headers,"X-CSRF-Token":"wrong"})).status).toBe(403);
      expect((await post({...headers,Origin:"https://other.invalid"})).status).toBe(403);
      expect((await post({...headers,"Idempotency-Key":"bad"})).status).toBe(422);
      expect((await post(headers,{...body,reviewer_id:other.id})).status).toBe(422);
      expect((await post(headers,body,"?data_mode=REPLAY")).status).toBe(422);
      expect(calls).toHaveLength(before);
      const response=await post({...headers,"Idempotency-Key":key.toUpperCase()});
      expect(response.status).toBe(200);expect(await response.json()).toEqual(saved);
      expect(calls.at(-1)).toMatchObject({owner:user.id,key,body,path:"/internal/v1/model-reviews"});
      expect(await (await fetch(base+"/"+saved.id,{headers:{Cookie:user.cookie}})).json()).toEqual(saved);
      expect((await request("/model-reviews/"+saved.id,{login:user})).status).toBe(403);
      const inbox=base.replace(/model-reviews$/,"model-review-proposals");
      const inboxBefore=calls.length;
      expect((await fetch(inbox)).status).toBe(401);
      expect((await fetch(inbox,{headers:{Cookie:other.cookie,"X-YTA-Owner-ID":user.id}})).status).toBe(403);
      expect((await fetch(inbox+"/"+proposalFixture.id,{headers:{Cookie:other.cookie}})).status).toBe(403);
      expect((await fetch(inbox+"?owner_id="+user.id,{headers:{Cookie:user.cookie}})).status).toBe(422);
      expect(calls).toHaveLength(inboxBefore);
      const inboxRead=await fetch(inbox+"?limit=1",{headers:{Cookie:user.cookie}});
      expect(inboxRead.status).toBe(200);
      expect(await inboxRead.json()).toEqual({items:[proposalFixture],next_cursor:null});
      expect(calls.at(-1)).toMatchObject({owner:user.id,path:"/internal/v1/model-review-proposals?limit=1"});
      const proposalRead=await fetch(inbox+"/"+proposalFixture.id,{headers:{Cookie:user.cookie}});
      expect(proposalRead.status).toBe(200);expect(await proposalRead.json()).toEqual(proposalFixture);
      expect((await fetch(inbox+"?cursor="+proposalFixture.id,{headers:{Cookie:user.cookie}})).status).toBe(502);
      const history=base.replace(/model-reviews$/,`model-proposals/${r.proposal_id}/reviews`);
      const historyBefore=calls.length;
      expect((await fetch(history)).status).toBe(401);
      expect((await fetch(history,{headers:{Cookie:other.cookie,"X-YTA-Owner-ID":user.id}})).status).toBe(403);
      for(const query of ["limit=51","cursor=bad","owner_id="+user.id,"data_mode=DEMO"]) {
        expect((await fetch(history+"?"+query,{headers:{Cookie:user.cookie}})).status).toBe(422);
      }
      expect(calls).toHaveLength(historyBefore);
      const historyRead=await fetch(history+"?limit=1",{headers:{Cookie:user.cookie}});
      expect(historyRead.status).toBe(200);
      expect(await historyRead.json()).toEqual({items:[saved],next_cursor:null});
      expect(calls.at(-1)).toMatchObject({owner:user.id,path:`/internal/v1/model-proposals/${r.proposal_id}/reviews?limit=1`});
      expect((await fetch(history+"?cursor="+saved.id,{headers:{Cookie:user.cookie}})).status).toBe(502);
      reviewResponse={...saved,private:token};
      expect((await fetch(history,{headers:{Cookie:user.cookie}})).status).toBe(502);
      const invalid=await post(headers);expect(invalid.status).toBe(502);expect(await invalid.text()).not.toContain(token);
      reviewResponse=saved;
      const paused=await createBff({pool:server.pool,config:{...config,YTA_WRITE_ADMISSION:"PAUSED"},startRunner:false,log:()=>{}});
      try {
        await paused.app.listen(0,"127.0.0.1");
        const pbase=(await paused.app.getUrl())+"/api/yta/v1/model-reviews";
        const upstream=calls.length;
        expect((await fetch(pbase,{method:"POST",headers,body:JSON.stringify(body)})).status).toBe(503);
        expect(calls).toHaveLength(upstream);
        expect((await fetch(pbase+"/"+saved.id,{headers:{Cookie:user.cookie}})).status).toBe(200);
      } finally {await paused.close();}
    } finally {reviewResponse=reviewFixture;await enabled.close();}
  });

  it("authenticates and scopes backtest catalog filters before contacting Alpha",async()=>{
    const path="/backtest-reports",before=calls.length;
    expect((await request(path)).status).toBe(401);
    for(const query of ["?data_mode=DEMO","?limit=51","?status=EMPTY","?limit=1&limit=2","?cursor=bad"])
      expect((await request(path+query,{login:user})).status).toBe(422);
    expect(calls).toHaveLength(before);
    const response=await request(path+"?limit=2",{login:user});
    expect(response.status).toBe(200);expect(await response.json()).toEqual(backtestPageFixture);
    expect(calls.at(-1)).toMatchObject({owner:user.id,path:"/internal/v1/backtest-reports?limit=2"});
    expect((await request(path+"?status=COMPLETE",{login:user})).status).toBe(502);
    try {
      backtestPageResponse={...backtestPageFixture,secret:token};
      const invalid=await request(path,{login:user});expect(invalid.status).toBe(502);
      expect(await invalid.text()).not.toContain(token);
    }finally{backtestPageResponse=backtestPageFixture;}
  });

  it("authenticates training catalog, validates query and rejects upstream scope leaks",async()=>{
    const path="/experimental-models/"+trainingFixture.model_id+"/training-evidence",before=calls.length;
    expect((await request(path)).status).toBe(401);
    for(const query of ["?limit=51","?limit=1&limit=2","?data_mode=DEMO","?cursor=bad"])
      expect((await request(path+query,{login:user})).status).toBe(422);
    expect(calls).toHaveLength(before);
    const response=await request(path+"?limit=2",{login:user});expect(response.status).toBe(200);
    expect(await response.json()).toEqual(trainingPageFixture);
    expect(calls.at(-1)).toMatchObject({owner:user.id,path:"/internal/v1"+path+"?limit=2"});
    expect((await request(path+"?limit=1",{login:user})).status).toBe(502);
    expect((await request(path+"?cursor="+trainingPageFixture.items[0].id,{login:user})).status).toBe(502);
    try {
      trainingPageResponse={...trainingPageFixture,private:token};
      const invalid=await request(path,{login:user});expect(invalid.status).toBe(502);expect(await invalid.text()).not.toContain(token);
      trainingPageResponse={...trainingPageFixture,items:trainingPageFixture.items.map((row:Record<string,unknown>)=>({...row,data_mode:"DEMO"}))};
      expect((await request(path,{login:user})).status).toBe(502);
    }finally{trainingPageResponse=trainingPageFixture;}
  });

  it("authenticates training evidence and rejects client scope and malformed upstream responses",async()=>{
    const path="/training-evidence/"+trainingFixture.id,before=calls.length;
    expect((await request(path)).status).toBe(401);
    expect((await request(path+"?data_mode=DEMO",{login:user})).status).toBe(422);
    expect((await request("/training-evidence/bad",{login:user})).status).toBe(422);
    expect(calls).toHaveLength(before);
    const response=await request(path,{login:user});expect(response.status).toBe(200);
    expect(await response.json()).toEqual(trainingFixture);
    expect(calls.at(-1)).toMatchObject({owner:user.id,path:"/internal/v1"+path});
    try {
      trainingResponse={...trainingFixture,private:token};
      const invalid=await request(path,{login:user});expect(invalid.status).toBe(502);expect(await invalid.text()).not.toContain(token);
      trainingResponse={...trainingFixture,report:{...trainingFixture.report,content_hash:"0".repeat(64)}};
      expect((await request(path,{login:user})).status).toBe(502);
    }finally{trainingResponse=trainingFixture;}
  });

  it("authenticates backtest reads and rejects invalid upstream evidence without leaking it",async()=>{
    const path="/backtest-reports/"+backtestFixture.id,before=calls.length;
    expect((await request(path)).status).toBe(401);
    expect((await request(path+"?data_mode=DEMO",{login:user})).status).toBe(422);
    expect((await request("/backtest-reports/bad",{login:user})).status).toBe(422);
    expect(calls).toHaveLength(before);
    const response=await request(path,{login:user});
    expect(response.status).toBe(200);expect(await response.json()).toEqual(backtestFixture);
    expect(calls.at(-1)).toMatchObject({owner:user.id,path:"/internal/v1"+path});
    try {
      backtestResponse={...backtestFixture,private:token};
      const invalid=await request(path,{login:user});expect(invalid.status).toBe(502);
      expect(await invalid.text()).not.toContain(token);
      backtestResponse={...backtestFixture,report:{...backtestFixture.report,content_hash:"0".repeat(64)}};
      expect((await request(path,{login:user})).status).toBe(502);
    }finally{backtestResponse=backtestFixture;}
  });

  it("authenticates calibration reads and validates identity without exposing bad upstream data",async()=>{
    const path="/experimental-calibrations/"+calibrationFixture.id,before=calls.length;
    expect((await request(path)).status).toBe(401);
    expect((await request(path+"?data_mode=LIVE_RESEARCH",{login:user})).status).toBe(422);
    expect((await request("/experimental-calibrations/bad",{login:user})).status).toBe(422);
    expect(calls).toHaveLength(before);
    const response=await request(path,{login:user});
    expect(response.status).toBe(200);expect(await response.json()).toEqual(calibrationFixture);
    expect(calls.at(-1)).toMatchObject({owner:user.id,path:"/internal/v1"+path});
    try {
      calibrationResponse={...calibrationFixture,private:token};
      const invalid=await request(path,{login:user});expect(invalid.status).toBe(502);
      expect(await invalid.text()).not.toContain(token);
      calibrationResponse={...calibrationFixture,report_hash:"0".repeat(64)};
      expect((await request(path,{login:user})).status).toBe(502);
    }finally{calibrationResponse=calibrationFixture;}
  });

  it("keeps the catalog disabled by default without contacting Alpha", async () => {
    const disabled = await createBff({startRunner: false, log: entry => observations.push(entry),
      config: readConfig({YTA_ENV: "test", YTA_DATABASE_URL: url!, YTA_ALPHA_URL: alphaOrigin,
        YTA_ALPHA_TOKEN: token, YTA_WEB_ORIGIN: origin})});
    try {
      await disabled.app.listen(0, "127.0.0.1");
      const before = calls.length;
      const me = await fetch(`${await disabled.app.getUrl()}/api/yta/v1/me`, {headers: {Cookie: user.cookie}});
      expect((await me.json()).experimental_models_enabled).toBe(false);
      const result = await fetch(`${await disabled.app.getUrl()}/api/yta/v1/experimental-models`,
        {headers: {Cookie: user.cookie}});
      expect(result.status).toBe(503);
      expect((await result.json()).error.code).toBe("EXPERIMENTAL_MODELS_DISABLED");
      expect((await fetch(`${await disabled.app.getUrl()}/api/yta/v1/experimental-models/${trainingFixture.model_id}/training-evidence`,{headers:{Cookie:user.cookie}})).status).toBe(503);
      expect((await fetch(`${await disabled.app.getUrl()}/api/yta/v1/training-evidence/${trainingFixture.id}`,{headers:{Cookie:user.cookie}})).status).toBe(503);
      expect((await fetch(`${await disabled.app.getUrl()}/api/yta/v1/backtest-reports`,{headers:{Cookie:user.cookie}})).status).toBe(503);
      expect((await fetch(`${await disabled.app.getUrl()}/api/yta/v1/backtest-reports/${backtestFixture.id}`,{headers:{Cookie:user.cookie}})).status).toBe(503);
      expect((await fetch(`${await disabled.app.getUrl()}/api/yta/v1/experimental-calibrations/${calibrationFixture.id}`,{headers:{Cookie:user.cookie}})).status).toBe(503);
      const report = await fetch(`${await disabled.app.getUrl()}/api/yta/v1/experimental-evaluations/model-evaluation-experimental-${priceReport.content_hash}`, {headers: {Cookie: user.cookie}});
      expect(report.status).toBe(503);
      const list = await fetch(`${await disabled.app.getUrl()}/api/yta/v1/experimental-models/${priceReport.model_id}/evaluations`, {headers: {Cookie: user.cookie}});
      expect(list.status).toBe(503);
      const compare = await fetch(`${await disabled.app.getUrl()}/api/yta/v1/experimental-evaluations/compare`, {
        method: "POST", headers: {Cookie: user.cookie, Origin: origin, "X-CSRF-Token": user.csrf, "Content-Type": "application/json"},
        body: JSON.stringify({evaluation_ids: comparison.evaluation_ids}),
      });
      expect(compare.status).toBe(503);
      const proposal = await fetch(`${await disabled.app.getUrl()}/api/yta/v1/model-proposals/${proposalFixture.id}`, {headers:{Cookie:user.cookie}});
      expect(proposal.status).toBe(503);
      const history=await fetch(`${await disabled.app.getUrl()}/api/yta/v1/model-proposals`,{headers:{Cookie:user.cookie}});
      expect(history.status).toBe(503);
      for(const path of ["/model-quality-gates/model-quality-gate-"+gateFixture.content_hash, "/probability-quality-policies", "/probability-quality-policies/"+probabilityPolicy.id, "/probability-quality-assessments/probability-quality-assessment-"+probabilityFixture.content_hash, "/historical-quality-policies", "/historical-quality-policies/"+qualityPolicy.id,"/historical-quality-assessments/historical-quality-assessment-"+qualityFixture.content_hash]) {
        expect((await fetch(`${await disabled.app.getUrl()}/api/yta/v1`+path,{headers:{Cookie:user.cookie}})).status).toBe(503);
      }
      expect(calls).toHaveLength(before);
    } finally { await disabled.close(); }
  });

  it("authenticates and validates market freshness without claiming stale data is fresh", async () => {
    const path = "/stocks/NVDA/freshness";
    const before = calls.length;
    expect((await request(path)).status).toBe(401);
    expect((await request("/stocks/AAPL/freshness", {login: user})).status).toBe(422);
    expect(calls).toHaveLength(before);
    expect(await (await request(path, {login: user})).json()).toEqual(freshness);
    try {
      freshnessResponse = {...freshness, status: "STALE", reason: "REFRESH_FAILED"};
      expect(await (await request(path, {login: user})).json()).toMatchObject({status: "STALE"});
      for (const invalid of [
        {...freshness, status: "UNAVAILABLE"}, {...freshness, source_id: null},
        {...freshness, data_mode: "LIVE_RESEARCH"}, {...freshness, secret: token},
      ]) {
        freshnessResponse = invalid;
        const result = await request(path, {login: user});
        expect(result.status).toBe(502);
        expect(await result.text()).not.toContain(token);
      }
    } finally { freshnessResponse = freshness; }
  });

  it("reads owned evidence and rejects invalid upstream responses", async () => {
    const path = `/prediction-drafts/${evidenceId}/evidence`;
    const before = calls.length;
    expect((await request(path)).status).toBe(401);
    expect(calls).toHaveLength(before);
    const response = await request(path, {login: user});
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(evidence);
    expect(calls.at(-1)?.owner).toBe(user.id);
    expect((await request(path + `?owner_id=${user.id}`, {login: other})).status).toBe(404);
    expect(calls.at(-1)?.owner).toBe(other.id);
    try {
      evidenceResponse = {...evidence, credential: token};
      const malformed = await request(path, {login: user});
      expect(malformed.status).toBe(502);
      expect(await malformed.text()).not.toContain(token);
      evidenceResponse = {...evidence, snapshot: {...evidence.snapshot, data_mode: "LIVE_RESEARCH"}};
      expect((await request(path, {login: user})).status).toBe(404);
      evidenceResponse = {...evidence, snapshot: {...evidence.snapshot, bars: [{...evidence.snapshot.bars[0], close: 1.23}]}};
      expect((await request(path, {login: user})).status).toBe(502);
    } finally { evidenceResponse = evidence; }
  });

  it("protects documentation and local assets with current session authentication", async () => {
    const paths = ["/docs", "/docs/openapi.json", "/docs/init.js", "/docs/swagger-ui.css", "/docs/swagger-ui-bundle.js"];
    for (const path of paths) expect((await request(path)).status).toBe(401);
    const html = await request("/docs", {login: user});
    expect(html.headers.get("content-security-policy")).toContain("connect-src 'self'");
    expect(html.headers.get("cache-control")).toBe("no-store");
    expect(await html.text()).toContain("YTA API 文档");
    const spec = await (await request("/docs/openapi.json", {login: user})).json();
    expect(spec.openapi).toBe("3.1.0");
    expect(spec.paths["/predictions"].post.operationId).toBe("freeze");
    for (const path of paths.slice(2)) {
      const response = await request(path, {login: user});
      expect(response.status).toBe(200);
      expect(response.headers.get("cache-control")).toBe("no-store");
      expect((await response.text()).length).toBeGreaterThan(100);
    }
    expect((await request("/docs/package.json", {login: user})).status).toBe(404);
    const token = randomBytes(32).toString("base64url");
    await server.pool.query("INSERT INTO yta.login_sessions(token_hash,owner_id,credential_id,expires_at) SELECT $1,$2,max(id),now()+interval '1 hour' FROM yta.login_credentials WHERE owner_id=$2", [digest(token), ownerA]);
    const disposable = {...user, cookie: "yta_session=" + token};
    expect((await request("/docs", {login: disposable})).status).toBe(200);
    await server.pool.query("INSERT INTO yta.login_revocations(token_hash) VALUES ($1)", [digest(token)]);
    for (const path of paths) expect((await request(path, {login: disposable})).status).toBe(401);
  });

  it("rejects unknown, repeated and malformed query fields before research or Alpha access", async () => {
    const before = calls.length;
    const paths = [`/model-proposals/${proposalFixture.id}`, "/sessions", "/predictions", "/evaluation-reports",
      `/sessions/${randomUUID()}/messages`, `/runs/${randomUUID()}/events`];
    for (const path of paths) {
      for (const query of ["owner_id=" + other.id, "data_mode=LIVE_RESEARCH", "cursor=0&cursor=1"]) {
        const response = await request(path + "?" + query, {login: user});
        expect(response.status).toBe(422);
        expect(response.headers.get("content-type")).toContain("application/json");
        expect(await response.json()).toMatchObject({error: {code: "INVALID_REQUEST", retryable: false}});
      }
    }
    for (const path of paths.slice(0, 3)) {
      for (const query of ["limit=1&limit=2", "limit=01", "limit=1e2", "limit=1000"]) {
        expect((await request(path + "?" + query, {login: user})).status).toBe(422);
      }
    }
    expect(calls).toHaveLength(before);
  });

  it("returns a retryable overload error without issuing a session", async () => {
    const before = await server.pool.query("SELECT count(*)::integer AS n FROM yta.login_sessions WHERE owner_id=$1", [ownerA]);
    // Fault injection at the resource boundary; real derivation saturation is tested separately.
    const overloaded = vi.spyOn(passwords, "verifyPassword").mockRejectedValueOnce(
      new ApiError("AUTH_BUSY", "Login is busy; try again shortly", 503, true));
    try {
      const response = await request("/auth/login", {body: {email: emailA, password}});
      expect(response.status).toBe(503);
      expect(response.headers.get("set-cookie")).toBeNull();
      expect(await response.json()).toMatchObject({error: {code: "AUTH_BUSY", retryable: true, request_id: expect.any(String)}});
      const after = await server.pool.query("SELECT count(*)::integer AS n FROM yta.login_sessions WHERE owner_id=$1", [ownerA]);
      expect(after.rows).toEqual(before.rows);
    } finally { overloaded.mockRestore(); }
  });
  async function completed(runId: string) {
    for (let attempt = 0; attempt < 100; attempt++) {
      const response = await request(`/runs/${runId}`, { login: user });
      const value = await response.json();
      if (["COMPLETED", "FAILED", "CANCELED", "EXECUTION_UNKNOWN"].includes(value.status)) return value;
      await delay(20);
    }
    throw new Error("Run did not finish");
  }

  it("fails research without publishing a draft/report when tool snapshots disagree", async () => {
    invalidReportHash = true;
    try {
      const id = await session();
      const key = randomUUID();
      const response = await request(`/sessions/${id}/messages`, {login: user, body: {text: "Conflicting evidence probe", request_id: key}, key});
      expect(response.status).toBe(202);
      const runId = (await response.json()).run_id;
      const result = await completed(runId);
      expect(result.status).toBe("FAILED");
      expect(result.draft).toBeNull(); expect(result.report).toBeNull();
      const events = await server.research.events(user.id, runId, 0);
      expect(events.some(event => event.type === "prediction.draft" || event.type === "message.completed")).toBe(false);
      expect(events.at(-1)).toMatchObject({type: "run.failed", payload: {code: "RESEARCH_REPORT_INVALID"}});
    } finally { invalidReportHash = false; }
  });

  beforeAll(async () => {
    if (!url || !new URL(url).pathname.endsWith("_test")) throw new Error("Dedicated *_test database required");
    const pool = database(url);
    for (const [id, email] of [[ownerA, emailA], [ownerB, emailB]]) {
      await pool.query("INSERT INTO yta.users(id,email) VALUES ($1,$2)", [id, email]);
      await pool.query("INSERT INTO yta.login_credentials(owner_id,password_hash) VALUES ($1,$2)", [id, await hashPassword(password)]);
    }
    await pool.end();
    alpha = createServer(async (req, res) => {
      if (req.headers.authorization !== `Bearer ${token}`) { res.writeHead(401).end(); return; }
      const chunks: Buffer[] = []; for await (const chunk of req) chunks.push(Buffer.from(chunk));
      const body = chunks.length ? JSON.parse(Buffer.concat(chunks).toString()) : undefined;
      const path = req.url!;
      calls.push({ path, owner: req.headers["x-yta-owner-id"] as string | undefined, body, key: req.headers["idempotency-key"] as string | undefined });
      res.setHeader("Content-Type", "application/json");
      if (path === "/internal/v1/ready") {
        res.statusCode = alphaReady ? 200 : 503;
        res.end(JSON.stringify({status: alphaReady ? "ready" : "not_ready", service: "alpha-engine", data_mode: "REPLAY", checks: {database: "ready", market: "replay"}}));
      }
      else if (path === `/internal/v1/prediction-drafts/${evidenceId}/evidence`) {
        if (req.headers["x-yta-owner-id"] !== ownerA) {
          res.statusCode = 404;
          res.end(JSON.stringify({error: {code: "NOT_FOUND", message: "Draft evidence not found", request_id: "req_test", retryable: false}}));
        } else res.end(JSON.stringify(evidenceResponse));
      }
      else if (/^\/internal\/v1\/experimental-models\/[^/]+\/evaluations\?/.test(path)) res.end(JSON.stringify(reportPageResponse));
      else if (path.startsWith("/internal/v1/model-review-proposals?")) res.end(JSON.stringify({items:[proposalFixture],next_cursor:null}));
      else if (path.startsWith("/internal/v1/model-review-proposals/")) res.end(JSON.stringify(proposalFixture));
      else if (/^\/internal\/v1\/experimental-models\/[^/]+\/training-evidence\?/.test(path)) res.end(JSON.stringify(trainingPageResponse));
      else if (path.startsWith("/internal/v1/training-evidence/")) res.end(JSON.stringify(trainingResponse));
      else if (path.startsWith("/internal/v1/backtest-reports?")) res.end(JSON.stringify(backtestPageResponse));
      else if (path.startsWith("/internal/v1/backtest-reports/")) res.end(JSON.stringify(backtestResponse));
      else if (path.startsWith("/internal/v1/experimental-calibrations/")) res.end(JSON.stringify(calibrationResponse));
      else if (path.startsWith("/internal/v1/model-reviews")) res.end(JSON.stringify(reviewResponse));
      else if ((path.startsWith("/internal/v1/model-quality-gates") || path.startsWith("/internal/v1/model-review-quality-gates"))) res.end(JSON.stringify(gateResponse));
      else if (path.startsWith("/internal/v1/probability-quality-policies?")) res.end(JSON.stringify(probabilityPageResponse));
      else if (path.startsWith("/internal/v1/probability-quality-policies/")) res.end(JSON.stringify(probabilityPolicy));
      else if (path.startsWith("/internal/v1/probability-quality-assessments")) res.end(JSON.stringify(probabilityResponse));
      else if (path.startsWith("/internal/v1/historical-quality-policies?")) res.end(JSON.stringify(qualityPageResponse));
      else if (path.startsWith("/internal/v1/historical-quality-policies/")) res.end(JSON.stringify(qualityPolicy));
      else if (path.startsWith("/internal/v1/historical-quality-assessments")) res.end(JSON.stringify(qualityResponse));
      else if (/^\/internal\/v1\/model-proposals\/[^/]+\/reviews\?/.test(path)) res.end(JSON.stringify({items:[reviewResponse],next_cursor:null}));
      else if (path.startsWith("/internal/v1/model-proposals?")) res.end(JSON.stringify(proposalPageResponse));
      else if (path.startsWith("/internal/v1/model-proposals")) res.end(JSON.stringify(proposalResponse));
      else if (path === "/internal/v1/experimental-evaluations/compare") res.end(JSON.stringify(comparisonResponse));
      else if (path.startsWith("/internal/v1/experimental-evaluations/")) res.end(JSON.stringify(priceReportResponse));
      else if (path.startsWith("/internal/v1/experimental-models?")) res.end(JSON.stringify(modelPageResponse));
      else if (path === "/internal/v1/market/freshness") res.end(JSON.stringify(freshnessResponse));
      else if (path.startsWith("/internal/v1/evaluation-reports")) res.end(JSON.stringify({ report_id: "test-report", data_mode: "REPLAY" }));
      else if (path === "/internal/v1/market/snapshot") res.end(JSON.stringify({ as_of: forecast.as_of, data_mode: "REPLAY", provider: "synthetic-fixture" }));
      else if (path === "/internal/v1/market/evidence") res.end(JSON.stringify({ data_snapshot_hash: forecast.data_snapshot_hash, snapshot: {
        symbol: "NVDA", as_of: forecast.as_of, data_mode: "REPLAY", provider: "synthetic-fixture", source_id: "synthetic-fixture",
        bars: [{ available_at: forecast.as_of }], splits: [],
      } }));
      else if (path === "/internal/v1/market/indicators") res.end(JSON.stringify({ series: {}, as_of: forecast.as_of.replace("Z", "+00:00"), data_mode: "REPLAY", data_snapshot_hash: invalidReportHash ? "f".repeat(64) : forecast.data_snapshot_hash }));
      else if (path === "/internal/v1/prediction-drafts") {
        const id = drafts.get(body.request_id) ?? randomUUID(); drafts.set(body.request_id, id);
        res.end(JSON.stringify({ draft_id: id, forecast }));
      } else if (path === "/internal/v1/predictions") res.end(JSON.stringify({ prediction_id: randomUUID(), status: "FROZEN" }));
      else { res.writeHead(503); res.end(JSON.stringify({ error: { code: "DATA_UNAVAILABLE", message: "Test source unavailable", request_id: "test", retryable: true } })); }
    });
    await new Promise<void>(resolve => alpha.listen(0, "127.0.0.1", resolve));
    const address = alpha.address(); if (!address || typeof address === "string") throw new Error("No test port");
    alphaOrigin = `http://127.0.0.1:${address.port}`;
    // HTTP tests dispatch their own admissions; recovery is exercised separately below.
    // Do not sweep unrelated fixtures from other suites sharing the dedicated test database.
    server = await createBff({startRunner: false, log: entry => observations.push(entry), config: readConfig({ YTA_ENV: "test", YTA_DATABASE_URL: url, YTA_ALPHA_URL: alphaOrigin, YTA_ALPHA_TOKEN: token, YTA_WEB_ORIGIN: origin, YTA_EXPERIMENTAL_MODELS: "ENABLED" }) });
    await server.app.listen(0, "127.0.0.1");
    endpoint = await server.app.getUrl();
    user = await login(emailA); other = await login(emailB);
  }, 20000);

  afterAll(async () => {
    if (server) await server.close();
    if (alpha) await new Promise<void>(resolve => alpha.close(() => resolve()));
  });

  it("paused write admission blocks business writes before persistence and keeps recovery controls", async () => {
    const sessionId = await session();
    const priorKey = randomUUID();
    const prior = await server.research.submit(user.id, sessionId, priorKey, "already accepted", "REPLAY");
    const paused = await createBff({startRunner: false, log: entry => observations.push(entry),
      config: readConfig({YTA_ENV: "test", YTA_DATABASE_URL: url!, YTA_ALPHA_URL: alphaOrigin,
        YTA_ALPHA_TOKEN: token, YTA_WEB_ORIGIN: origin, YTA_WRITE_ADMISSION: "PAUSED"})});
    try {
      await paused.app.listen(0, "127.0.0.1");
      const base = `${await paused.app.getUrl()}/api/yta/v1`;
      const headers = {Cookie: user.cookie, Origin: origin, "X-CSRF-Token": user.csrf,
        "Content-Type": "application/json", "Idempotency-Key": randomUUID(), "X-YTA-Write-Admission": "OPEN"};
      const send = (path: string, body?: unknown) => fetch(base + path, {headers,
        method: body === undefined ? "GET" : "POST", body: body === undefined ? undefined : JSON.stringify(body)});
      const counts = async () => (await server.pool.query(
        "SELECT (SELECT count(*) FROM yta.research_sessions WHERE owner_id=$1) AS sessions, " +
        "(SELECT count(*) FROM yta.research_runs WHERE owner_id=$1) AS runs", [user.id])).rows;
      const before = await counts(), upstream = calls.length;
      const paths = ["/model-quality-gates", "/probability-quality-assessments", "/historical-quality-assessments", "/model-proposals", "/sessions", `/sessions/${sessionId}/messages`, "/predictions",
        `/predictions/${randomUUID()}/corrections`, "/evaluation-reports", `/replay/predictions/${randomUUID()}/settle`];
      for (const path of paths) {
        const result = await send(path, {});
        expect(result.status).toBe(503);
        expect((await result.json()).error).toMatchObject({code: "WRITE_ADMISSION_PAUSED", retryable: false});
      }
      // No client header can open admission, and even an exact prior key cannot submit here.
      const replay = await fetch(base + `/sessions/${sessionId}/messages`, {method: "POST",
        headers: {...headers, "Idempotency-Key": priorKey},
        body: JSON.stringify({request_id: priorKey, text: "already accepted"})});
      expect(replay.status).toBe(503);
      expect(await counts()).toEqual(before);
      expect(calls).toHaveLength(upstream);
      expect((await fetch(base + "/predictions", {method: "POST", headers: {Origin: origin,
        "Content-Type": "application/json"}, body: "{}"})).status).toBe(401);
      expect((await fetch(base + "/predictions", {method: "POST",
        headers: {...headers, "X-CSRF-Token": other.csrf}, body: "{}"})).status).toBe(403);
      expect((await send("/me")).status).toBe(200);
      expect((await send("/sessions")).status).toBe(200);
      expect((await send(`/runs/${prior.run_id}`)).status).toBe(200);
      expect((await send(`/runs/${prior.run_id}/cancel`, {})).status).toBe(202);
      const cancellation = await server.pool.query("SELECT run_id FROM yta.run_cancellations WHERE run_id=$1", [prior.run_id]);
      expect(cancellation.rowCount).toBe(1);
      const ready = await send("/ready");
      expect(ready.status).toBe(503);
      expect((await ready.json()).checks.admission).toBe("PAUSED");
      expect((await send("/health")).status).toBe(200);
      const loginResponse = await send("/auth/login", {email: user.email, password});
      expect(loginResponse.status).toBe(200);
      const identity = await loginResponse.json();
      const logout = await fetch(base + "/auth/logout", {method: "POST", body: "{}",
        headers: {...headers, Cookie: loginResponse.headers.get("set-cookie")!.split(";")[0]!,
          "X-CSRF-Token": identity.csrf_token}});
      expect(logout.status).toBe(200);
      // The open instance resolves the exact saved identity, never a new Run.
      const resumed = await request(`/sessions/${sessionId}/messages`, {login: user,
        body: {request_id: priorKey, text: "already accepted"}, key: priorKey});
      expect(resumed.status).toBe(202);
      expect((await resumed.json()).run_id).toBe(prior.run_id);
      expect(await counts()).toEqual(before);
    } finally { await paused.close(); }
  });

  it("requires login, correct origin and a session-bound CSRF token", async () => {
    const denied = await request("/sessions");
    expect(denied.status).toBe(401);
    expect((await denied.json()).error.request_id).toBe(denied.headers.get("x-request-id"));
    expect((await request("/sessions", { login: user, body: {}, key: randomUUID(), csrf: other.csrf })).status).toBe(403);
    expect((await request("/sessions", { login: user, body: {}, key: randomUUID(), origin: "https://attacker.invalid" })).status).toBe(403);
    expect((await request("/auth/login", { body: { email: emailA, password }, origin: "https://attacker.invalid" })).status).toBe(403);
    expect((await request("/sessions", { login: user, body: { profile_id: "admin" }, key: randomUUID() })).status).toBe(422);
  });

  it("links transport request IDs to persisted research runs without logging prompts", async () => {
    const sessionId = await session();
    const response = await request(`/sessions/${sessionId}/messages`, {login: user,
      body: {text: "private-observability-prompt", request_id: randomUUID()}});
    const runId = (await response.json()).run_id;
    const id = response.headers.get("x-request-id");
    await completed(runId);
    expect(observations.find(entry => entry.event === "bff.research_admitted" && entry.request_id === id))
      .toMatchObject({run_id: runId});
    expect(JSON.stringify(observations)).not.toContain("private-observability-prompt");
    expect(JSON.stringify(observations)).not.toContain(password);
    expect(JSON.stringify(observations)).not.toContain(user.cookie);
  });

  it("paginates sessions with timestamp ties, microseconds, new inserts and scoped cursors", async () => {
    const isolated = randomUUID();
    await server.pool.query("INSERT INTO yta.users(id,email) VALUES ($1,$2)", [isolated, isolated + "@yta.test"]);
    const ids = Array.from({length: 6}, () => randomUUID());
    for (let i = 0; i < ids.length; i++) {
      await server.pool.query("INSERT INTO yta.research_sessions(id,owner_id,profile_id,data_mode,request_key,request_hash,created_at) VALUES ($1::uuid,$2,'alpha-orchestrator','REPLAY',$1::text,$3,$4)",
        [ids[i], isolated, "a".repeat(64), i < 3 ? "2024-12-01T12:00:00.123456Z" : "2024-12-01T12:00:00.123455Z"]);
    }
    const first = await server.research.listSessions(isolated, "REPLAY", undefined, 2);
    const newer = await server.research.createSession(isolated, "REPLAY", randomUUID());
    const found = first.items.map(s => s.session_id);
    let cursor = first.next_cursor;
    while (cursor) {
      const page = await server.research.listSessions(isolated, "REPLAY", cursor, 2);
      found.push(...page.items.map(s => s.session_id)); cursor = page.next_cursor;
    }
    expect(found).toHaveLength(6); expect(new Set(found)).toEqual(new Set(ids));
    expect((await server.research.listSessions(isolated, "REPLAY")).items[0]!.session_id).toBe(newer.session_id);
    expect((await new ResearchRepository(server.pool, "LIVE_RESEARCH").listSessions(isolated, "LIVE_RESEARCH")).items).toEqual([]);
    await expect(server.research.listSessions(other.id, "REPLAY", first.items[0]!.session_id)).rejects.toMatchObject({status: 404});
    await expect(new ResearchRepository(server.pool, "LIVE_RESEARCH").listSessions(isolated, "LIVE_RESEARCH", first.items[0]!.session_id)).rejects.toMatchObject({status: 404});
    expect((await request("/sessions?cursor=" + first.items[0]!.session_id, {login: user})).status).toBe(404);
    for (const query of ["limit=0", "limit=101", "cursor=bad", "data_mode=LIVE_RESEARCH", "owner_id=" + other.id]) {
      expect((await request("/sessions?" + query, {login: user})).status).toBe(422);
    }
    const own = await session();
    await new ResearchRepository(server.pool, "LIVE_RESEARCH").createSession(user.id, "LIVE_RESEARCH", randomUUID());
    const page = await (await request("/sessions?limit=1", {login: user})).json();
    expect(page.items).toHaveLength(1); expect(page.items[0].session_id).toBe(own);
  });

  it("isolates direct reads, cancellation, idempotent retries and recovery by mode", async () => {
    const live = new ResearchRepository(server.pool, "LIVE_RESEARCH");
    const session = await live.createSession(user.id, "LIVE_RESEARCH", randomUUID());
    const requestId = randomUUID();
    const submitted = await live.submit(user.id, session.session_id, requestId, "private live prompt", "LIVE_RESEARCH");
    const run = await live.getRun(user.id, submitted.run_id);
    expect((await live.messages(user.id, session.session_id)).items).toHaveLength(1);
    for (const path of ["/sessions/" + session.session_id + "/messages", "/runs/" + run.id, "/runs/" + run.id + "/events"]) {
      const response = await request(path, {login: user});
      expect(response.status).toBe(404);
      expect(await response.text()).not.toContain("private live prompt");
    }
    expect((await request("/runs/" + run.id + "/cancel", {login: user, body: {}, key: randomUUID()})).status).toBe(404);
    // Even an exact prior request cannot disclose the run through the other mode.
    expect((await request("/sessions/" + session.session_id + "/messages", {login: user, body: {text: "private live prompt", request_id: requestId}})).status).toBe(404);
    expect(await live.isCanceled(run.id)).toBe(false);
    expect((await server.research.pending()).some(item => item.id === run.id)).toBe(false);
    expect((await live.pending()).some(item => item.id === run.id)).toBe(true);
    await expect(server.research.append(run, {type: "run.started", payload: {}})).rejects.toMatchObject({code: "DATA_MODE_MISMATCH"});
    await expect(server.research.append({...run, data_mode: "REPLAY"}, {type: "run.started", payload: {}})).rejects.toMatchObject({status: 404});
    server.runner.launch(run);
    await delay(30);
    expect(await live.events(user.id, run.id, 0)).toEqual([]);
    expect(await live.submit(user.id, session.session_id, requestId, "private live prompt", "LIVE_RESEARCH")).toMatchObject({run_id: run.id, created: false});
    // Finish this synthetic queued run so later test sweeps remain isolated.
    await live.append(run, {type: "run.canceled", payload: {}});
  });

  it("corrections require session CSRF and explicit confirmation", async () => {
    const path = "/predictions/" + randomUUID() + "/corrections";
    const body = {draft_id: randomUUID(), confirmed: true};
    expect((await request(path, {body, key: randomUUID()})).status).toBe(401);
    expect((await request(path, {login: user, body, csrf: other.csrf, key: randomUUID()})).status).toBe(403);
    expect((await request(path, {login: user, body: {...body, confirmed: false}, key: randomUUID()})).status).toBe(422);
    const key = randomUUID();
    await request(path, {login: user, body, key});
    expect(calls.at(-1)).toMatchObject({path: "/internal/v1" + path, owner: user.id, body, key});
  });

  it("validates prediction pagination and binds it to authenticated ownership", async () => {
    const cursor = randomUUID();
    const path = "/predictions?cursor=" + cursor + "&limit=2&status=SETTLED";
    expect((await request(path)).status).toBe(401);
    await request(path, {login: user});
    expect(calls.at(-1)).toMatchObject({path: "/internal/v1" + path, owner: user.id});
    for (const query of ["limit=101", "limit=0", "status=bad", "cursor=bad", "data_mode=LIVE_RESEARCH"]) {
      expect((await request("/predictions?" + query, {login: user})).status).toBe(422);
    }
  });

  it("report access binds owner and mode and protects creation with CSRF", async () => {
    const body = {base_from: "2024-11-22", base_to: "2024-11-22", cutoff: "2024-12-03T00:00:00Z"};
    expect((await request("/evaluation-reports", {body})).status).toBe(401);
    expect((await request("/evaluation-reports", {login: user, body, csrf: other.csrf})).status).toBe(403);
    for (const invalid of [{...body, owner_id: other.id}, {...body, data_mode: "LIVE_RESEARCH"}, {...body, base_to: "2024-11-21"}, {...body, prediction_ids: []}]) {
      expect((await request("/evaluation-reports", {login: user, body: invalid})).status).toBe(422);
    }
    expect((await request("/evaluation-reports", {login: user, body})).status).toBe(200);
    expect(calls.at(-1)).toMatchObject({path: "/internal/v1/evaluation-reports", owner: user.id, body: {...body, data_mode: "REPLAY"}});
    expect((await request("/evaluation-reports")).status).toBe(401);
    const cursor = randomUUID();
    expect((await request("/evaluation-reports?limit=2&cursor=" + cursor, {login: user})).status).toBe(200);
    expect(calls.at(-1)).toMatchObject({path: "/internal/v1/evaluation-reports?cursor=" + cursor + "&limit=2", owner: user.id});
    for (const query of ["limit=0", "limit=101", "limit=1.5", "cursor=invalid", "data_mode=LIVE_RESEARCH", "owner_id=" + other.id]) {
      expect((await request("/evaluation-reports?" + query, {login: user})).status).toBe(422);
    }
    const path = "/evaluation-reports/" + randomUUID();
    expect((await request(path)).status).toBe(401);
    expect((await request(path, {login: other})).status).toBe(200);
    expect(calls.at(-1)).toMatchObject({path: "/internal/v1" + path, owner: other.id});
    expect((await request("/evaluation-reports/invalid", {login: user})).status).toBe(422);
  });

  it("normalizes authenticated stock paths and rejects malformed or unsupported symbols before Alpha", async () => {
    for (const suffix of ["bars", "indicators"]) {
      const accepted = await request(`/stocks/%20nvDa%20/${suffix}`, {login: user});
      expect(accepted.status).toBe(200); await accepted.json();
      const expected = suffix === "bars" ? "/internal/v1/market/snapshot" : "/internal/v1/market/indicators";
      expect(calls.at(-1)?.path).toBe(expected);
      for (const value of ["AAPL", "ＮＶＤＡ", "NV DA", "NVDA/..", "\u000bnvda"]) {
        const count = calls.length;
        const denied = await request(`/stocks/${encodeURIComponent(value)}/${suffix}`, {login: user});
        expect(denied.status).toBe(422); await denied.json();
        expect(calls.length).toBe(count);
      }
      const anonymous = await request(`/stocks/nvda/${suffix}`);
      expect(anonymous.status).toBe(401); await anonymous.json();
    }
  });

  it("readiness reflects Alpha failure while liveness stays up and never leaks config", async () => {
    const ready = await request("/ready");
    expect(ready.status).toBe(200);
    expect((await ready.json()).checks.database).toBe("ready");
    alphaReady = false;
    try {
      const failed = await request("/ready");
      expect(failed.status).toBe(503);
      expect((await failed.json()).checks.alpha).toBe("not_ready");
      expect((await request("/health")).status).toBe(200);
    } finally { alphaReady = true; }
    const broken = await createBff({log: entry => observations.push(entry),config: readConfig({YTA_ENV: "test", YTA_ALPHA_URL: alphaOrigin, YTA_ALPHA_TOKEN: token, YTA_DATABASE_URL: "postgresql://yta_app:secret-marker@127.0.0.1:1/yta_test"}), startRunner: false});
    try {
      await broken.app.listen(0, "127.0.0.1");
      const failed = await fetch(`${await broken.app.getUrl()}/api/yta/v1/ready`);
      expect(failed.status).toBe(503);
      const text = await failed.text();
      expect(JSON.parse(text).checks.database).toBe("unavailable");
      expect(text).not.toContain("secret-marker"); expect(text).not.toContain(token);
    } finally { await broken.close(); }
  });

  it("deduplicates session creation and messages across concurrent requests", async () => {
    const key = randomUUID();
    const sessions = await Promise.all(Array.from({ length: 4 }, async () => (await (await request("/sessions", { login: user, body: {}, key })).json()).session_id));
    expect(new Set(sessions).size).toBe(1);
    const sessionId = sessions[0];
    const requestId = randomUUID();
    const submitted = await Promise.all(Array.from({ length: 4 }, async () => {
      const response = await request(`/sessions/${sessionId}/messages`, { login: user, body: { text: "研究 NVDA", request_id: requestId } });
      expect(response.status).toBe(202); return (await response.json()).run_id as string;
    }));
    expect(new Set(submitted).size).toBe(1);
    const runId = submitted[0]!;
    const result = await completed(runId);
    expect(result.status).toBe("COMPLETED");
    expect(result.report).toMatchObject({ status: "DRAFT_READY", draft_id: result.draft.draft_id, data_mode: "REPLAY", as_of: forecast.as_of });
    expect(result.report.evidence).toHaveLength(2);
    const recovered = await new ResearchRepository(server.pool, "REPLAY").runSummary(user.id, runId);
    expect(recovered.report).toEqual(result.report);
    expect(calls.filter(c => c.path === "/internal/v1/prediction-drafts" && (c.body as {request_id: string}).request_id === runId)).toHaveLength(1);
    const conflict = await request(`/sessions/${sessionId}/messages`, { login: user, body: { text: "changed", request_id: requestId } });
    expect(conflict.status).toBe(409);
    const records = await (await request(`/sessions/${sessionId}/messages`, { login: user })).json();
    expect(records.items.map((m: {role: string}) => m.role)).toEqual(["user", "assistant"]);
    expect(records.items[1].content).toContain("不是真实行情研究");
    expect((await request(`/runs/${runId}`, { login: other })).status).toBe(404);
    expect((await request(`/sessions/${sessionId}/messages`, { login: other })).status).toBe(404);

    const stream = await request(`/runs/${runId}/events`, { login: user });
    expect(stream.headers.get("content-type")).toContain("text/event-stream");
    const text = await stream.text();
    expect(text).not.toContain(token);
    const events = text.split("\n").filter(line => line.startsWith("data: ")).map(line => JSON.parse(line.slice(6)));
    expect(events.at(-1).type).toBe("run.completed");
    expect(events.find(e => e.type === "message.completed").payload.report).toEqual(result.report);
    const toolIds = events.filter(e => e.type === "tool.completed" && e.payload.success).map(e => e.payload.tool_call_id);
    expect(result.report.evidence.every((item: {tool_call_id: string}) => toolIds.includes(item.tool_call_id))).toBe(true);
    const middle = events[2];
    const replay = await (await request(`/runs/${runId}/events?cursor=${middle.event_id}`, { login: user })).text();
    const replayed = replay.split("\n").filter(line => line.startsWith("data: ")).map(line => JSON.parse(line.slice(6)));
    expect(replayed.map(e => e.event_id)).toEqual(events.slice(3).map(e => e.event_id));
    const headerReplay = await fetch(`${endpoint}/api/yta/v1/runs/${runId}/events?cursor=${events[0].event_id}`,
      {headers: {Cookie: user.cookie, "Last-Event-ID": middle.event_id}});
    const headerEvents = (await headerReplay.text()).split("\n").filter(line => line.startsWith("data: ")).map(line => JSON.parse(line.slice(6)));
    expect(headerEvents.map(e => e.event_id)).toEqual(events.slice(3).map(e => e.event_id));
    expect((await request(`/runs/${runId}/events?cursor=${randomUUID()}`, { login: user })).status).toBe(409);
  });

  it("forwards only authenticated ownership and explicit confirmation to Alpha", async () => {
    const draftId = randomUUID(); const key = randomUUID();
    const before = calls.length;
    const forged = await request("/predictions", { login: user, key, body: { draft_id: draftId, confirmed: true, owner_id: other.id, expected_return: "1" } });
    expect(forged.status).toBe(422);
    const unconfirmed = await request("/predictions", { login: user, key, body: { draft_id: draftId, confirmed: false } });
    expect(unconfirmed.status).toBe(422);
    expect(calls.length).toBe(before);
    const response = await request("/predictions", { login: user, key, body: { draft_id: draftId, confirmed: true } });
    expect(response.status).toBe(200);
    expect(calls.at(-1)?.owner).toBe(user.id);
    expect(calls.at(-1)?.key).toBe(key);
    expect(calls.at(-1)?.body).toEqual({ draft_id: draftId, confirmed: true });
  });

  it("revokes login immediately and invalidates sessions after credential rotation", async () => {
    const temporary = await login(emailB);
    const response = await request("/auth/logout", { login: temporary, body: {} });
    expect(response.status).toBe(200);
    expect((await request("/me", { login: temporary })).status).toBe(401);
    await server.pool.query("INSERT INTO yta.login_credentials(owner_id,password_hash) VALUES ($1,$2)", [ownerB, await hashPassword(password)]);
    expect((await request("/me", { login: other })).status).toBe(401);
  });

  it("cancels active runs and marks abandoned admission unknown without re-execution", async () => {
    await server.runner.close();
    let executions = 0;
    const driver: ResearchDriver = { async execute(_run, emit, signal) { executions++; await emit({ type: "message.delta", payload: { text: "partial" } }); await delay(5000, undefined, { signal }); } };
    const isolated = await createBff({log: entry => observations.push(entry), config: readConfig({ YTA_ENV: "test", YTA_DATABASE_URL: url!, YTA_ALPHA_URL: alphaOrigin, YTA_ALPHA_TOKEN: token }), driver, startRunner: false });
    // Use repository operations to isolate restart semantics from the live worker's queue poll.
    const session = await isolated.research.createSession(ownerA, "REPLAY", randomUUID());
    const submitted = await isolated.research.submit(ownerA, session.session_id, randomUUID(), "cancellation case", "REPLAY");
    const run = await isolated.research.getRun(ownerA, submitted.run_id);
    isolated.runner.launch(run);
    for (let n=0; n<100 && executions===0; n++) await delay(10);
    await isolated.research.cancel(ownerA, run.id);
    for (let n=0; n<100; n++) {
      if ((await isolated.research.runSummary(ownerA, run.id)).status === "CANCELED") break;
      await delay(20);
    }
    expect((await isolated.research.runSummary(ownerA, run.id)).status).toBe("CANCELED");
    const second = await isolated.research.submit(ownerA, session.session_id, randomUUID(), "abandoned", "REPLAY");
    const abandoned = await isolated.research.getRun(ownerA, second.run_id);
    await isolated.research.append(abandoned, { type: "run.started", payload: {} });
    isolated.runner.launch(abandoned);
    for (let n=0; n<100; n++) {
      if ((await isolated.research.runSummary(ownerA, abandoned.id)).status === "EXECUTION_UNKNOWN") break;
      await delay(20);
    }
    expect((await isolated.research.runSummary(ownerA, abandoned.id)).status).toBe("EXECUTION_UNKNOWN");
    expect(executions).toBe(1);
    await expect(isolated.research.submit(ownerA, session.session_id, randomUUID(), "unsafe resend", "REPLAY")).rejects.toMatchObject({ code: "SESSION_BUSY" });
    await isolated.close();
  });
  it("constructs the configured Hermes driver without injection and keeps credentials private",async()=>{
    const probe=await createBff({config:{...server.config,YTA_RESEARCH_DRIVER:"HERMES",
      YTA_HERMES_ORIGIN:"http://127.0.0.1:1",YTA_HERMES_TOKEN:"private-hermes-configuration-marker-123456789",YTA_HERMES_MODEL:"test-model"},
      pool:server.pool,startRunner:false,log:()=>{}});
    try{
      await probe.app.listen(0,"127.0.0.1");
      const response=await fetch((await probe.app.getUrl())+"/api/yta/v1/me",{headers:{Cookie:user.cookie}});
      expect(response.status).toBe(200);
      const value=await response.json();
      expect(value).toMatchObject({runtime:"hermes",data_mode:"REPLAY"});
      expect(JSON.stringify(value)).not.toContain("private-hermes");
    }finally{await probe.close();}
  });

});
