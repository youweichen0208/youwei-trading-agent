// Explicit real-provider production acceptance. Uses a clearly labelled simulated portfolio.
import assert from "node:assert/strict";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { setTimeout as delay } from "node:timers/promises";
import { chromium, request, expect } from "@playwright/test";

const origin = "https://trading.youwei-agent.com";
const login = JSON.parse(await readFile(process.env.ALPHA_LOGIN_FILE, "utf8"));
const output = process.env.ALPHA_BROWSER_OUTPUT;
assert.ok(output);
await mkdir(output, { recursive: true, mode: 0o700 });
let browser = await chromium.launch({ channel: "chrome" });
let api;
const errors = [];
try {
  let context = await browser.newContext({
    baseURL: origin,
    viewport: { width: 1440, height: 1000 },
  });
  let page = await context.newPage();
  page.setDefaultTimeout(30_000);
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByLabel("邮箱", { exact: true }).fill(login.email);
  await page.getByLabel("密码", { exact: true }).fill(login.password);
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "进入工作台", exact: true })
    .click();
  await page.getByRole("button", { name: "编辑模拟持仓 ↗" }).click();
  await page.getByLabel("持仓日期").fill("2025-08-01");
  await page.getByLabel("股票与数量").fill("NVDA 2");
  await page.getByLabel("美元现金").fill("100");
  await page.getByRole("button", { name: "保存新快照" }).click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await page.getByRole("button", { name: "＋ 新建研究" }).click();
  await page
    .getByRole("textbox", { name: "研究问题" })
    .fill(
      "部署验收（模拟持仓，不是真实账户）：Analyze NVDA from 2025-01-02 to 2025-08-01. " +
        "Include price chart and MA20/RSI14, ask the prediction specialist for the fixed statistical forecast " +
        "and historical evaluation, and ask the portfolio specialist to value my selected simulated portfolio " +
        "as of its date using RAW prices for that exact date (fetch its own raw dataset; do not reuse split prices). " +
        "Publish all five corresponding Canvas components. Summarize saved evidence in Chinese, label IEX-only " +
        "market coverage and the simulated portfolio. Do not execute trades.",
    );
  const response = page.waitForResponse(
    (r) =>
      r.request().method() === "POST" &&
      new URL(r.url()).pathname === "/api/research",
  );
  await page.getByRole("button", { name: "发送研究" }).click();
  const submitted = await response;
  assert.equal(submitted.status(), 202);
  const { taskId, threadId } = await submitted.json();
  const state = await context.storageState();
  const initial = await (
    await context.request.get(`/api/tasks/${taskId}`)
  ).json();
  assert.ok(["queued", "running", "waiting"].includes(initial.task.status));
  await browser.close();
  console.log(
    JSON.stringify({ taskId, threadId, browserClosedDuringTask: true }),
  );
  api = await request.newContext({ baseURL: origin, storageState: state });
  let snapshot;
  const deadline = Date.now() + 720_000;
  while (Date.now() < deadline) {
    const result = await api.get(`/api/tasks/${taskId}`);
    assert.equal(result.status(), 200);
    snapshot = await result.json();
    if (
      ["completed", "failed", "cancelled", "interrupted"].includes(
        snapshot.task.status,
      )
    )
      break;
    await delay(3000);
  }
  await writeFile(
    `${output}/research-result.json`,
    JSON.stringify(snapshot, null, 2) + "\n",
    { mode: 0o600 },
  );
  assert.equal(
    snapshot.task.status,
    "completed",
    snapshot.task.failure_code ?? "Research did not complete",
  );
  assert.deepEqual(
    [...new Set(snapshot.artifacts.map((a) => a.component))].sort(),
    [
      "stock_chart",
      "indicator_panel",
      "research_summary",
      "prediction_result",
      "portfolio_summary",
    ].sort(),
  );
  assert.ok(snapshot.task.stream_text.length > 20);
  browser = await chromium.launch({ channel: "chrome" });
  context = await browser.newContext({
    baseURL: origin,
    storageState: state,
    viewport: { width: 1440, height: 1000 },
  });
  page = await context.newPage();
  page.setDefaultTimeout(30_000);
  page.on("pageerror", (error) => errors.push(error.message));
  let resubmissions = 0;
  page.on("request", (r) => {
    if (r.method() === "POST" && new URL(r.url()).pathname === "/api/research")
      resubmissions++;
  });
  await page.goto("/");
  await page
    .getByRole("navigation", { name: "研究记录" })
    .getByRole("button")
    .first()
    .click();
  await page.locator(".canvas-toggle").click();
  await expect(page.locator(".artifact")).toHaveCount(5, { timeout: 30_000 });
  await page.reload();
  await page
    .getByRole("navigation", { name: "研究记录" })
    .getByRole("button")
    .first()
    .click();
  await page.locator(".canvas-toggle").click();
  await expect(page.locator(".artifact")).toHaveCount(5, { timeout: 30_000 });
  await page.screenshot({ path: `${output}/desktop.png`, fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "收起画布" }).click();
  await expect(page.getByRole("textbox", { name: "研究问题" })).toBeVisible();
  await page.screenshot({ path: `${output}/mobile.png`, fullPage: true });
  assert.equal(resubmissions, 0);
  assert.deepEqual(errors, []);
  const evidence = {
    origin,
    taskId,
    threadId,
    status: snapshot.task.status,
    realModel: true,
    realMarket: true,
    browserClosedDuringTask: true,
    restoredFiveComponents: true,
    resubmissions,
    errors,
    checkedAt: new Date().toISOString(),
  };
  await writeFile(
    `${output}/evidence.json`,
    JSON.stringify(evidence, null, 2) + "\n",
    { mode: 0o600 },
  );
  console.log(JSON.stringify(evidence));
} finally {
  await api?.dispose();
  await browser.close();
}
