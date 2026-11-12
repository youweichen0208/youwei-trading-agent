import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { completeFixtureResearch } from "../support/research-fixture.js";

test("closing the browser during research preserves background completion and restores all results", async ({
  page,
  context,
  browser,
  baseURL,
}) => {
  test.setTimeout(60000);
  const email = `${randomUUID()}@closed-browser.test`;
  await page.goto("/");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByRole("button", { name: "还没有账户？注册" }).click();
  await page.getByLabel("邮箱", { exact: true }).fill(email);
  await page
    .getByLabel("密码", { exact: true })
    .fill("closed-browser-test-password");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "创建账户", exact: true })
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
    .fill("关闭页面后继续完成研究");
  const submitted = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === "/api/research",
  );
  await page.getByRole("button", { name: "发送研究" }).click();
  const { taskId } = await (await submitted).json();
  const savedSession = await context.storageState();
  let closedBeforeCompletion = false;
  const root = await completeFixtureResearch(email, async () => {
    await page.locator(".canvas-toggle").click();
    await expect(page.locator(".artifact")).toHaveCount(1);
    const current = await (
      await context.request.get(`/api/tasks/${taskId}`)
    ).json();
    expect(current.task.status).toBe("running");
    expect(current.artifacts).toHaveLength(1);
    await context.close();
    expect(page.isClosed()).toBe(true);
    closedBeforeCompletion = true;
    // The fixture's remaining real tools/Quant/store work executes only after
    // this callback, while every page in this user's old context is closed.
  });
  expect(closedBeforeCompletion).toBe(true);
  expect(root.id).toBe(taskId);
  const restored = await browser.newContext({
    storageState: savedSession,
    baseURL,
  });
  try {
    const snapshot = await (
      await restored.request.get(`/api/tasks/${taskId}`)
    ).json();
    expect(snapshot.task.status).toBe("completed");
    expect(snapshot.artifacts).toHaveLength(5);
    const reopened = await restored.newPage();
    let newSubmissions = 0;
    const errors: string[] = [];
    reopened.on("request", (request) => {
      if (
        request.method() === "POST" &&
        new URL(request.url()).pathname === "/api/research"
      )
        newSubmissions++;
    });
    reopened.on("pageerror", (error) => errors.push(error.message));
    await reopened.goto("/");
    await reopened
      .getByRole("navigation", { name: "研究记录" })
      .getByRole("button")
      .first()
      .click();
    await expect(
      reopened.getByText("五类产物已保存（测试数据）。", { exact: true }),
    ).toBeVisible();
    await reopened.locator(".canvas-toggle").click();
    await expect(reopened.locator(".artifact")).toHaveCount(5);
    expect(newSubmissions).toBe(0);
    expect(errors).toEqual([]);
  } finally {
    await restored.close();
  }
});

test("live Canvas, offline auto reconnect, five components and durable restore", async ({
  page,
  context,
  browser,
  baseURL,
}) => {
  test.setTimeout(60000);
  const errors: string[] = [];
  let submissions = 0;
  page.on("request", (req) => {
    if (
      req.method() === "POST" &&
      new URL(req.url()).pathname === "/api/research"
    )
      submissions++;
  });
  page.on("pageerror", (e) => errors.push(e.message));
  const email = `${randomUUID()}@canvas.test`;
  await page.goto("/");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByRole("button", { name: "还没有账户？注册" }).click();
  await page.getByLabel("邮箱", { exact: true }).fill(email);
  await page.getByLabel("密码", { exact: true }).fill("canvas-test-password");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "创建账户", exact: true })
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
    .fill("验证五类研究产物");
  await page.getByRole("button", { name: "发送研究" }).click();
  await expect(page.getByRole("button", { name: "停止任务" })).toBeVisible();
  const root = await completeFixtureResearch(email, async (appendText) => {
    await page.locator(".canvas-toggle").click();
    await expect(
      page.getByRole("heading", { name: "NVDA 日线（测试数据）" }),
    ).toBeVisible();
    await expect(
      page.getByRole("img", { name: "历史收盘价走势" }),
    ).toBeVisible();
    await expect(
      page.getByText("已保存研究前半段", { exact: true }),
    ).toBeVisible();
    await page.reload();
    await page
      .getByRole("navigation", { name: "研究记录" })
      .getByRole("button")
      .first()
      .click();
    await expect(
      page.getByText("已保存研究前半段", { exact: true }),
    ).toBeVisible();
    await appendText();
    await expect(
      page.getByText("已保存研究前半段，继续研究后半段", { exact: true }),
    ).toBeVisible();
    await context.setOffline(true);
    await expect(
      page.getByText("连接正在恢复，任务继续在后台运行。"),
    ).toBeVisible({ timeout: 20000 });
  });
  await context.setOffline(false);
  await expect(
    page.getByText("五类产物已保存（测试数据）。", { exact: true }),
  ).toBeVisible({ timeout: 15000 });
  await page.locator(".canvas-toggle").click();
  await expect(page.locator(".artifact")).toHaveCount(5);
  await expect(
    page.getByText("连接正在恢复，任务继续在后台运行。"),
  ).not.toBeVisible();
  expect(submissions).toBe(1);
  await page.close();
  const restored = await context.newPage();
  restored.on("pageerror", (e) => errors.push(e.message));
  await restored.goto("/");
  await restored
    .getByRole("navigation", { name: "研究记录" })
    .getByRole("button")
    .first()
    .click();
  await expect(
    restored.getByText("五类产物已保存（测试数据）。", { exact: true }),
  ).toBeVisible();
  await restored.locator(".canvas-toggle").click();
  await expect(restored.locator(".artifact")).toHaveCount(5);
  await expect(
    restored.getByRole("img", { name: "MA20 和 RSI14 指标" }),
  ).toBeVisible();
  await expect(restored.getByText("探索性统计基线 · 回溯评估")).toBeVisible();
  await expect(
    restored.getByRole("heading", { name: "模拟组合" }),
  ).toBeVisible();
  await expect(restored.locator(".artifact canvas").first()).toBeVisible();
  await restored.screenshot({
    path: "test-results/canvas-desktop.png",
    fullPage: true,
  });
  await restored
    .getByRole("button", { name: "下移 NVDA 日线（测试数据）" })
    .click();
  await expect(restored.locator(".artifact h3").first()).toHaveText(
    "MA20 与 RSI14",
  );
  await restored.reload();
  await restored
    .getByRole("navigation", { name: "研究记录" })
    .getByRole("button")
    .first()
    .click();
  await restored.locator(".canvas-toggle").click();
  await expect(restored.locator(".artifact h3").first()).toHaveText(
    "MA20 与 RSI14",
  );
  await restored.getByText(/任务记录 ·/).click();
  await restored.getByRole("button", { name: "查看此轮产物" }).click();
  await expect(restored.getByText("正在查看历史产物")).toBeVisible();
  await restored.getByRole("button", { name: "返回当前画布" }).click();
  await expect(restored.getByText("正在查看历史产物")).not.toBeVisible();
  await restored.setViewportSize({ width: 390, height: 844 });
  expect(
    await restored.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await restored.screenshot({
    path: "test-results/canvas-mobile.png",
    fullPage: true,
  });
  const other = await browser.newContext({ baseURL });
  try {
    const registration = await other.request.post("/api/auth/register", {
      headers: { Origin: baseURL! },
      data: {
        email: `${randomUUID()}@isolation.test`,
        password: "isolation-test-password",
      },
    });
    expect(registration.ok()).toBe(true);
    const otherPage = await other.newPage();
    await otherPage.goto("/");
    await expect(otherPage.getByText("尚无研究记录")).toBeVisible();
    const ownSnapshot = await (
      await context.request.get(`/api/tasks/${root.id}`)
    ).json();
    for (const path of [
      `tasks/${root.id}`,
      `threads/${root.thread_id}`,
      `workspaces/${root.workspace_id}`,
      `datasets/${ownSnapshot.artifacts.find((a: any) => a.datasetRef).datasetRef}`,
      `results/${ownSnapshot.artifacts.find((a: any) => a.resultRef).resultRef}`,
    ])
      expect((await other.request.get(`/api/${path}`)).status()).toBe(404);
  } finally {
    await other.close();
  }
  expect(errors).toEqual([]);
});
