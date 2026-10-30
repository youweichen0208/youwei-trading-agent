import { execFileSync } from "node:child_process";
import { test, expect, type Page, type BrowserContext } from "@playwright/test";

let loginCookies: Awaited<ReturnType<BrowserContext["cookies"]>> = [];
async function login(page: Page) {
  if (loginCookies.length) {
    await page.context().addCookies(loginCookies);
    if ((await page.request.get("/api/yta/v1/me")).ok()) {
      await page.goto("/research");
      await expect(page.getByRole("heading", {name: "NVDA · 价格研究"})).toBeVisible();
      return;
    }
    await page.context().clearCookies();
  }
  await page.goto("/research"); await expect(page).toHaveURL(/\/login$/);
  await page.getByLabel("邮箱").fill(process.env.YTA_E2E_EMAIL!);
  await page.getByLabel("密码").fill(process.env.YTA_E2E_PASSWORD!);
  await page.getByRole("button", {name: "进入研究工作台"}).click();
  await expect(page.getByRole("heading", {name: "NVDA · 价格研究"})).toBeVisible();
  loginCookies = await page.context().cookies();
}
test("authenticated Swagger renders local documentation without external requests or mutations", async ({ page }) => {
  await login(page);
  const urls: string[] = [], errors: string[] = [], writes: string[] = [];
  page.on("request", request => {urls.push(request.url()); if (request.method() !== "GET") writes.push(request.method());});
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/api/yta/v1/docs");
  await expect(page).toHaveTitle("YTA API 文档");
  await expect(page.getByRole("heading", {name: /YTA BFF API/})).toBeVisible();
  const spec = await (await page.request.get("/api/yta/v1/docs/openapi.json")).json();
  const operations = Object.values(spec.paths).reduce<number>((n, value) => n + Object.keys(value as object).length, 0);
  await expect(page.locator(".opblock")).toHaveCount(operations);
  await page.locator("#operations-default-freeze .opblock-summary").click();
  await expect(page.locator("#operations-default-freeze")).toContainText("confirmed");
  await expect(page.getByRole("button", {name: "Try it out"})).toHaveCount(0);
  expect(urls.every(url => new URL(url).origin === new URL(page.url()).origin)).toBe(true);
  expect(writes).toEqual([]);
  expect(errors).toEqual([]);
  await page.screenshot({path: "test-results/swagger-desktop.png", fullPage: true});
});
test("authenticated replay: tools, explicit freeze, settlement, persisted history", async ({ page }) => {
  const errors: string[] = []; page.on("pageerror", e => errors.push(e.message));
  await page.setViewportSize({width: 1440, height: 1000});
  await login(page);
  await expect(page.getByText("合成行情 · 固定流程验证 · 未连接真实 Hermes")).toBeVisible();
  await page.getByLabel("研究问题").fill("查看 NVDA 的五日基线");
  const admitted = page.waitForResponse(response => response.request().method() === "POST" && /\/sessions\/[^/]+\/messages$/.test(new URL(response.url()).pathname));
  await page.getByRole("button", {name: "发送 →"}).click();
  const runId = (await (await admitted).json()).run_id;
  await expect(page.getByText("研究已完成", {exact: true})).toBeVisible();
  const savedRun = await (await page.request.get(`/api/yta/v1/runs/${runId}`)).json();
  expect(savedRun.report.status).toBe("DRAFT_READY");
  expect(savedRun.report.draft_id).toBe(savedRun.draft.draft_id);
  const evidenceResponse = await page.request.get(`/api/yta/v1/prediction-drafts/${savedRun.draft.draft_id}/evidence`);
  expect(evidenceResponse.status()).toBe(200);
  const historicalEvidence = await evidenceResponse.json();
  expect(historicalEvidence.data_snapshot_hash).toBe(savedRun.report.evidence[0].data_snapshot_hash);
  expect(historicalEvidence.snapshot.data_mode).toBe("REPLAY");
  expect(historicalEvidence.snapshot.bars.length).toBeGreaterThan(20);
  expect(typeof historicalEvidence.snapshot.bars[0].close).toBe("string");
  expect(savedRun.report.evidence.map((item: {tool: string}) => item.tool)).toEqual(["market_snapshot", "indicators"]);
  expect(savedRun.report.evidence.every((item: {data_snapshot_hash: string}) => item.data_snapshot_hash === savedRun.draft.forecast.data_snapshot_hash)).toBe(true);
  const reportPanel = page.locator("details.research-report");
  await reportPanel.getByText("研究依据与风险", {exact: true}).click();
  await expect(reportPanel.getByText("风险与限制", {exact: true})).toBeVisible();
  await expect(reportPanel.getByText("合成回放数据不能证明真实行情中的预测表现。", {exact: true})).toBeVisible();
  await expect(reportPanel.locator(".report-sources > li")).toHaveCount(2);
  await reportPanel.locator(".report-fingerprint").first().getByText("核对数据快照").click();
  await expect(reportPanel.locator("code").first()).toHaveText(savedRun.draft.forecast.data_snapshot_hash);
  await reportPanel.getByRole("button", {name: "查看草稿历史行情"}).click();
  const historical = reportPanel.getByRole("region", {name: "历史日线数据"});
  await expect(historical).toBeVisible();
  await expect(historical.locator("tbody tr")).toHaveCount(20);
  await expect(historical.locator("tbody tr").first()).toContainText(historicalEvidence.snapshot.bars.at(-1).session);
  await reportPanel.getByRole("button", {name: "更早日线"}).click();
  await expect(reportPanel).toContainText("第 2 / 25 页");
  await reportPanel.getByRole("button", {name: "较近日线"}).click();
  await historical.scrollIntoViewIfNeeded();
  await page.screenshot({path: "test-results/evidence-desktop.png", fullPage: true});
  await reportPanel.evaluate(element => { const area = element.closest(".messages")!; area.scrollTop += element.getBoundingClientRect().top - area.getBoundingClientRect().top; });
  await page.screenshot({path: "test-results/report-desktop.png", fullPage: true});
  await expect(page.getByText("由研究工具打开")).toBeVisible();
  await expect(page.locator(".canvas-panel").getByText("0.50%", {exact: true})).toBeVisible();
  const before = await page.request.get("/api/yta/v1/predictions");
  expect((await before.json()).items).toHaveLength(0);
  await page.screenshot({path: "test-results/research-desktop.png", fullPage: true});
  await page.getByRole("button", {name: "检查并冻结预测"}).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", {name: "返回检查"}).click();
  expect((await (await page.request.get("/api/yta/v1/predictions")).json()).items).toHaveLength(0);
  await page.getByRole("button", {name: "检查并冻结预测"}).click();
  await page.getByRole("button", {name: "确认冻结", exact: true}).click();
  await page.getByRole("link", {name: "查看冻结记录"}).click();
  await expect(page.getByRole("heading", {name: "预测详情"})).toBeVisible();
  await page.getByRole("button", {name: "模拟到期并结算"}).click();
  await expect(page.getByText("2.00%", {exact: true})).toBeVisible();
  await expect(page.getByText("1.50 个百分点")).toBeVisible();
  await page.reload(); await expect(page.getByText("结算版本 1", {exact: true})).toBeVisible();
  await page.screenshot({path: "test-results/prediction-desktop.png", fullPage: true});
  const historyStreams: string[] = [];
  page.on("request", request => {
    if (/\/runs\/[^/]+\/events/.test(request.url())) historyStreams.push(request.url());
  });
  await page.getByRole("link", {name: "研究工作台", exact: true}).click();
  await page.getByRole("button", {name: /NVDA 研究/}).first().click();
  await expect(page.getByText("研究已完成", {exact: true})).toBeVisible();
  await expect(page.locator(".message.assistant")).toHaveCount(1);
  expect((await (await page.request.get(`/api/yta/v1/runs/${runId}`)).json()).report).toEqual(savedRun.report);
  await reportPanel.getByText("研究依据与风险", {exact: true}).click();
  await expect(reportPanel.locator(".report-sources > li")).toHaveCount(2);
  expect(historyStreams).toEqual([]);
  await expect(page.locator(".message.assistant")).toContainText(savedRun.report.summary);
  await expect(page.getByRole("button", {name: "停止研究"})).toHaveCount(0);
  await page.setViewportSize({width: 390, height: 844});
  await expect(reportPanel.getByText("风险与限制", {exact: true})).toBeVisible();
  await reportPanel.getByRole("button", {name: "查看草稿历史行情"}).click();
  await expect(reportPanel.getByRole("region", {name: "历史日线数据"})).toBeVisible();
  await reportPanel.getByRole("region", {name: "历史日线数据"}).scrollIntoViewIfNeeded();
  await page.screenshot({path: "test-results/evidence-mobile.png", fullPage: true});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await reportPanel.evaluate(element => { const area = element.closest(".messages")!; area.scrollTop += element.getBoundingClientRect().top - area.getBoundingClientRect().top; });
  await page.screenshot({path: "test-results/report-mobile.png", fullPage: true});
  await page.setViewportSize({width: 1440, height: 1000});
  await expect(page.locator(".canvas-panel").getByText("0.50%", {exact: true})).toBeVisible();
  expect(errors).toEqual([]);
});

test("quota refusal preserves input and navigation without automatic resubmission", async ({ page }) => {
  await login(page);
  const keys: string[] = [];
  let quotaClosed = true;
  await page.route("**/api/yta/v1/sessions/*/messages", async route => {
    if (route.request().method() !== "POST") { await route.continue(); return; }
    keys.push(route.request().postDataJSON().request_id);
    if (quotaClosed) await route.fulfill({status: 429, contentType: "application/json", body: JSON.stringify({error: {
      code: "RESEARCH_QUOTA_EXCEEDED", message: "Quota exhausted", request_id: "quota-browser-fixture", retryable: false,
    }})});
    else await route.continue();
  });
  const prompt = "额度释放后再研究 NVDA";
  await page.getByLabel("研究问题").fill(prompt);
  await page.getByRole("button", {name: "发送 →"}).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("过去 24 小时的研究请求已达到上限");
  await expect(page.getByRole("main").getByRole("alert")).not.toContainText("核对");
  await expect(page.getByLabel("研究问题")).toHaveValue(prompt);
  await expect(page.getByLabel("研究问题")).toBeEnabled();
  await expect(page.getByRole("button", {name: "＋ 新建"})).toBeEnabled();
  await expect(page.getByRole("button", {name: "核对原请求"})).toHaveCount(0);
  await expect(page.locator(".message.user")).toHaveCount(0);
  await page.screenshot({path: "test-results/research-quota.png", fullPage: true});
  expect(keys).toHaveLength(1);
  // Only a new, explicit user action retries after a definite non-admission.
  quotaClosed = false;
  await page.getByRole("button", {name: "发送 →"}).click();
  await expect(page.getByText("研究已完成", {exact: true})).toBeVisible();
  expect(keys).toHaveLength(2);
  expect(keys[1]).not.toBe(keys[0]);
  await expect(page.locator(".message.user")).toHaveCount(1);
});

test("mobile tabs, chart availability and session logout", async ({ page }) => {
  await page.setViewportSize({width: 375, height: 812}); await login(page);
  await expect(page.getByLabel("研究问题")).toBeVisible();
  await page.getByRole("button", {name: "图表与预测"}).click();
  await expect(page.getByRole("heading", {name: "NVDA NVIDIA"})).toBeVisible();
  await expect(page.locator(".chart-host canvas").first()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path: "test-results/research-mobile.png", fullPage: true});
  await page.getByRole("button", {name: "退出", exact: true}).click();
  await expect(page).toHaveURL(/\/login$/);
  expect((await page.request.get("/api/yta/v1/sessions")).status()).toBe(401);
});

test("lost admission and freeze responses retry the original keys without duplicate facts", async ({ page }) => {
  await page.setViewportSize({width: 1440, height: 1000}); await login(page);
  const requestKeys: string[] = [], freezeKeys: string[] = [];
  let lostMessage = false, lostFreeze = false;
  await page.route("**/api/yta/v1/sessions/*/messages", async route => {
    if (route.request().method() !== "POST") { await route.continue(); return; }
    requestKeys.push(route.request().postDataJSON().request_id);
    if (!lostMessage) { lostMessage = true; await route.fetch(); await route.abort("failed"); }
    else await route.continue();
  });
  await page.route("**/api/yta/v1/predictions", async route => {
    if (route.request().method() !== "POST") { await route.continue(); return; }
    freezeKeys.push(route.request().headers()["idempotency-key"]!);
    if (!lostFreeze) { lostFreeze = true; await route.fetch(); await route.abort("failed"); }
    else await route.continue();
  });
  await page.getByLabel("研究问题").fill("重试网络中断的回放");
  await page.getByRole("button", {name: "发送 →"}).click();
  await page.getByRole("button", {name: "核对原请求"}).click();
  await expect(page.getByText("研究已完成", {exact: true})).toBeVisible();
  expect(requestKeys).toHaveLength(2); expect(new Set(requestKeys).size).toBe(1);
  await expect(page.locator(".message.assistant")).toHaveCount(1);
  const before = (await (await page.request.get("/api/yta/v1/predictions")).json()).items.length;
  await page.getByRole("button", {name: "检查并冻结预测"}).click();
  await page.getByRole("button", {name: "确认冻结", exact: true}).click();
  await expect(page.getByRole("dialog").getByRole("alert")).toBeVisible();
  await page.getByRole("button", {name: "确认冻结", exact: true}).click();
  await expect(page.getByRole("link", {name: "查看冻结记录"})).toBeVisible();
  expect(freezeKeys).toHaveLength(2); expect(new Set(freezeKeys).size).toBe(1);
  expect((await (await page.request.get("/api/yta/v1/predictions")).json()).items.length).toBe(before + 1);
});


test("evidence-backed unscorable detail shows reason and no invented scores", async ({ page }) => {
  await login(page);
  const me = await (await page.request.get("/api/yta/v1/me")).json();
  const id = execFileSync("uv", ["run", "python", "tests/e2e/seed_unscorable.py", me.user.id], {encoding: "utf8"}).trim();
  await page.goto(`/predictions/${id}`);
  await expect(page.getByText("不可评分 · 不纳入命中率与误差分母")).toBeVisible();
  await expect(page.getByText("目标交易日停牌，无有效收盘价", {exact: true})).toBeVisible();
  await expect(page.getByText("synthetic-exchange-notice", {exact: true})).toBeVisible();
  await expect(page.getByText("实际价格收益", {exact: true})).toHaveCount(0);
  await expect(page.getByText("NaN", {exact: false})).toHaveCount(0);
  await expect(page.getByRole("button", {name: "模拟到期并结算"})).toHaveCount(0);
  await page.screenshot({path: "test-results/unscorable-desktop.png", fullPage: true});
  await page.getByRole("link", {name: "返回记录"}).click();
  await page.getByRole("combobox").selectOption("UNSCORABLE");
  await expect(page.locator("tbody tr")).toHaveCount(1);
});


test("base-price revision preserves original forecast and displays both outcome versions", async ({ page }) => {
  await page.setViewportSize({width: 1440, height: 1000});
  await login(page);
  const me = await (await page.request.get("/api/yta/v1/me")).json();
  const id = execFileSync("uv", ["run", "python", "tests/e2e/seed_base_revision.py", me.user.id], {encoding: "utf8"}).trim();
  await page.goto(`/predictions/${id}`);
  await expect(page.locator(".outcome")).toHaveCount(2);
  await expect(page.locator(".forecast-card").getByText("收盘 100 USD", {exact: true})).toBeVisible();
  await expect(page.locator(".outcome").first().getByText("2.00%", {exact: true})).toBeVisible();
  const revised = page.locator(".outcome").nth(1);
  await expect(revised.getByText("0.99%", {exact: true})).toBeVisible();
  await expect(revised.getByText("100 USD", {exact: true})).toBeVisible();
  await expect(revised.getByText("101 USD", {exact: true})).toBeVisible();
  await expect(revised.getByText("1 USD", {exact: true})).toBeVisible();
  await expect(revised.getByText("synthetic-base-101", {exact: true})).toBeVisible();
  await page.screenshot({path: "test-results/base-revision-desktop.png", fullPage: true});
});

test("older research sessions remain reachable after a failed page request", async ({ page }) => {
  await page.setViewportSize({width: 1440, height: 1000});
  await login(page);
  const me = await (await page.request.get("/api/yta/v1/me")).json();
  for (let i = 0; i < 28; i++) {
    const response = await page.request.post("/api/yta/v1/sessions", {
      headers: {Origin: new URL(page.url()).origin, "X-CSRF-Token": me.csrf_token, "Idempotency-Key": crypto.randomUUID()},
      data: {profile_id: "alpha-orchestrator"},
    });
    expect(response.status()).toBe(201);
  }
  const first = await (await page.request.get("/api/yta/v1/sessions")).json();
  const second = await (await page.request.get("/api/yta/v1/sessions?cursor=" + first.next_cursor)).json();
  await page.reload();
  await expect(page.locator(".session-list > button")).toHaveCount(25);
  let failed = false;
  await page.route("**/api/yta/v1/sessions?cursor=*", async route => {
    if (!failed) { failed = true; await route.abort("failed"); }
    else await route.continue();
  });
  await page.getByRole("button", {name: "加载更早会话"}).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.locator(".session-list > button")).toHaveCount(25);
  await page.getByRole("button", {name: "加载更早会话"}).click();
  await expect(page.locator(".session-list > button")).toHaveCount(25 + second.items.length);
  await expect(page.getByRole("button", {name: "加载更早会话"})).toHaveCount(0);
  const lastId = second.items.at(-1).session_id;
  const history = page.waitForResponse(r => r.url().includes("/sessions/" + lastId + "/messages"));
  await page.locator(".session-list > button").last().click();
  expect((await history).status()).toBe(200);
  await page.screenshot({path: "test-results/session-pagination-desktop.png", fullPage: true});
});

test("prediction history paginates, retries and filters all records", async ({ page }) => {
  await page.setViewportSize({width: 1440, height: 1000}); await login(page);
  const me = await (await page.request.get("/api/yta/v1/me")).json();
  execFileSync("uv", ["run", "python", "tests/e2e/seed_prediction_history.py", me.user.id], {encoding: "utf8"});
  const first = await (await page.request.get("/api/yta/v1/predictions")).json();
  const second = await (await page.request.get("/api/yta/v1/predictions?cursor=" + first.next_cursor)).json();
  await page.goto("/predictions");
  await expect(page.locator("tbody tr")).toHaveCount(25);
  let failed = false;
  await page.route("**/api/yta/v1/predictions?cursor=*", async route => {
    if (!failed) { failed = true; await route.abort("failed"); } else await route.continue();
  });
  await page.getByRole("button", {name: "加载更早记录"}).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.locator("tbody tr")).toHaveCount(25);
  await page.getByRole("button", {name: "加载更早记录"}).click();
  await expect(page.locator("tbody tr")).toHaveCount(25 + second.items.length);
  await expect(page.getByRole("button", {name: "加载更早记录"})).toHaveCount(0);
  const links = await page.locator("tbody a").evaluateAll(nodes => nodes.map(n => n.getAttribute("href")));
  expect(new Set(links).size).toBe(links.length);
  await page.getByLabel("状态").selectOption("UNSCORABLE");
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await page.getByRole("link", {name: "查看详情 →"}).click();
  await expect(page.getByText("不可评分 · 不纳入命中率与误差分母")).toBeVisible();
  await page.goto("/predictions");
  await page.setViewportSize({width: 375, height: 812});
  await expect(page.getByRole("button", {name: "加载更早记录"})).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path: "test-results/prediction-pagination-mobile.png", fullPage: true});
});

test("chart uses one adjusted snapshot and rejects inconsistent series before retry", async ({ page }) => {
  let invalid = true;
  let rawBarRequests = 0;
  page.on("request", request => { if (request.url().includes("/stocks/NVDA/bars")) rawBarRequests++; });
  await page.route("**/api/yta/v1/stocks/NVDA/indicators", async route => {
    const response = await route.fetch();
    if (invalid) {
      const body = await response.json(); body.sessions = body.sessions.slice(1);
      await route.fulfill({response, json: body});
    } else await route.fulfill({response});
  });
  await login(page);
  await expect(page.locator(".chart-card").getByRole("alert")).toBeVisible();
  await expect(page.locator(".chart-host canvas")).toHaveCount(0);
  invalid = false;
  await page.getByRole("button", {name: "重新加载", exact: true}).click();
  await expect(page.locator(".chart-host canvas").first()).toBeVisible();
  await expect(page.locator(".chart-source")).toContainText("拆股可比价格与成交量");
  expect(rawBarRequests).toBe(0);
});

test("NVDA overview protects access, shows provenance and works on mobile", async ({ page }) => {
  await page.goto("/"); await expect(page).toHaveURL(/\/login$/);
  await login(page);
  await page.getByRole("link", {name: "NVDA 概览", exact: true}).click();
  await expect(page.getByRole("heading", {name: "NVDA · 研究概览"})).toBeVisible();
  await expect(page.getByRole("heading", {name: "合成回放演练"})).toBeVisible();
  await expect(page.getByRole("heading", {name: "回放数据可用"})).toBeVisible();
  await expect(page.locator(".chart-source")).toContainText("synthetic-fixture");
  await expect(page.locator(".chart-source")).toContainText("拆股可比价格与成交量");
  await expect(page.locator(".chart-host canvas").first()).toBeVisible();
  await page.setViewportSize({width: 1440, height: 1000});
  await page.screenshot({path: "test-results/overview-desktop.png", fullPage: true});
  let unavailable = true;
  await page.route("**/api/yta/v1/stocks/NVDA/indicators", async route => {
    if (unavailable) await route.fulfill({status: 503, json: {error: {code: "DATA_UNAVAILABLE", message: "unavailable"}}});
    else await route.continue();
  });
  await page.reload();
  await expect(page.locator(".chart-card").getByRole("alert")).toBeVisible();
  await expect(page.locator(".chart-host canvas")).toHaveCount(0);
  await expect(page.locator(".last-price")).toContainText("—");
  unavailable = false;
  await page.getByRole("button", {name: "重新加载", exact: true}).click();
  await expect(page.locator(".chart-host canvas").first()).toBeVisible();
  await page.setViewportSize({width: 375, height: 812});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path: "test-results/overview-mobile.png", fullPage: true});
  await page.getByRole("link", {name: "进入研究工作台 →", exact: true}).click();
  await expect(page.getByRole("heading", {name: "NVDA · 价格研究"})).toBeVisible();
});

test("explicit correction preserves the original and retries a lost response with one key", async ({ page }) => {
  await page.setViewportSize({width: 1440, height: 1000}); await login(page);
  const me = await (await page.request.get("/api/yta/v1/me")).json();
  const originalId = execFileSync("uv", ["run", "python", "tests/e2e/seed_base_revision.py", me.user.id], {encoding: "utf8"}).trim();
  const before = await (await page.request.get("/api/yta/v1/predictions/" + originalId)).json();
  await page.goto("/predictions/" + originalId);
  await page.getByRole("link", {name: "研究并更正此预测 →"}).click();
  await expect(page.getByText("正在研究预测更正", {exact: true})).toBeVisible();
  await page.getByLabel("研究问题").fill("重新核对 NVDA 的五日基线");
  await page.getByRole("button", {name: "发送 →"}).click();
  await expect(page.getByText("研究已完成", {exact: true})).toBeVisible();
  const keys: string[] = []; let correctedId = ""; let lose = true;
  await page.route("**/api/yta/v1/predictions/*/corrections", async route => {
    keys.push(route.request().headers()["idempotency-key"]!);
    const response = await route.fetch();
    expect(response.status()).toBe(200);
    correctedId = (await response.json()).prediction_id;
    if (lose) { lose = false; await route.abort("failed"); } else await route.fulfill({response});
  });
  await page.getByRole("button", {name: "检查并更正预测"}).click();
  await page.getByRole("button", {name: "返回检查"}).click();
  expect(keys).toEqual([]);
  await page.getByRole("button", {name: "检查并更正预测"}).click();
  await expect(page.getByRole("dialog")).toContainText("原预测继续参与统计");
  await page.screenshot({path: "test-results/correction-confirm-desktop.png", fullPage: true});
  await page.getByRole("button", {name: "确认更正", exact: true}).click();
  await expect(page.getByRole("dialog").getByRole("alert")).toBeVisible();
  await page.getByRole("button", {name: "确认更正", exact: true}).click();
  await page.getByRole("link", {name: "查看冻结记录 →"}).click();
  expect(keys).toHaveLength(2); expect(new Set(keys).size).toBe(1);
  const corrected = await (await page.request.get("/api/yta/v1/predictions/" + correctedId)).json();
  expect(corrected.supersedes_prediction_id).toBe(originalId);
  expect(await (await page.request.get("/api/yta/v1/predictions/" + originalId)).json()).toEqual(before);
  await page.reload();
  await expect(page.getByRole("link", {name: "原预测", exact: true})).toHaveAttribute("href", "/predictions/" + originalId);
});

test("unavailable correction target cannot fall back to ordinary freezing", async ({ page }) => {
  await login(page);
  await page.goto("/research?correct=" + crypto.randomUUID());
  await page.getByLabel("研究问题").fill("核对五日基线");
  await page.getByRole("button", {name: "发送 →"}).click();
  await expect(page.getByText("研究已完成", {exact: true})).toBeVisible();
  await expect(page.getByRole("button", {name: "检查并更正预测"})).toBeDisabled();
  await expect(page.getByRole("button", {name: "检查并冻结预测"})).toHaveCount(0);
  await expect(page.locator(".forecast-card").getByRole("alert")).toBeVisible();
});

test("evaluation saves a fixed cohort, retries lost responses, and preserves null scores", async ({ page }) => {
  await login(page);
  await page.goto("/predictions");
  await page.getByRole("link", {name: "查看基线评测 →"}).click();
  await page.getByLabel("基准日开始").fill("2024-11-22");
  await page.getByLabel("基准日结束").fill("2024-11-22");
  await page.getByLabel("事实截止时间（UTC）").fill("2024-12-03T00:00");
  const bodies: unknown[] = []; const ids: string[] = []; let lose = true;
  await page.route("**/api/yta/v1/evaluation-reports", async route => {
    if (route.request().method() !== "POST") return route.continue();
    bodies.push(route.request().postDataJSON());
    const response = await route.fetch(); expect(response.ok()).toBe(true);
    ids.push((await response.json()).report_id);
    if (lose) {lose = false; await route.abort("failed");} else await route.fulfill({response});
  });
  await page.getByRole("button", {name: "生成并保存评测"}).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await page.getByRole("button", {name: "生成并保存评测"}).click();
  await expect(page.getByRole("heading", {name: "基线评测详情"})).toBeVisible();
  expect(ids).toHaveLength(2); expect(ids[0]).toBe(ids[1]); expect(bodies[0]).toEqual(bodies[1]);
  const report = await (await page.request.get("/api/yta/v1/evaluation-reports/" + ids[0])).json();
  expect(report.sample_count).toBeGreaterThan(0); expect(report.correction_count).toBeGreaterThan(0);
  expect(report.counts.UNSCORABLE).toBeGreaterThan(0); expect(report.counts.WAITING_DATA).toBeGreaterThan(0);
  await expect(page.locator("tbody tr")).toHaveCount(report.sample_count);
  await expect(page.getByText(report.content_hash, {exact: true})).toBeVisible();
  await expect(page.getByRole("link", {name: "原预测 →"}).first()).toBeVisible();
  await page.reload();
  await expect(page.getByText(report.content_hash, {exact: true})).toBeVisible();
  await page.screenshot({path: "test-results/evaluation-desktop.png", fullPage: true});
  await page.setViewportSize({width: 375, height: 812});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path: "test-results/evaluation-mobile.png", fullPage: true});
  await page.getByRole("link", {name: "← 返回评测列表"}).click();
  await page.getByLabel("基准日开始").fill("2023-01-01");
  await page.getByLabel("基准日结束").fill("2023-01-02");
  await page.getByLabel("事实截止时间（UTC）").fill("2024-12-03T00:00");
  await page.getByRole("button", {name: "生成并保存评测"}).click();
  await expect(page.getByText("暂无已评分样本，指标显示“—”，不按零分处理。")).toBeVisible();
  await expect(page.locator("tbody tr")).toHaveCount(0);
});

test("evaluation pagination keeps rows after a failed page request", async ({ page }) => {
  await login(page);
  const me = await (await page.request.get("/api/yta/v1/me")).json();
  for (let minute = 1; minute <= 26; minute++) {
    const response = await page.request.post("/api/yta/v1/evaluation-reports", {
      headers: {"X-CSRF-Token": me.csrf_token, Origin: new URL(page.url()).origin},
      data: {base_from: "2023-01-01", base_to: "2023-01-02", cutoff: `2024-12-03T00:${String(minute).padStart(2, "0")}:00Z`},
    });
    expect(response.ok()).toBe(true);
  }
  const all = await (await page.request.get("/api/yta/v1/evaluation-reports?limit=100")).json();
  await page.goto("/evaluations");
  await expect(page.locator("tbody tr")).toHaveCount(25);
  let fail = true;
  await page.route("**/api/yta/v1/evaluation-reports?cursor=*", async route => {
    if (fail) {fail = false; await route.abort("failed");} else await route.continue();
  });
  await page.getByRole("button", {name: "加载更早评测"}).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await expect(page.locator("tbody tr")).toHaveCount(25);
  await page.getByRole("button", {name: "加载更早评测"}).click();
  await expect(page.locator("tbody tr")).toHaveCount(all.items.length);
  const hrefs = await page.getByRole("link", {name: "查看评测 →"}).evaluateAll(links => links.map(link => link.getAttribute("href")));
  expect(new Set(hrefs).size).toBe(all.items.length);
});

test("evaluation rejects inconsistent summaries and wrong-mode details before rendering", async ({ page }) => {
  await login(page);
  let malformed = true;
  await page.route("**/api/yta/v1/evaluation-reports", async route => {
    const response = await route.fetch(); const body = await response.json();
    if (malformed) body.items[0].sample_count += 1;
    await route.fulfill({response, json: body});
  });
  await page.goto("/evaluations");
  await expect(page.locator(".evaluations-page").getByRole("alert")).toContainText("评测列表格式或数据模式不一致");
  await expect(page.getByRole("link", {name: "查看评测 →"})).toHaveCount(0);
  malformed = false;
  await page.getByRole("button", {name: "重新加载"}).click();
  await expect(page.getByRole("link", {name: "查看评测 →"}).first()).toBeVisible();
  let wrongMode = true;
  await page.route("**/api/yta/v1/evaluation-reports/*", async route => {
    const response = await route.fetch(); const body = await response.json();
    if (wrongMode) body.data_mode = "LIVE_RESEARCH";
    await route.fulfill({response, json: body});
  });
  await page.getByRole("link", {name: "查看评测 →"}).first().click();
  await expect(page.locator(".evaluations-page").getByRole("alert")).toContainText("评测数据格式或统计口径不一致");
  await expect(page.getByRole("heading", {name: "复现依据"})).toHaveCount(0);
  wrongMode = false;
  await page.getByRole("button", {name: "重新加载"}).click();
  await expect(page.getByRole("heading", {name: "复现依据"})).toBeVisible();
});

test("invalid evaluation save response stays on the form and can be retried", async ({ page }) => {
  await login(page); await page.goto("/evaluations");
  let malformed = true;
  await page.route("**/api/yta/v1/evaluation-reports", async route => {
    if (route.request().method() !== "POST") return route.continue();
    const response = await route.fetch(); const body = await response.json();
    if (malformed) body.mae = "0"; // Empty cohorts must have null metrics.
    await route.fulfill({response, json: body});
  });
  await page.getByLabel("基准日开始").fill("2022-01-01");
  await page.getByLabel("基准日结束").fill("2022-01-02");
  await page.getByLabel("事实截止时间（UTC）").fill("2024-12-03T00:00");
  await page.getByRole("button", {name: "生成并保存评测"}).click();
  await expect(page.locator(".evaluations-page").getByRole("alert")).toContainText("评测数据格式或统计口径不一致");
  await expect(page).toHaveURL(/\/evaluations$/);
  malformed = false;
  await page.getByRole("button", {name: "生成并保存评测"}).click();
  await expect(page.getByText("暂无已评分样本，指标显示“—”，不按零分处理。")).toBeVisible();
});

test("wrong-mode saved research report cannot display sources or enable freezing", async ({ page }) => {
  await login(page);
  const admitted = page.waitForResponse(response => response.request().method() === "POST" && /\/sessions\/[^/]+\/messages$/.test(new URL(response.url()).pathname));
  await page.getByLabel("研究问题").fill("检查研究报告恢复边界");
  await page.getByRole("button", {name: "发送 →"}).click();
  const runId = (await (await admitted).json()).run_id;
  await expect(page.getByText("研究已完成", {exact: true})).toBeVisible();
  await page.route(`**/api/yta/v1/runs/${runId}`, async route => {
    const response = await route.fetch();
    const body = await response.json();
    body.report.data_mode = "LIVE_RESEARCH";
    await route.fulfill({response, json: body});
  });
  await page.reload();
  await page.getByRole("button", {name: /NVDA 研究/}).first().click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("研究报告与当前草稿或数据模式不一致");
  await expect(page.locator("details.research-report")).toHaveCount(0);
  await expect(page.getByRole("button", {name: "检查并冻结预测"})).toHaveCount(0);
});

test("streamed report with a mismatched draft clears the freeze action", async ({ page }) => {
  await login(page);
  await page.route("**/api/yta/v1/runs/*/events", async route => {
    const response = await route.fetch();
    const body = (await response.text()).split("\n").map(line => {
      if (!line.startsWith("data: ")) return line;
      const event = JSON.parse(line.slice(6));
      if (event.type === "message.completed" && event.payload.report) {
        event.payload.report.draft_id = "00000000-0000-4000-8000-000000000087";
      }
      return "data: " + JSON.stringify(event);
    }).join("\n");
    await route.fulfill({response, body});
  });
  await page.getByLabel("研究问题").fill("检查流式报告引用边界");
  await page.getByRole("button", {name: "发送 →"}).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("研究报告与当前草稿或数据模式不一致");
  await expect(page.locator("details.research-report")).toHaveCount(0);
  await expect(page.getByRole("button", {name: "检查并冻结预测"})).toHaveCount(0);
});

test("historical evidence rejects a mismatched snapshot and supports explicit retry", async ({ page }) => {
  await login(page);
  await page.getByLabel("研究问题").fill("查看历史行情证据");
  await page.getByRole("button", {name: "发送 →"}).click();
  await expect(page.getByText("研究已完成", {exact: true})).toBeVisible();
  let mismatch = true;
  let reads = 0;
  await page.route("**/api/yta/v1/prediction-drafts/*/evidence", async route => {
    reads++;
    const response = await route.fetch(); const body = await response.json();
    if (mismatch) body.data_snapshot_hash = "f".repeat(64);
    await route.fulfill({response, json: body});
  });
  const report = page.locator("details.research-report");
  await report.getByText("研究依据与风险", {exact: true}).click();
  await report.getByRole("button", {name: "查看草稿历史行情"}).click();
  await expect(report.getByRole("alert")).toContainText("历史行情证据暂时无法读取或与报告不一致");
  await expect(report.getByRole("region", {name: "历史日线数据"})).toHaveCount(0);
  expect(reads).toBe(1);
  mismatch = false;
  await report.getByRole("button", {name: "查看草稿历史行情"}).click();
  await expect(report.getByRole("region", {name: "历史日线数据"})).toBeVisible();
  await expect(report.getByRole("alert")).toHaveCount(0);
  expect(reads).toBe(2);
});

test("overview freshness clears prior availability on failure and shows stale or unavailable explicitly", async ({ page }) => {
  await login(page);
  await page.clock.install();
  let state = "fresh";
  await page.route("**/api/yta/v1/stocks/NVDA/freshness", async route => {
    if (state === "failure") {
      await route.fulfill({status: 503, json: {error: {message: "private upstream error"}}}); return;
    }
    const response = await route.fetch(); const body = await response.json();
    if (state === "stale") { body.status = "STALE"; body.reason = "REFRESH_FAILED"; }
    if (state === "unavailable") {
      body.status = "UNAVAILABLE"; body.reason = "REFRESH_FAILED";
      body.source_id = null; body.data_snapshot_hash = null; body.as_of = null;
    }
    if (state === "wrong-mode") body.data_mode = "LIVE_RESEARCH";
    await route.fulfill({response, json: body});
  });
  await page.goto("/");
  const status = page.getByRole("region", {name: "行情可用状态"});
  await expect(status.getByRole("heading", {name: "回放数据可用"})).toBeVisible();
  state = "stale";
  await page.clock.fastForward(30000);
  await expect(status.getByRole("heading", {name: "行情已过期"})).toBeVisible();
  await expect(status).toContainText("上次有效信息截点");
  await page.screenshot({path: "test-results/freshness-stale.png", fullPage: true});
  state = "failure";
  await status.getByRole("button", {name: "检查行情状态"}).click();
  await expect(status.getByRole("heading", {name: "行情状态未知"})).toBeVisible();
  await expect(status).not.toContainText("private upstream error");
  await expect(status).not.toContainText("上次有效信息截点");
  state = "unavailable";
  await status.getByRole("button", {name: "检查行情状态"}).click();
  await expect(status.getByRole("heading", {name: "行情不可用"})).toBeVisible();
  state = "wrong-mode";
  await status.getByRole("button", {name: "检查行情状态"}).click();
  await expect(status.getByRole("heading", {name: "行情状态未知"})).toBeVisible();
  state = "fresh";
  await status.getByRole("button", {name: "检查行情状态"}).click();
  await expect(status.getByRole("heading", {name: "回放数据可用"})).toBeVisible();
});

test("paused writes preserve research and freeze identities until explicit retry", async ({page}) => {
  await login(page);
  const runKeys: string[] = [], freezeKeys: string[] = [];
  let paused = true;
  const refusal = {status: 503, contentType: "application/json", body: JSON.stringify({error: {
    code: "WRITE_ADMISSION_PAUSED", message: "写入入口暂时关闭，请稍后再试。", request_id: "paused-browser-fixture", retryable: false,
  }})};
  await page.route("**/api/yta/v1/sessions/*/messages", async route => {
    if (route.request().method() !== "POST") { await route.continue(); return; }
    runKeys.push(route.request().postDataJSON().request_id);
    if (paused) await route.fulfill(refusal); else await route.continue();
  });
  await page.getByLabel("研究问题").fill("维护结束后核对同一研究请求");
  await page.getByRole("button", {name: "发送 →"}).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("写入入口暂时关闭");
  await expect(page.getByRole("button", {name: "核对原请求"})).toBeEnabled();
  await expect(page.getByLabel("研究问题")).toBeDisabled();
  expect(runKeys).toHaveLength(1);
  paused = false;
  await page.getByRole("button", {name: "核对原请求"}).click();
  await expect(page.getByText("研究已完成", {exact: true})).toBeVisible();
  expect(runKeys).toHaveLength(2);
  expect(runKeys[0]).toBe(runKeys[1]);
  paused = true;
  await page.route("**/api/yta/v1/predictions", async route => {
    if (route.request().method() !== "POST") { await route.continue(); return; }
    freezeKeys.push(route.request().headers()["idempotency-key"]!);
    if (paused) await route.fulfill(refusal); else await route.continue();
  });
  await page.getByRole("button", {name: "检查并冻结预测"}).click();
  await page.getByRole("button", {name: "确认冻结", exact: true}).click();
  await expect(page.getByRole("dialog").getByRole("alert")).toContainText("写入入口暂时关闭");
  expect(freezeKeys).toHaveLength(1);
  paused = false;
  await page.getByRole("button", {name: "确认冻结", exact: true}).click();
  await expect(page.getByRole("link", {name: "查看冻结记录 →"})).toBeVisible();
  expect(freezeKeys).toHaveLength(2);
  expect(freezeKeys[0]).toBe(freezeKeys[1]);
});

test("Hermes structured stream displays partial summary without protocol fields", async ({page}) => {
  await page.route("**/api/yta/v1/me", async route => {
    const response = await route.fetch();
    const value = await response.json();
    await route.fulfill({response, json: {...value, runtime: "hermes"}});
  });
  await page.addInitScript(() => {
    const streams: EventTarget[] = [];
    class ControlledSource extends EventTarget {
      onopen: (() => void) | null = null;
      onerror: (() => void) | null = null;
      constructor(_url: string) { super(); streams.push(this); }
      close() {}
    }
    Object.assign(window, {EventSource: ControlledSource, __reportStreams: streams});
  });
  await login(page);
  await page.getByLabel("研究问题").fill("结构化摘要展示测试");
  const admitted = page.waitForResponse(r => r.request().method() === "POST" && /\/sessions\/[^/]+\/messages$/.test(new URL(r.url()).pathname));
  await page.getByRole("button", {name:"发送 →"}).click();
  const response = await admitted, runId = (await response.json()).run_id;
  const sessionId = new URL(response.url()).pathname.split("/").at(-2)!;
  await expect.poll(() => page.evaluate(() => (window as unknown as {__reportStreams: EventTarget[]}).__reportStreams.length)).toBeGreaterThan(0);
  let sequence = 0;
  const emit = async (type: string, payload: unknown) => {
    sequence++;
    await page.evaluate(event => {
      const target = (window as unknown as {__reportStreams: EventTarget[]}).__reportStreams.at(-1)!;
      target.dispatchEvent(new MessageEvent(event.type, {data: JSON.stringify(event)}));
    }, {event_id: crypto.randomUUID(), schema_version: 1, session_id: sessionId, run_id: runId,
      profile_id: "alpha-orchestrator", sequence, occurred_at: new Date().toISOString(), type, payload});
  };
  await emit("run.started", {});
  await emit("message.delta", {text:'{"schema_version":"research-report-v1","evidence":[{"tool_call_id":"hidden-reference"}],"summary":"逐步显示'});
  await expect(page.locator(".message.assistant")).toContainText("逐步显示");
  await expect(page.locator(".message.assistant")).not.toContainText("schema_version");
  await expect(page.locator(".message.assistant")).not.toContainText("hidden-reference");
  await emit("message.delta", {text:'的摘要\\n和风险说明。"}'});
  await expect(page.locator(".message.assistant")).toContainText("逐步显示的摘要\n和风险说明。");
  await expect(page.getByRole("button", {name:"检查并冻结预测"})).toHaveCount(0);
  let saved: {status:string;draft:unknown;report:{summary:string}};
  await expect.poll(async () => {saved = await (await page.request.get(`/api/yta/v1/runs/${runId}`)).json();return saved.status;}).toBe("COMPLETED");
  await emit("prediction.draft", saved!.draft);
  await emit("message.completed", {text:saved!.report.summary, report:saved!.report});
  await emit("run.completed", {});
  await expect(page.locator(".message.assistant")).toContainText(saved!.report.summary);
  await expect(page.getByText("研究已完成", {exact:true})).toBeVisible();
});
