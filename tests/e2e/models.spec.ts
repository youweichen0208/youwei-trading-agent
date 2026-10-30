import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { test, expect, type Page, type BrowserContext } from "@playwright/test";

const fixture = JSON.parse(readFileSync("packages/contracts/fixtures/experimental-model-page.json", "utf8"));
const first = fixture.items[0];
const second = {...first, id: "lightgbm-experimental-" + "b".repeat(64), registration_sha256: "b".repeat(64)};
let cookies: Awaited<ReturnType<BrowserContext["cookies"]>> = [];
async function login(page: Page, enabled = true) {
  if (cookies.length) await page.context().addCookies(cookies);
  else {
  await page.goto("/login");
  await page.getByLabel("邮箱").fill(process.env.YTA_E2E_EMAIL!);
  await page.getByLabel("密码").fill(process.env.YTA_E2E_PASSWORD!);
  await page.getByRole("button", {name: "进入研究工作台"}).click();
  await expect(page).not.toHaveURL(/\/login$/);
  cookies = await page.context().cookies();
  }
  if (enabled) await page.route("**/api/yta/v1/me", async route => {
    const response = await route.fetch();
    expect(response.status()).toBe(200);
    await route.fulfill({json: {...await response.json(), experimental_models_enabled: true}});
  });
}

test("model catalog is hidden by default and direct navigation explains availability", async ({page}) => {
  await login(page, false);
  await page.goto("/models");
  await expect(page.getByRole("heading", {name: "实验模型目录尚未启用"})).toBeVisible();
  await expect(page.getByRole("navigation").getByRole("link", {name: "实验模型"})).toHaveCount(0);
});

test("model catalog paginates, filters and preserves full model identity on desktop and mobile", async ({page}) => {
  await login(page);
  const queries: URL[] = [];
  await page.route("**/api/yta/v1/experimental-models?*", async route => {
    const url = new URL(route.request().url()); queries.push(url);
    const horizon = url.searchParams.get("horizon_sessions");
    const result = horizon === "20" ? {items: [], next_cursor: null}
      : url.searchParams.has("cursor") ? {items: [second], next_cursor: null}
      : {items: [first], next_cursor: first.id};
    await route.fulfill({json: result});
  });
  await page.setViewportSize({width: 1440, height: 1000});
  await page.goto("/models");
  await expect(page.getByRole("article")).toHaveCount(1);
  await expect(page.getByText("保留窗口误差 ≠ 最终评测成绩")).toBeVisible();
  await page.getByText("核对完整版本与时间").click();
  await expect(page.getByText(first.id, {exact:true})).toBeVisible();
  await expect(page.getByText(first.reserved_window_mae, {exact:true})).toBeVisible();
  await page.screenshot({path: "test-results/models-desktop.png", fullPage: true});
  await page.getByRole("button", {name: "下一页"}).click();
  await expect(page.getByText("第 2 页")).toBeVisible();
  await expect(page.getByRole("article")).toContainText("bbbbbbbbbbbb");
  expect(queries.at(-1)?.searchParams.get("cursor")).toBe(first.id);
  await page.getByLabel("预测跨度").selectOption("20");
  await expect(page.getByRole("heading", {name: "暂无匹配的实验模型"})).toBeVisible();
  expect(queries.at(-1)?.searchParams.has("cursor")).toBe(false);
  await page.getByRole("button", {name: "查看全部跨度"}).click();
  await expect(page.getByRole("article")).toHaveCount(1);
  await page.setViewportSize({width: 390, height: 844});
  await page.getByText("核对完整版本与时间").click();
  await expect(page.getByText(first.id, {exact:true})).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path: "test-results/models-mobile.png", fullPage: true});
});

test("model catalog refuses wrong mode and supports retry without fabricating metrics", async ({page}) => {
  await login(page);
  let invalid = true;
  await page.route("**/api/yta/v1/experimental-models?*", route => route.fulfill({json: {
    items: [{...first, data_mode: invalid ? "LIVE_RESEARCH" : "REPLAY"}], next_cursor: null,
  }}));
  await page.goto("/models");
  await expect(page.getByRole("main").getByRole("alert")).toContainText("模型目录暂时无法读取");
  await expect(page.getByRole("article")).toHaveCount(0);
  invalid = false;
  await page.getByRole("button", {name: "重新加载"}).click();
  await expect(page.getByRole("article")).toHaveCount(1);
});

test("a late result from the previous horizon cannot replace the current filter", async ({page}) => {
  await login(page);
  let release!: () => void;
  const wait = new Promise<void>(resolve => {release = resolve;});
  let requested!: () => void;
  const started = new Promise<void>(resolve => {requested = resolve;});
  await page.route("**/api/yta/v1/experimental-models?*", async route => {
    const horizon = new URL(route.request().url()).searchParams.get("horizon_sessions");
    if (!horizon) {requested(); await wait;}
    await route.fulfill({json: horizon ? {items: [], next_cursor: null} : fixture}).catch(() => {});
  });
  await page.goto("/models");
  await started;
  await page.getByLabel("预测跨度").selectOption("20");
  await expect(page.getByRole("heading", {name: "暂无匹配的实验模型"})).toBeVisible();
  release();
  await expect(page.getByRole("article")).toHaveCount(0);
  await expect(page.getByLabel("预测跨度")).toHaveValue("20");
});

test("registered report list opens exact comparison and sample evidence on desktop and mobile", async ({page}) => {
  await login(page);
  const report = JSON.parse(readFileSync("packages/contracts/fixtures/experimental-price-report.json", "utf8"));
  const list = JSON.parse(readFileSync("packages/contracts/fixtures/experimental-evaluation-page.json", "utf8"));
  await page.route("**/api/yta/v1/experimental-models/*/evaluations?*", route => route.fulfill({json: list}));
  await page.route("**/api/yta/v1/experimental-evaluations/*", route => route.fulfill({json: report}));
  await page.setViewportSize({width: 1440, height: 1000});
  await page.goto(`/models/${report.model_id}/evaluations`);
  await expect(page.getByRole("heading", {name: "模型评测记录"})).toBeVisible();
  await page.getByRole("link", {name: /查看评测 /}).first().click();
  await expect(page.getByRole("region", {name: "同样本指标比较"})).toBeVisible();
  const metrics = page.getByRole("region", {name: "同样本指标比较"});
  await expect(metrics.locator("tbody tr")).toHaveCount(3);
  await expect(metrics).toContainText(report.metrics.model.mae);
  await expect(page.getByRole("region", {name: "逐样本评测结果"}).locator("tbody tr")).toHaveCount(report.manifest.length);
  await page.getByText("核对逐样本证据与计算源码").click();
  await expect(page.locator(".report-manifest")).toContainText(report.manifest[0].observation_snapshot_hash);
  await page.screenshot({path: "test-results/model-report-desktop.png", fullPage: true});
  await page.setViewportSize({width: 390, height: 844});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(metrics).toBeVisible();
  await page.screenshot({path: "test-results/model-report-mobile.png", fullPage: true});
});

test("report pages retry failures and reject a report from another identity or mode", async ({page}) => {
  await login(page);
  const report = JSON.parse(readFileSync("packages/contracts/fixtures/experimental-price-report.json", "utf8"));
  const id = "model-evaluation-experimental-" + report.content_hash;
  let state = "failed";
  await page.route("**/api/yta/v1/experimental-evaluations/*", route => state === "failed"
    ? route.fulfill({status:503,json:{error:{code:"ALPHA_UNAVAILABLE",message:"unavailable"}}})
    : route.fulfill({json: state === "wrong" ? {...report,cohort:{...report.cohort,data_mode:"LIVE_RESEARCH"}} : report}));
  await page.goto("/model-evaluations/" + id);
  await expect(page.getByRole("main").getByRole("alert")).toContainText("暂时无法读取");
  state = "wrong";
  await page.getByRole("button", {name:"重新加载"}).click();
  await expect(page.getByRole("main").getByRole("alert")).toBeVisible();
  await expect(page.getByRole("region", {name:"同样本指标比较"})).toHaveCount(0);
  state = "valid";
  await page.getByRole("button", {name:"重新加载"}).click();
  await expect(page.getByRole("region", {name:"同样本指标比较"})).toBeVisible();
  await page.goto("/model-evaluations/model-evaluation-experimental-" + "0".repeat(64));
  await expect(page.getByRole("main").getByRole("alert")).toBeVisible();
  await expect(page.getByRole("region", {name:"同样本指标比较"})).toHaveCount(0);
});

const comparisonFixture = JSON.parse(readFileSync("packages/contracts/fixtures/experimental-report-comparison.json", "utf8"));
async function chooseComparison(page: Page) {
  await page.route("**/api/yta/v1/experimental-models/*/evaluations?*", route => {
    const report = comparisonFixture.reports.find((row: {model_id: string}) => route.request().url().includes(row.model_id));
    return route.fulfill({json:{items:[{
      id:"model-evaluation-experimental-" + report.content_hash, model_id:report.model_id,
      content_hash:report.content_hash,cohort_hash:report.cohort_hash,data_mode:report.cohort.data_mode,
      horizon_sessions:report.cohort.horizon_sessions,registered_at:"2026-10-29T00:00:00+00:00",
      cutoff:report.cohort.cutoff,sample_count:report.manifest.length,qualification:"UNVERIFIED",
    }],next_cursor:null}});
  });
  for (const report of comparisonFixture.reports) {
    await page.goto(`/models/${report.model_id}/evaluations`);
    await page.getByRole("button", {name:"加入比较"}).click();
    await expect(page.getByRole("button", {name:"移出比较"})).toHaveAttribute("aria-pressed", "true");
  }
  await page.getByRole("link", {name:"比较已选报告（2） →"}).click();
  await expect(page).toHaveURL(/\/models\/compare$/);
  await expect(page.getByRole("heading", {name:"实验报告比较", exact:true})).toBeVisible();
}

test("cross-model selection persists and shows exact comparison on desktop and phone", async ({page}) => {
  await login(page);
  let submitted: unknown;
  await page.route("**/api/yta/v1/experimental-evaluations/compare", route => {
    submitted=route.request().postDataJSON();
    expect(route.request().headers()["x-csrf-token"]).toBeTruthy();
    return route.fulfill({json:comparisonFixture});
  });
  await chooseComparison(page);
  await page.reload();
  await expect(page.getByRole("heading", {name:"已选报告 · 2 / 10"})).toBeVisible();
  await page.getByRole("button", {name:"比较所选报告"}).click();
  const results=page.getByRole("region", {name:"多报告指标比较"});
  await expect(results.locator("tbody tr")).toHaveCount(6);
  expect(submitted).toEqual({evaluation_ids:comparisonFixture.evaluation_ids});
  await expect(results).toContainText(comparisonFixture.reports[0].metrics.model.mae);
  await page.getByText("核对共同样本与完整模型身份").click();
  await expect(page.getByText("样本集合：" + comparisonFixture.cohort_hash)).toBeVisible();
  await page.screenshot({path:"test-results/model-comparison-desktop.png",fullPage:true});
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path:"test-results/model-comparison-mobile.png",fullPage:true});
  await page.getByRole("button", {name:"移除报告 1"}).click();
  await expect(results).toHaveCount(0);
  await expect(page.getByRole("button", {name:"比较所选报告"})).toBeDisabled();
});

test("comparison preserves selection after mismatch and rejects reordered or foreign reports", async ({page}) => {
  await login(page);
  let state="mismatch";
  await page.route("**/api/yta/v1/experimental-evaluations/compare", route => {
    if(state==="mismatch") return route.fulfill({status:422,json:{error:{code:"EVALUATION_COHORT_MISMATCH"}}});
    const value = state === "reordered" ? {...comparisonFixture,evaluation_ids:[...comparisonFixture.evaluation_ids].reverse(),reports:[...comparisonFixture.reports].reverse()}
      : state === "foreign" ? {...comparisonFixture,reports:comparisonFixture.reports.map((report: {cohort: object}) => ({...report,cohort:{...report.cohort,data_mode:"LIVE_RESEARCH"}}))} : comparisonFixture;
    return route.fulfill({json:value});
  });
  await chooseComparison(page);
  await page.getByRole("button", {name:"比较所选报告"}).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("样本或信息截点不同");
  for (const next of ["reordered", "foreign", "valid"]) {
    state=next;
    await page.getByRole("button", {name:"比较所选报告"}).click();
    if (state === "valid") await expect(page.getByRole("region", {name:"多报告指标比较"})).toBeVisible();
    else {
      await expect(page.getByRole("main").getByRole("alert")).toContainText("暂时无法比较");
      await expect(page.getByRole("region", {name:"多报告指标比较"})).toHaveCount(0);
    }
    await expect(page.getByRole("heading", {name:"已选报告 · 2 / 10"})).toBeVisible();
  }
});

test("disabled comparison page does not request experimental reports", async ({page}) => {
  await login(page, false);
  const requests: string[]=[];
  page.on("request", request => {if(request.url().includes("/experimental-evaluations")) requests.push(request.url());});
  await page.goto("/models/compare");
  await expect(page.getByRole("heading", {name:"实验评测尚未启用"})).toBeVisible();
  expect(requests).toEqual([]);
});


test("comparison selection stays scoped to the authenticated account and mode", async ({page}) => {
  await login(page);
  await chooseComparison(page);
  let mode = "LIVE_RESEARCH", otherAccount = false;
  await page.route("**/api/yta/v1/me", async route => {
    const me = await (await route.fetch()).json();
    await route.fulfill({json: {...me, experimental_models_enabled:true, data_mode:mode,
      user: {...me.user,id:otherAccount ? "00000000-0000-4000-8000-000000000001" : me.user.id}}});
  });
  await page.reload();
  await expect(page.getByRole("heading", {name:"已选报告 · 0 / 10"})).toBeVisible();
  mode="REPLAY";otherAccount=true;
  await page.reload();
  await expect(page.getByRole("heading", {name:"已选报告 · 0 / 10"})).toBeVisible();
  otherAccount=false;
  await page.reload();
  await expect(page.getByRole("heading", {name:"已选报告 · 2 / 10"})).toBeVisible();
});


const proposalFixture=JSON.parse(readFileSync("packages/contracts/fixtures/model-proposal.json","utf8"));
test("proposal history survives storage loss, paginates and opens detail on mobile", async ({page}) => {
  await login(page);
  await page.goto("/models");
  const owner=await page.evaluate(async () => (await (await fetch("/api/yta/v1/me")).json()).user.id as string);
  const saved=proposalResult(owner,proposalFixture.proposal.request_id,{rationale:"核对历史提案 🧪"});
  const older={...saved,id:"00000000-0000-4000-8000-000000000002"};
  const queries: URL[]=[];
  await page.route("**/api/yta/v1/model-proposals?*", route => {
    const url=new URL(route.request().url());queries.push(url);
    return route.fulfill({json:url.searchParams.has("cursor") ? {items:[older],next_cursor:null} : {items:[saved],next_cursor:saved.id}});
  });
  await page.route("**/api/yta/v1/model-proposals/"+saved.id,route=>route.fulfill({json:saved}));
  await page.getByRole("link",{name:"我的模型提案 →"}).click();
  await expect(page.getByRole("article")).toHaveCount(1);
  await page.evaluate(() => sessionStorage.clear());
  await page.reload();
  await expect(page.getByRole("article")).toContainText("核对历史提案 🧪");
  await page.screenshot({path:"test-results/proposal-history-desktop.png",fullPage:true});
  await page.getByRole("button",{name:"下一页"}).click();
  await expect(page.getByRole("link",{name:"查看提案 00000000 →"})).toBeVisible();
  expect(queries.at(-1)?.searchParams.get("cursor")).toBe(saved.id);
  await expect(page.getByRole("button",{name:"下一页"})).toBeDisabled();
  await page.getByRole("button",{name:"上一页"}).click();
  await expect(page.getByRole("link",{name:`查看提案 ${saved.id.slice(0,8)} →`})).toBeVisible();
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path:"test-results/proposal-history-mobile.png",fullPage:true});
  await page.getByRole("link",{name:`查看提案 ${saved.id.slice(0,8)} →`}).click();
  await expect(page.getByRole("heading",{name:"模型提案详情"})).toBeVisible();
  await expect(page.getByText("核对历史提案 🧪",{exact:true})).toBeVisible();
});

test("proposal history rejects foreign scope and stale pages, retries and shows empty state", async ({page}) => {
  await login(page);
  await page.goto("/models");
  const owner=await page.evaluate(async () => (await (await fetch("/api/yta/v1/me")).json()).user.id as string);
  const saved=proposalResult(owner,proposalFixture.proposal.request_id,{rationale:"自己的提案"});
  let mode="foreign";
  await page.route("**/api/yta/v1/model-proposals?*",route=>{
    const item=mode==="foreign" ? proposalFixture : mode==="mode" ? {...saved,proposal:{...saved.proposal,data_mode:"LIVE_RESEARCH"}} : saved;
    return route.fulfill({json:mode==="empty" ? {items:[],next_cursor:null} : {items:[item],next_cursor:item.id}});
  });
  await page.goto("/model-proposals");
  await expect(page.getByRole("main").getByRole("alert")).toBeVisible();
  await expect(page.getByRole("article")).toHaveCount(0);
  mode="mode";
  await page.getByRole("button",{name:"重新加载"}).click();
  await expect(page.getByRole("main").getByRole("alert")).toBeVisible();
  mode="valid";
  await page.getByRole("button",{name:"重新加载"}).click();
  await expect(page.getByRole("article")).toContainText("自己的提案");
  await page.getByRole("button",{name:"下一页"}).click();
  await expect(page.getByRole("main").getByRole("alert")).toBeVisible();
  await expect(page.getByRole("article")).toHaveCount(0);
  mode="empty";
  await page.getByRole("button",{name:"重新加载"}).click();
  await expect(page.getByRole("heading",{name:"暂无已提交提案"})).toBeVisible();
});

test("disabled proposal history makes no history request", async ({page}) => {
  await login(page,false);
  let calls=0;
  await page.route("**/api/yta/v1/model-proposals?*",route=>{calls++;return route.abort();});
  await page.goto("/model-proposals");
  await expect(page.getByRole("heading",{name:"实验模型提案尚未启用"})).toBeVisible();
  expect(calls).toBe(0);
});

async function proposalForm(page: Page) {
  await page.route("**/api/yta/v1/experimental-evaluations/*",route => {
    const report=comparisonFixture.reports.find((r: {content_hash:string}) => route.request().url().endsWith(r.content_hash));
    return route.fulfill({json:report});
  });
  await page.goto(`/models/propose/${comparisonFixture.evaluation_ids[0]}/${comparisonFixture.evaluation_ids[1]}`);
  await expect(page.getByLabel("提出理由")).toBeVisible();
  return page.evaluate(async () => (await (await fetch("/api/yta/v1/me")).json()).user.id as string);
}
function proposalResult(owner: string,key: string,body: Record<string,unknown>) {
  const p={...proposalFixture.proposal,...body,owner_id:owner,request_id:key};
  return {...proposalFixture,proposal:p,content_hash:createHash("sha256").update(JSON.stringify(p,Object.keys(p).sort())).digest("hex")};
}

test("lost proposal response survives refresh and retries one exact identity before opening detail",async ({page})=>{
  await login(page);
  let saved=proposalFixture;
  const attempts: {key:string;body:Record<string,unknown>}[]=[];
  let owner="";
  await page.route("**/api/yta/v1/model-proposals",route=>{
    const body=route.request().postDataJSON(),key=route.request().headers()["idempotency-key"]!;
    attempts.push({key,body});saved=proposalResult(owner,key,body);
    if(attempts.length===1) return route.abort("failed");
    return route.fulfill({json:saved});
  });
  await page.route("**/api/yta/v1/model-proposals/*",route=>route.fulfill({json:saved}));
  owner=await proposalForm(page);
  await page.getByLabel("提出理由").fill("候选证据复核 🧪，请保留精确版本。");
  await page.getByRole("button",{name:"提交候选提案",exact:true}).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("无法确认提交结果");
  await page.reload();
  await expect(page.getByLabel("提出理由")).toHaveValue("候选证据复核 🧪，请保留精确版本。");
  await expect(page.getByLabel("提出理由")).toBeDisabled();
  expect(attempts).toHaveLength(1);
  await page.getByRole("button",{name:"重试原提案",exact:true}).click();
  await expect(page.getByRole("heading",{name:"提案已提交"})).toBeVisible();
  expect(attempts).toHaveLength(2);expect(attempts[1]).toEqual(attempts[0]);
  await page.getByRole("link",{name:"打开提案详情 →"}).click();
  await expect(page).toHaveURL(/\/model-proposals\//);
  await expect(page.getByRole("heading",{name:"模型提案详情"})).toBeVisible();
  await expect(page.getByText("SUBMITTED · 未验收",{exact:true})).toBeVisible();
  await page.screenshot({path:"test-results/model-proposal-desktop.png",fullPage:true});
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:"test-results/model-proposal-mobile.png",fullPage:true});
});

test("paused or mismatched proposal responses retain the original request and never show false success",async ({page})=>{
  await login(page);
  let owner="",state="paused",saved=proposalFixture;
  const attempts: {key:string;body:Record<string,unknown>}[]=[];
  await page.route("**/api/yta/v1/model-proposals",route=>{
    const body=route.request().postDataJSON(),key=route.request().headers()["idempotency-key"]!;
    attempts.push({key,body});saved=proposalResult(owner,key,body);
    if(state==="paused") return route.fulfill({status:503,json:{error:{code:"WRITE_ADMISSION_PAUSED"}}});
    return route.fulfill({json:state==="invalid" ? proposalResult(owner,key,{...body,rationale:"Different"}) : saved});
  });
  owner=await proposalForm(page);
  await page.getByLabel("提出理由").fill("固定理由");
  await page.getByRole("button",{name:"提交候选提案",exact:true}).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("提交入口暂时关闭");
  state="invalid";
  await page.getByRole("button",{name:"重试原提案",exact:true}).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("无法确认提交结果");
  await expect(page.getByRole("heading",{name:"提案已提交"})).toHaveCount(0);
  state="valid";
  await page.getByRole("button",{name:"重试原提案",exact:true}).click();
  await expect(page.getByRole("heading",{name:"提案已提交"})).toBeVisible();
  expect(attempts).toHaveLength(3);expect(attempts.every(item=>JSON.stringify(item)===JSON.stringify(attempts[0]))).toBe(true);
  await page.route("**/api/yta/v1/model-proposals/*",route=>route.fulfill({json:{...saved,proposal:{...saved.proposal,owner_id:"00000000-0000-4000-8000-000000000001"}}}));
  await page.goto("/model-proposals/"+saved.id);
  await expect(page.getByRole("main").getByRole("alert")).toContainText("无法核对提案或报告");
  await expect(page.getByRole("heading",{name:"提案已提交"})).toHaveCount(0);
});

test("disabled proposal pages never dispatch a proposal or read experimental evidence",async ({page})=>{
  await login(page,false);
  const requests: string[]=[];
  page.on("request",r=>{if(r.url().includes("/api/yta/v1/model-proposals") || r.url().includes("/api/yta/v1/experimental-evaluations")) requests.push(r.url());});
  await page.goto(`/models/propose/${comparisonFixture.evaluation_ids[0]}/baseline`);
  await expect(page.getByRole("heading",{name:"实验模型提案尚未启用"})).toBeVisible();
  await page.goto("/model-proposals/"+proposalFixture.id);
  await expect(page.getByRole("heading",{name:"实验模型提案尚未启用"})).toBeVisible();
  expect(requests).toEqual([]);
});

test("proposal storage failure prevents dispatch before a retry identity can be saved",async ({page})=>{
  await login(page);
  await proposalForm(page);
  let dispatched=0;
  await page.route("**/api/yta/v1/model-proposals",route=>{dispatched++;return route.abort();});
  await page.getByLabel("提出理由").fill("不能丢失提交身份");
  await page.evaluate(()=>{Storage.prototype.setItem=()=>{throw new Error("Storage unavailable");};});
  await page.getByRole("button",{name:"提交候选提案",exact:true}).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("允许浏览器保存本次请求");
  expect(dispatched).toBe(0);
  await expect(page.getByLabel("提出理由")).toBeEnabled();
});

const qualityFixture=JSON.parse(readFileSync("packages/contracts/fixtures/quality-assessment.json","utf8"));
const qualityReports=JSON.parse(readFileSync("packages/contracts/fixtures/quality-source-reports.json","utf8"));
const qualityRule={id:"historical-quality-policy-"+qualityFixture.policy_hash,content_hash:qualityFixture.policy_hash,policy:qualityFixture.policy};
const qualityPath=`/models/quality/${qualityFixture.candidate_evaluation_id}/${qualityFixture.reference_evaluation_id ?? "baseline"}`;
async function qualitySources(page: Page) {
  await page.route("**/api/yta/v1/experimental-evaluations/*",route=>route.fulfill({json:qualityReports.find((report:{content_hash:string})=>route.request().url().endsWith(report.content_hash))}));
}
test("quality rules paginate and explicit lost-response retry preserves the selected evidence",async({page})=>{
  await login(page);await qualitySources(page);
  const olderPolicy={...qualityRule.policy,policy_version:"older-test-rule"};
  const olderHash=createHash("sha256").update(JSON.stringify(olderPolicy,Object.keys(olderPolicy).sort())).digest("hex");
  const older={policy:olderPolicy,content_hash:olderHash,id:"historical-quality-policy-"+olderHash};
  const queries: URL[]=[];
  await page.route("**/api/yta/v1/historical-quality-policies?*",route=>{
    const url=new URL(route.request().url());queries.push(url);
    return route.fulfill({json:url.searchParams.has("cursor") ? {items:[older],next_cursor:null} : {items:[qualityRule],next_cursor:qualityRule.id}});
  });
  const requests:unknown[]=[];
  await page.route("**/api/yta/v1/historical-quality-assessments",route=>{
    requests.push(route.request().postDataJSON());
    return requests.length===1 ? route.abort("failed") : route.fulfill({json:qualityFixture});
  });
  await page.route("**/api/yta/v1/historical-quality-assessments/*",route=>route.fulfill({json:qualityFixture}));
  await page.goto(qualityPath);
  await expect(page.getByRole("radio")).toHaveCount(1);
  await expect(page.getByRole("button",{name:"按所选规则核验"})).toBeDisabled();
  expect(queries[0]?.searchParams.get("horizon_sessions")).toBe(String(qualityFixture.policy.horizon_sessions));
  await page.getByRole("button",{name:"下一页"}).click();
  await expect(page.getByRole("radio")).toHaveAccessibleName(/older-test-rule/);
  await page.getByRole("button",{name:"上一页"}).click();
  await expect(page.getByRole("radio")).toHaveAccessibleName(new RegExp(qualityRule.policy.policy_version));
  await page.getByRole("radio").check();
  expect(requests).toHaveLength(0);
  await page.getByRole("button",{name:"按所选规则核验"}).click();
  await expect(page.getByRole("button",{name:"重试本次核验"})).toBeVisible();
  await page.getByRole("button",{name:"重试本次核验"}).click();
  await expect(page.getByRole("region",{name:"质量核验明细"})).toBeVisible();
  expect(requests).toEqual(Array(2).fill({policy_id:qualityRule.id,candidate_evaluation_id:qualityFixture.candidate_evaluation_id,reference_evaluation_id:qualityFixture.reference_evaluation_id}));
  await page.screenshot({path:"test-results/quality-desktop.png",fullPage:true});
  await page.getByRole("link",{name:"打开固定核验详情 →"}).click();
  await expect(page.getByRole("heading",{name:"质量核验详情"})).toBeVisible();
  await page.reload();
  await expect(page.getByRole("region",{name:"质量核验明细"})).toBeVisible();
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:"test-results/quality-mobile.png",fullPage:true});
});
test("quality workflow rejects wrong scope, recovers from write pause and clears old result on selection change",async({page})=>{
  await login(page);await qualitySources(page);
  let state="foreign";
  const alternativePolicy={...qualityRule.policy,policy_version:"second-rule"};
  const alternativeHash=createHash("sha256").update(JSON.stringify(alternativePolicy,Object.keys(alternativePolicy).sort())).digest("hex");
  const alternative={policy:alternativePolicy,content_hash:alternativeHash,id:"historical-quality-policy-"+alternativeHash};
  await page.route("**/api/yta/v1/historical-quality-policies?*",route=>route.fulfill({json:{items:[state==="foreign" ? {...qualityRule,policy:{...qualityRule.policy,data_mode:"LIVE_RESEARCH"}} : qualityRule,alternative],next_cursor:null}}));
  await page.route("**/api/yta/v1/historical-quality-assessments",route=>state==="paused"
    ? route.fulfill({status:503,json:{error:{code:"WRITE_ADMISSION_PAUSED",message:"paused"}}})
    : route.fulfill({json:state==="wrong" ? {...qualityFixture,candidate_evaluation_id:"model-evaluation-experimental-"+"0".repeat(64)} : qualityFixture}));
  await page.goto(qualityPath);
  await expect(page.getByRole("button",{name:"重试规则列表"})).toBeVisible();
  state="paused";
  await page.getByRole("button",{name:"重试规则列表"}).click();
  await page.getByRole("radio").first().check();
  await page.getByRole("button",{name:"按所选规则核验"}).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("暂时关闭");
  state="wrong";
  await page.getByRole("button",{name:"重试本次核验"}).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("无法确认");
  await expect(page.getByRole("region",{name:"质量核验明细"})).toHaveCount(0);
  state="valid";
  await page.getByRole("button",{name:"重试本次核验"}).click();
  await expect(page.getByRole("region",{name:"质量核验明细"})).toBeVisible();
  await page.getByRole("radio").nth(1).check();
  await expect(page.getByRole("region",{name:"质量核验明细"})).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("radio").first()).not.toBeChecked();
  await expect(page.getByRole("region",{name:"质量核验明细"})).toHaveCount(0);
});
test("quality workflow disabled state does not request private evidence",async({page})=>{
  await login(page,false);let calls=0;
  await page.route("**/api/yta/v1/historical-quality-**",route=>{calls++;return route.abort();});
  await page.goto(qualityPath);
  await expect(page.getByRole("heading",{name:"历史质量核验尚未启用"})).toBeVisible();
  expect(calls).toBe(0);
});

const reviewProbability=JSON.parse(readFileSync("packages/contracts/fixtures/review-probability-evidence.json","utf8"));
const reviewProbabilityRule={id:"probability-quality-policy-"+reviewProbability.assessment.policy_hash,content_hash:reviewProbability.assessment.policy_hash,policy:reviewProbability.assessment.policy};
function reviewGate() {
  const fixture=JSON.parse(readFileSync("packages/contracts/fixtures/model-quality-gate.json","utf8")),p=reviewProposalFixture();
  const {content_hash:_,...old}=fixture;
  const body={...old,proposal_id:p.id,proposal_content_hash:p.content_hash,candidate_evaluation_id:qualityFixture.candidate_evaluation_id,
    reference_evaluation_id:qualityFixture.reference_evaluation_id,candidate_model_id:qualityReports[0].model_id,cohort_hash:qualityFixture.cohort_hash,
    price_assessment_id:"historical-quality-assessment-"+qualityFixture.content_hash,probability_assessment_id:"probability-quality-assessment-"+reviewProbability.assessment.content_hash,
    calibration_id:qualityReports[0].calibration_id,calibration_content_hash:qualityReports[0].calibration_content_hash,
    probability_report_hash:reviewProbability.assessment.report_hash,calibration_cohort_hash:reviewProbability.assessment.calibration_cohort_hash,evaluation_cohort_hash:reviewProbability.assessment.evaluation_cohort_hash,
    price_policy_hash:qualityFixture.policy_hash,probability_policy_hash:reviewProbability.assessment.policy_hash,
    price_numeric_pass:true,probability_numeric_pass:true,numeric_pass:true,failed_price_checks:[],failed_probability_checks:[],
    promotion_blockers:["HISTORICAL_EVIDENCE_UNQUALIFIED","HUMAN_APPROVAL_REQUIRED"]};
  return {...body,content_hash:createHash("sha256").update(JSON.stringify(body,Object.keys(body).sort())).digest("hex")};
}
async function completeReviewEvidence(page:Page) {
  const price=page.getByLabel("价格证据",{exact:true}),probability=page.getByLabel("概率证据",{exact:true});
  await price.getByRole("radio").check();await price.getByRole("button",{name:"按所选规则核验"}).click();
  await probability.getByRole("radio").check();await probability.getByRole("button",{name:"按所选规则核验"}).click();
  await page.getByRole("button",{name:"固定组合证据",exact:true}).click();
  await expect(page.getByRole("heading",{name:"组合证据已固定 · 尚未批准"})).toBeVisible();
}
const reviewFixture=JSON.parse(readFileSync("packages/contracts/fixtures/model-review.json","utf8"));
function reviewProposalFixture() {
  const p={...proposalFixture.proposal,candidate_evaluation_id:qualityFixture.candidate_evaluation_id,
    reference_evaluation_id:qualityFixture.reference_evaluation_id,candidate_report_hash:qualityReports[0].content_hash,
    reference_report_hash:qualityReports[1].content_hash,candidate_model_id:qualityReports[0].model_id,
    reference_model_id:qualityReports[1].model_id,cohort_hash:qualityFixture.cohort_hash};
  return {...proposalFixture,proposal:p,content_hash:createHash("sha256").update(JSON.stringify(p,Object.keys(p).sort())).digest("hex")};
}
async function reviewSources(page:Page,allowed=true) {
  await login(page);await qualitySources(page);
  let owner="";
  await page.route("**/api/yta/v1/me",async route=>{
    const response=await route.fetch(),me=await response.json();owner=me.user.id;
    await route.fulfill({json:{...me,experimental_models_enabled:true,can_review_models:allowed}});
  });
  const proposal=reviewProposalFixture();
  await page.route("**/api/yta/v1/model-review-proposals?*",route=>route.fulfill({json:{items:[proposal],next_cursor:null}}));
  await page.route("**/api/yta/v1/model-review-proposals/*",route=>route.fulfill({json:proposal}));
  await page.route("**/api/yta/v1/historical-quality-policies?*",route=>route.fulfill({json:{items:[qualityRule],next_cursor:null}}));
  await page.route("**/api/yta/v1/historical-quality-assessments",route=>route.fulfill({json:qualityFixture}));
  await page.route("**/api/yta/v1/experimental-calibrations/*",route=>route.fulfill({json:reviewProbability.calibration}));
  await page.route("**/api/yta/v1/probability-quality-policies?*",route=>route.fulfill({json:{items:[reviewProbabilityRule],next_cursor:null}}));
  await page.route("**/api/yta/v1/probability-quality-assessments",route=>route.fulfill({json:reviewProbability.assessment}));
  await page.route("**/api/yta/v1/model-review-quality-gates",route=>route.fulfill({json:reviewGate()}));
  return {proposal,owner:()=>owner};
}
function reviewResponse(owner:string,key:string,request:Record<string,unknown>) {
  if("quality_gate_id" in request) {
    const fixture=JSON.parse(readFileSync("packages/contracts/fixtures/model-review-v2.json","utf8"));
    const {content_hash:_,...base}=fixture.review,gate=reviewGate();
    const body={...base,...request,reviewer_id:owner,request_id:key,proposal_id:proposalFixture.id,
      proposal_content_hash:gate.proposal_content_hash,quality_gate_content_hash:gate.content_hash,
      price_assessment_id:gate.price_assessment_id,probability_assessment_id:gate.probability_assessment_id,
      price_policy_hash:gate.price_policy_hash,probability_policy_hash:gate.probability_policy_hash,
      candidate_model_id:gate.candidate_model_id,previous_review_id:request.expected_previous_review_id,
      revision:request.expected_previous_review_id ? 2 : 1,disposition:request.action==="REJECT" ? "REJECTED" : "BLOCKED",
      blocking_reasons:request.action==="REJECT" ? [] : ["HISTORICAL_EVIDENCE_UNQUALIFIED"],failed_price_checks:[],failed_probability_checks:[]};
    delete body.expected_previous_review_id;
    return {...fixture,review:{...body,content_hash:createHash("sha256").update(JSON.stringify(body,Object.keys(body).sort())).digest("hex")}};
  }
  const {content_hash:_,...base}=reviewFixture.review;
  const body={...base,...request,reviewer_id:owner,request_id:key,proposal_id:proposalFixture.id,
    assessment_content_hash:qualityFixture.content_hash,policy_hash:qualityFixture.policy_hash,
    proposal_content_hash:reviewProposalFixture().content_hash,candidate_model_id:qualityReports[0].model_id,
    previous_review_id:request.expected_previous_review_id,revision:request.expected_previous_review_id ? 2 : 1,
    disposition:request.action==="REJECT" ? "REJECTED" : "BLOCKED",
    blocking_reasons:request.action==="REJECT" ? [] : ["HISTORICAL_EVIDENCE_UNQUALIFIED"],failed_checks:[]};
  delete body.expected_previous_review_id;
  return {...reviewFixture,review:{...body,content_hash:createHash("sha256").update(JSON.stringify(body,Object.keys(body).sort())).digest("hex")}};
}

test("reviewer explicitly checks evidence and recovers the same review after a lost response and refresh",async({page})=>{
  const ctx=await reviewSources(page);let saved:ReturnType<typeof reviewResponse>|undefined;
  await page.route("**/api/yta/v1/model-proposals/*/reviews?*",route=>route.fulfill({json:{items:saved ? [saved] : [],next_cursor:null}}));
  const sent:{key:string;body:Record<string,unknown>}[]=[];
  await page.route("**/api/yta/v1/model-reviews",route=>{
    const key=route.request().headers()["idempotency-key"]!,body=route.request().postDataJSON();sent.push({key,body});
    saved=reviewResponse(ctx.owner(),key,body);
    return sent.length===1 ? route.abort("failed") : route.fulfill({json:saved});
  });
  await page.route("**/api/yta/v1/model-reviews/*",route=>route.fulfill({json:saved}));
  await page.goto("/model-review-proposals");
  await expect(page.getByRole("heading",{name:"审核模型提案"})).toBeVisible();
  await page.getByRole("link",{name:/查看提案/}).click();
  await expect(page.getByRole("button",{name:"确认提交审核"})).toBeDisabled();
  await completeReviewEvidence(page);

  await page.getByLabel("审核动作").selectOption("APPROVE");await page.getByLabel("审核理由").fill("核对固定证据，保留资格未通过原因。");
  await page.screenshot({path:"test-results/review-form-desktop.png",fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  await page.screenshot({path:"test-results/review-form-mobile.png",fullPage:true});
  await page.setViewportSize({width:1280,height:900});
  await page.getByRole("button",{name:"确认提交审核"}).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("保留原请求");
  await page.reload();await expect(page.getByRole("button",{name:"重试原审核"})).toBeVisible();
  await expect(page.getByLabel("审核理由")).toBeDisabled();
  await page.getByRole("button",{name:"重试原审核"}).click();
  await expect(page.getByRole("button",{name:"重新加载最新状态并核对"})).toBeVisible();
  expect(sent).toHaveLength(2);expect(sent[1]).toEqual(sent[0]);expect(sent[0]!.body.expected_previous_review_id).toBeNull();
  await page.screenshot({path:"test-results/review-desktop.png",fullPage:true});
  await page.getByRole("link",{name:"打开审核记录"}).first().click();await expect(page).toHaveURL(/\/model-reviews\//);await page.reload();
  await expect(page.getByRole("heading",{name:"模型审核记录"})).toBeVisible();
  await expect(page.getByText("晋升受阻 · 未批准",{exact:true})).toBeVisible();
  await page.setViewportSize({width:390,height:844});
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  await page.screenshot({path:"test-results/review-mobile.png",fullPage:true});
});

test("review conflicts require reload, rejected proposals close and storage failure prevents dispatch",async({page})=>{
  const ctx=await reviewSources(page);let closed=false,calls=0;
  const request={proposal_id:ctx.proposal.id,assessment_id:"historical-quality-assessment-"+qualityFixture.content_hash,action:"REJECT",reason:"另一审核已拒绝",expected_previous_review_id:null};
  await page.route("**/api/yta/v1/model-proposals/*/reviews?*",route=>route.fulfill({json:{items:closed ? [reviewResponse(ctx.owner(),reviewFixture.review.request_id,request)] : [],next_cursor:null}}));
  await page.route("**/api/yta/v1/model-reviews",route=>{calls++;closed=true;return route.fulfill({status:409,json:{error:{code:"MODEL_REVIEW_STALE",message:"Changed"}}});});
  await page.goto("/model-review-proposals/"+ctx.proposal.id);
  await completeReviewEvidence(page);
  await page.getByLabel("审核理由").fill("不同意候选依据");
  await page.evaluate(()=>{const original=Storage.prototype.setItem;Object.assign(window,{restoreReviewStorage:()=>{Storage.prototype.setItem=original;}});Storage.prototype.setItem=()=>{throw Error("Unavailable");};});
  await page.getByRole("button",{name:"确认提交审核"}).click();await expect(page.getByRole("main").getByRole("alert")).toContainText("允许保存");expect(calls).toBe(0);
  await page.evaluate(()=>{(window as unknown as {restoreReviewStorage:()=>void}).restoreReviewStorage();});
  await page.getByRole("button",{name:"确认提交审核"}).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("状态已变化");
  await page.getByRole("button",{name:"重新加载最新状态并核对"}).click();
  await expect(page.getByRole("heading",{name:"提案已拒绝，不能继续审核"})).toBeVisible();
  await expect(page.getByRole("button",{name:"确认提交审核"})).toHaveCount(0);expect(calls).toBe(1);
});

test("ordinary accounts cannot use review pages or request reviewer evidence",async({page})=>{
  await reviewSources(page,false);let calls=0;
  await page.route("**/api/yta/v1/model-review**",route=>{calls++;return route.abort();});
  await page.goto("/model-review-proposals");await expect(page.getByRole("heading",{name:"当前账户无法访问模型审核"})).toBeVisible();
  await page.goto("/model-review-proposals/"+proposalFixture.id);await expect(page.getByRole("heading",{name:"当前账户无法访问模型审核"})).toBeVisible();
  await expect(page.getByRole("navigation").getByRole("link",{name:"模型审核"})).toHaveCount(0);expect(calls).toBe(0);
});

const calibrationFixture=JSON.parse(readFileSync("packages/contracts/fixtures/calibration-report.json","utf8"));
test("fixed calibration report links from price evidence and preserves probabilities, empty bins and window identity",async({page})=>{
  await login(page);
  const price=JSON.parse(readFileSync("packages/contracts/fixtures/experimental-price-report.json","utf8"));
  await page.route("**/api/yta/v1/experimental-evaluations/*",route=>route.fulfill({json:price}));
  const requests:string[]=[];
  await page.route("**/api/yta/v1/experimental-calibrations/*",route=>{requests.push(route.request().url());return route.fulfill({json:calibrationFixture});});
  await page.goto("/model-evaluations/model-evaluation-experimental-"+price.content_hash);
  await page.getByRole("link",{name:"查看固定概率校准报告"}).click();
  await expect(page).toHaveURL("/model-calibrations/"+calibrationFixture.id);
  await expect(page.getByRole("heading",{name:"概率校准报告",exact:true})).toBeVisible();
  const bins=page.getByRole("region",{name:"概率可靠性分箱"});
  await expect(bins.locator("tbody tr")).toHaveCount(10);
  expect(await bins.getByText("无样本",{exact:true}).count()).toBeGreaterThan(0);
  await expect(page.getByText(calibrationFixture.report.reliability.brier,{exact:true}).first()).toBeVisible();
  await expect(page.getByRole("region",{name:"概率逐样本结果"}).locator("tbody tr")).toHaveCount(calibrationFixture.report.evaluation_ids.length);
  await expect(page.getByRole("button",{name:"下一页样本"})).toBeDisabled();
  await page.getByText("核对拟合窗口、结点与代码身份").click();
  await expect(page.locator(".report-manifest")).toContainText(calibrationFixture.report.calibration_ids[0]);
  await page.setViewportSize({width:1440,height:1000});
  await page.screenshot({path:"test-results/calibration-desktop.png",fullPage:true});
  await page.reload();await expect(bins).toBeVisible();
  await page.setViewportSize({width:390,height:844});
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  await page.screenshot({path:"test-results/calibration-mobile.png",fullPage:true});
  expect(requests.every(url=>url.endsWith(calibrationFixture.id))).toBe(true);
});

test("calibration page refuses wrong identity or mode and clears stale values on failed reload",async({page})=>{
  await login(page);let state="wrong";
  await page.route("**/api/yta/v1/experimental-calibrations/*",route=>{
    if(state==="unavailable")return route.fulfill({status:503,json:{error:{code:"MODEL_UNAVAILABLE",message:"Unavailable"}}});
    const fixture=state==="wrong" ? {...calibrationFixture,id:"calibration-experimental-"+"0".repeat(64),content_hash:"0".repeat(64)}
      : state==="foreign" ? {...calibrationFixture,report:{...calibrationFixture.report,data_mode:"LIVE_RESEARCH"}} : calibrationFixture;
    return route.fulfill({json:fixture});
  });
  await page.goto("/model-calibrations/"+calibrationFixture.id);
  await expect(page.getByRole("main").getByRole("alert")).toContainText("暂时无法读取");
  await expect(page.getByRole("region",{name:"概率可靠性分箱"})).toHaveCount(0);
  state="foreign";await page.getByRole("button",{name:"重新加载"}).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("暂时无法读取");
  state="valid";await page.getByRole("button",{name:"重新加载"}).click();
  await expect(page.getByRole("region",{name:"概率可靠性分箱"})).toBeVisible();
  state="unavailable";await page.reload();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("暂时无法读取");
  await expect(page.getByRole("region",{name:"概率可靠性分箱"})).toHaveCount(0);
});

test("disabled calibration page makes no report request",async({page})=>{
  await login(page,false);let calls=0;
  await page.route("**/api/yta/v1/experimental-calibrations/*",route=>{calls++;return route.abort();});
  await page.goto("/model-calibrations/"+calibrationFixture.id);
  await expect(page.getByRole("heading",{name:"概率校准报告尚未启用"})).toBeVisible();expect(calls).toBe(0);
});

const probabilityFixture=JSON.parse(readFileSync("packages/contracts/fixtures/probability-quality-assessment.json","utf8"));
const probabilitySource=JSON.parse(readFileSync("packages/contracts/fixtures/probability-quality-source.json","utf8"));
const probabilityRule={id:"probability-quality-policy-"+probabilityFixture.policy_hash,content_hash:probabilityFixture.policy_hash,policy:probabilityFixture.policy};
const probabilityPath="/probability-checks/"+probabilityFixture.calibration_id;
async function probabilitySources(page:Page) {
 await page.route("**/api/yta/v1/experimental-calibrations/*",route=>route.fulfill({json:probabilitySource}));
}
test("probability quality rules paginate and explicit lost-response retry preserves the selected evidence",async({page})=>{
  await login(page);await probabilitySources(page);
  const olderPolicy={...probabilityRule.policy,policy_version:"older-test-rule"};
  const olderHash=createHash("sha256").update(JSON.stringify(olderPolicy,Object.keys(olderPolicy).sort())).digest("hex");
  const older={policy:olderPolicy,content_hash:olderHash,id:"probability-quality-policy-"+olderHash};
  const queries: URL[]=[];
  await page.route("**/api/yta/v1/probability-quality-policies?*",route=>{
    const url=new URL(route.request().url());queries.push(url);
    return route.fulfill({json:url.searchParams.has("cursor") ? {items:[older],next_cursor:null} : {items:[probabilityRule],next_cursor:probabilityRule.id}});
  });
  const requests:unknown[]=[];
  await page.route("**/api/yta/v1/probability-quality-assessments",route=>{
    requests.push(route.request().postDataJSON());
    return requests.length===1 ? route.abort("failed") : route.fulfill({json:probabilityFixture});
  });
  await page.route("**/api/yta/v1/probability-quality-assessments/*",route=>route.fulfill({json:probabilityFixture}));
  await page.goto("/model-calibrations/"+probabilityFixture.calibration_id);
  await page.getByRole("link",{name:"选择规则并核验概率质量 →"}).click();
  await expect(page.getByRole("radio")).toHaveCount(1);
  await expect(page.getByRole("button",{name:"按所选规则核验"})).toBeDisabled();
  expect(queries[0]?.searchParams.get("horizon_sessions")).toBe(String(probabilityFixture.policy.horizon_sessions));
  await page.getByRole("button",{name:"下一页"}).click();
  await expect(page.getByRole("radio")).toHaveAccessibleName(/older-test-rule/);
  await page.getByRole("button",{name:"上一页"}).click();
  await expect(page.getByRole("radio")).toHaveAccessibleName(new RegExp(probabilityRule.policy.policy_version));
  await page.getByRole("radio").check();
  expect(requests).toHaveLength(0);
  await page.getByRole("button",{name:"按所选规则核验"}).click();
  await expect(page.getByRole("button",{name:"重试本次核验"})).toBeVisible();
  await page.getByRole("button",{name:"重试本次核验"}).click();
  await expect(page.getByRole("region",{name:"概率核验明细"})).toBeVisible();
  expect(requests).toEqual(Array(2).fill({policy_id:probabilityRule.id,calibration_id:probabilityFixture.calibration_id}));
  await page.screenshot({path:"test-results/probability-quality-desktop.png",fullPage:true});
  await page.getByRole("link",{name:"打开固定核验详情 →"}).click();
  await expect(page.getByRole("heading",{name:"概率核验详情"})).toBeVisible();
  await page.reload();
  await expect(page.getByRole("region",{name:"概率核验明细"})).toBeVisible();
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:"test-results/probability-quality-mobile.png",fullPage:true});
});
test("probability quality workflow rejects wrong scope, recovers from write pause and clears old result on selection change",async({page})=>{
  await login(page);await probabilitySources(page);
  let state="foreign";
  const alternativePolicy={...probabilityRule.policy,policy_version:"second-rule"};
  const alternativeHash=createHash("sha256").update(JSON.stringify(alternativePolicy,Object.keys(alternativePolicy).sort())).digest("hex");
  const alternative={policy:alternativePolicy,content_hash:alternativeHash,id:"probability-quality-policy-"+alternativeHash};
  await page.route("**/api/yta/v1/probability-quality-policies?*",route=>route.fulfill({json:{items:[state==="foreign" ? {...probabilityRule,policy:{...probabilityRule.policy,data_mode:"LIVE_RESEARCH"}} : probabilityRule,alternative],next_cursor:null}}));
  await page.route("**/api/yta/v1/probability-quality-assessments",route=>state==="paused"
    ? route.fulfill({status:503,json:{error:{code:"WRITE_ADMISSION_PAUSED",message:"paused"}}})
    : route.fulfill({json:state==="wrong" ? {...probabilityFixture,calibration_id:"calibration-experimental-"+"0".repeat(64)} : probabilityFixture}));
  await page.goto(probabilityPath);
  await expect(page.getByRole("button",{name:"重试规则列表"})).toBeVisible();
  state="paused";
  await page.getByRole("button",{name:"重试规则列表"}).click();
  await page.getByRole("radio").first().check();
  await page.getByRole("button",{name:"按所选规则核验"}).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("暂时关闭");
  state="wrong";
  await page.getByRole("button",{name:"重试本次核验"}).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("无法确认");
  await expect(page.getByRole("region",{name:"概率核验明细"})).toHaveCount(0);
  state="valid";
  await page.getByRole("button",{name:"重试本次核验"}).click();
  await expect(page.getByRole("region",{name:"概率核验明细"})).toBeVisible();
  await page.getByRole("radio").nth(1).check();
  await expect(page.getByRole("region",{name:"概率核验明细"})).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("radio").first()).not.toBeChecked();
  await expect(page.getByRole("region",{name:"概率核验明细"})).toHaveCount(0);
});
test("probability quality workflow disabled state does not request private evidence",async({page})=>{
  await login(page,false);let calls=0;
  await page.route("**/api/yta/v1/probability-quality-**",route=>{calls++;return route.abort();});
  await page.goto(probabilityPath);
  await expect(page.getByRole("heading",{name:"概率质量核验尚未启用"})).toBeVisible();
  expect(calls).toBe(0);
});


test("mixed review history and v2 details retain both fixed evidence links after refresh",async({page})=>{
  const ctx=await reviewSources(page);
  const v2=JSON.parse(readFileSync("packages/contracts/fixtures/model-review-v2.json","utf8"));
  const {content_hash:_,...raw}=v2.review;
  const body={...raw,proposal_id:ctx.proposal.id,previous_review_id:reviewFixture.id,revision:2};
  const combined={...v2,review:{...body,content_hash:createHash("sha256").update(JSON.stringify(body,Object.keys(body).sort())).digest("hex")}};
  const legacy={...reviewFixture,review:{...reviewFixture.review,proposal_id:ctx.proposal.id}};
  await page.route("**/api/yta/v1/model-proposals/*/reviews?*",route=>route.fulfill({json:{items:[combined,legacy],next_cursor:null}}));
  await page.route("**/api/yta/v1/model-reviews/*",route=>route.fulfill({json:combined}));
  await page.goto("/model-review-proposals/"+ctx.proposal.id);
  await expect(page.getByRole("heading",{name:"审核 #2",exact:true})).toBeVisible();
  await expect(page.getByRole("heading",{name:"审核 #1",exact:true})).toBeVisible();
  await expect(page.getByRole("link",{name:"查看固定概率核验"})).toHaveAttribute("href","/probability-quality/"+body.probability_assessment_id);
  await page.getByRole("link",{name:"打开审核记录"}).first().click();await expect(page).toHaveURL(/\/model-reviews\//);await page.reload();
  await expect(page.getByRole("heading",{name:"模型审核记录"})).toBeVisible();
  await expect(page.getByRole("link",{name:"查看固定质量核验"})).toHaveAttribute("href","/model-quality/"+body.price_assessment_id);
  await expect(page.getByText("组合核验："+body.quality_gate_id)).toBeVisible();
  await page.setViewportSize({width:390,height:844});
  await expect(page.getByRole("link",{name:"查看固定概率核验"})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
});


test("combined evidence retries the same pair, rejects wrong scope and invalidates changed selections",async({page})=>{
  const ctx=await reviewSources(page);let mode="lost";const sent:unknown[]=[];
  await page.route("**/api/yta/v1/model-proposals/*/reviews?*",route=>route.fulfill({json:{items:[],next_cursor:null}}));
  await page.route("**/api/yta/v1/model-review-quality-gates",route=>{
    sent.push(route.request().postDataJSON());
    if(mode==="lost"){mode="wrong";return route.abort("failed");}
    return route.fulfill({json:mode==="wrong" ? {...reviewGate(),proposal_id:reviewFixture.review.request_id} : reviewGate()});
  });
  await page.goto("/model-review-proposals/"+ctx.proposal.id);
  const price=page.getByLabel("价格证据",{exact:true}),probability=page.getByLabel("概率证据",{exact:true});
  await price.getByRole("radio").check();await price.getByRole("button",{name:"按所选规则核验"}).click();
  await expect(page.getByRole("button",{name:"固定组合证据",exact:true})).toBeDisabled();
  await probability.getByRole("radio").check();await probability.getByRole("button",{name:"按所选规则核验"}).click();
  await page.getByRole("button",{name:"固定组合证据",exact:true}).click();
  await expect(page.getByRole("button",{name:"重试固定组合证据"})).toBeVisible();
  await page.getByRole("button",{name:"重试固定组合证据"}).click();
  await expect(page.getByRole("heading",{name:"组合证据已固定 · 尚未批准"})).toHaveCount(0);
  mode="valid";await page.getByRole("button",{name:"重试固定组合证据"}).click();
  await expect(page.getByRole("heading",{name:"组合证据已固定 · 尚未批准"})).toBeVisible();
  expect(sent).toHaveLength(3);expect(sent[1]).toEqual(sent[0]);expect(sent[2]).toEqual(sent[0]);
  await page.getByLabel("审核理由").fill("两类证据均核对");await expect(page.getByRole("button",{name:"确认提交审核"})).toBeEnabled();
  await probability.getByRole("button",{name:"按所选规则核验"}).click();
  await expect(page.getByRole("heading",{name:"组合证据已固定 · 尚未批准"})).toHaveCount(0);
  await expect(page.getByRole("button",{name:"确认提交审核"})).toBeDisabled();
});

test("a saved legacy review retries its exact v1 body without requiring new combined evidence",async({page})=>{
  const ctx=await reviewSources(page);const key=reviewFixture.review.request_id;
  const body={proposal_id:ctx.proposal.id,assessment_id:"historical-quality-assessment-"+qualityFixture.content_hash,action:"APPROVE",reason:"保留旧审核原请求",expected_previous_review_id:null};
  await page.route("**/api/yta/v1/model-proposals/*/reviews?*",route=>route.fulfill({json:{items:[],next_cursor:null}}));
  await page.goto("/model-review-proposals/"+ctx.proposal.id);
  await expect(page.getByLabel("审核理由")).toBeVisible();
  await page.evaluate(({cache,attempt})=>sessionStorage.setItem(cache,JSON.stringify(attempt)),{cache:`yta:model-review:${ctx.owner()}:REPLAY:${ctx.proposal.id}`,attempt:{key,request:body}});
  let dispatched:unknown;
  await page.route("**/api/yta/v1/model-reviews",route=>{dispatched={key:route.request().headers()["idempotency-key"],request:route.request().postDataJSON()};return route.fulfill({json:reviewResponse(ctx.owner(),key,body)});});
  await page.reload();await page.getByRole("button",{name:"重试原审核"}).click();
  await expect(page.getByRole("button",{name:"重新加载最新状态并核对"})).toBeVisible();
  expect(dispatched).toEqual({key,request:body});
  await expect(page.getByRole("heading",{name:"核对价格与概率证据"})).toHaveCount(0);
});
