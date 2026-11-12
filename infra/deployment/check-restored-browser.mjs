// Resume a browser-only acceptance after the recorded live research completed.
// Read-only: does not submit another model request or create another portfolio.
import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import { chromium, expect } from "@playwright/test";

const origin = "https://trading.youwei-agent.com";
const output = process.env.ALPHA_BROWSER_OUTPUT;
assert.ok(output && process.env.ALPHA_PREVIOUS_BROWSER_LOG);
const login = JSON.parse(await readFile(process.env.ALPHA_LOGIN_FILE, "utf8"));
const prior = JSON.parse(
  (await readFile(process.env.ALPHA_PREVIOUS_BROWSER_LOG, "utf8")).split(
    "\n",
  )[0],
);
assert.equal(prior.browserClosedDuringTask, true);
const completed = JSON.parse(
  await readFile(`${output}/research-result.json`, "utf8"),
);
assert.equal(completed.task.id, prior.taskId);
assert.equal(completed.task.status, "completed");
const browser = await chromium.launch({
  channel: "chrome",
  args: ["--no-proxy-server"],
});
try {
  const context = await browser.newContext({
    baseURL: origin,
    viewport: { width: 1440, height: 1000 },
  });
  const page = await context.newPage();
  page.setDefaultTimeout(30_000);
  const errors = [];
  let resubmissions = 0;
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("request", (r) => {
    if (r.method() === "POST" && new URL(r.url()).pathname === "/api/research")
      resubmissions++;
  });
  await page.goto("/");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByLabel("邮箱", { exact: true }).fill(login.email);
  await page.getByLabel("密码", { exact: true }).fill(login.password);
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "进入工作台", exact: true })
    .click();
  const select = async () => {
    const selected = page.waitForResponse(
      (r) => new URL(r.url()).pathname === `/api/threads/${prior.threadId}`,
    );
    await page
      .getByRole("navigation", { name: "研究记录" })
      .getByRole("button")
      .first()
      .click();
    assert.equal((await selected).status(), 200);
    await expect(page.locator(".canvas-panel")).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: /研究画布，5 项结果/ }),
    ).toBeVisible();
    await page.screenshot({
      path: `${output}/conversation-desktop.png`,
      fullPage: true,
    });
    await page.getByRole("button", { name: /研究画布，5 项结果/ }).click();
    await expect(page.locator(".artifact")).toHaveCount(5, { timeout: 30_000 });
  };
  await expect(
    page.getByRole("heading", { name: "今天想研究哪只股票？" }),
  ).toBeVisible();
  await expect(
    page
      .getByRole("navigation", { name: "研究记录" })
      .getByRole("button")
      .first(),
  ).toBeVisible();
  await expect(page.locator(".canvas-panel")).toHaveCount(0);
  await page.screenshot({
    path: `${output}/empty-desktop.png`,
    fullPage: true,
  });
  await select();
  await page.reload();
  await select();
  const latest = await (
    await context.request.get(`/api/tasks/${prior.taskId}`)
  ).json();
  assert.equal(latest.task.status, "completed");
  assert.equal(latest.task.stream_text, completed.task.stream_text);
  assert.equal(latest.artifacts.length, 5);
  await expect(page.locator(".quote-symbol")).toHaveText("NVDA");
  await expect(page.getByRole("img", { name: "历史收盘价走势" })).toBeVisible();
  await page
    .getByRole("navigation", { name: "研究结果导航" })
    .getByRole("button", { name: "行情", exact: true })
    .click();
  await expect(
    page.locator(".quote-metrics > div").nth(1).locator("strong"),
  ).not.toHaveText("—");
  await expect(
    page.locator(".quote-metrics > div").nth(2).locator("strong"),
  ).not.toHaveText("—");
  await page.screenshot({ path: `${output}/desktop.png`, fullPage: true });
  await page
    .getByRole("textbox", { name: "研究问题" })
    .fill("继续分析这项研究");
  await page.getByRole("button", { name: "放大画布" }).click();
  await expect(page.locator(".conversation-column")).not.toBeVisible();
  await page.getByRole("button", { name: "还原画布" }).click();
  await page.getByRole("button", { name: "收起画布" }).click();
  await expect(page.getByRole("textbox", { name: "研究问题" })).toHaveValue(
    "继续分析这项研究",
  );
  await page.getByRole("textbox", { name: "研究问题" }).fill("");
  await page.locator(".canvas-toggle").click();
  await page.getByRole("button", { name: "1M", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "1M", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "K 线", exact: true }).click();
  await expect(page.getByRole("img", { name: "历史价格 K 线" })).toBeVisible();
  await page.getByRole("button", { name: "走势", exact: true }).click();
  await page.getByRole("button", { name: "ALL", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator(".conversation-column")).not.toBeVisible();
  await page.getByRole("button", { name: "收起画布" }).click();
  await expect(page.getByRole("textbox", { name: "研究问题" })).toBeVisible();
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    true,
  );
  await page.getByRole("button", { name: "研究记录与持仓 ＋" }).click();
  await page.getByRole("button", { name: "编辑模拟持仓 ↗" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await page.getByRole("button", { name: "研究记录与持仓 −" }).click();
  await page
    .getByRole("button", { name: /研究画布，5 项结果/ })
    .scrollIntoViewIfNeeded();
  await page.screenshot({
    path: `${output}/conversation-mobile.png`,
    fullPage: true,
  });
  await page.locator(".canvas-toggle").click();
  await expect(page.getByRole("img", { name: "历史收盘价走势" })).toBeVisible();
  await expect(page.getByText("读取研究结果…", { exact: true })).toHaveCount(0);
  await page
    .getByRole("navigation", { name: "研究结果导航" })
    .getByRole("button", { name: "行情", exact: true })
    .click();
  await expect(page.locator(".quote-symbol")).toBeInViewport();
  await page.screenshot({ path: `${output}/mobile.png`, fullPage: true });
  assert.equal(resubmissions, 0);
  assert.deepEqual(errors, []);
  const evidence = {
    origin,
    ...prior,
    status: latest.task.status,
    realModel: true,
    realMarket: true,
    restoredFiveComponents: true,
    canvasCollapsedUntilOpened: true,
    draftPreservedAfterCanvasToggle: true,
    refreshThenSelectHistory: true,
    resumedBrowserCheck: true,
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
  await browser.close();
}
