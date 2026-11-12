import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { seedForecasts } from "../support/forecast-fixture.js";

test("research pool, settled forecasts, evidence and settings survive reload on desktop and mobile", async ({
  page,
  context,
  baseURL,
}, testInfo) => {
  test.setTimeout(60000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const email = `${randomUUID()}@hub.test`;
  const registered = await context.request.post("/api/auth/register", {
    headers: { Origin: baseURL! },
    data: { email, password: "research-hub-test-password" },
  });
  expect(registered.status()).toBe(201);
  await seedForecasts(email);
  await page.goto("/lab");
  await expect(page.getByRole("heading", { name: "我的研究池" })).toBeVisible();
  await page.getByRole("textbox", { name: "添加跟踪股票代码" }).fill("NVDA");
  await page.getByRole("button", { name: "加入自选", exact: true }).click();
  const row = page
    .getByRole("row")
    .filter({ has: page.getByRole("button", { name: "NVDA ↗", exact: true }) });
  await expect(row).toContainText("持续跟踪");
  await row.getByRole("button", { name: "暂停", exact: true }).click();
  await expect(row).toContainText("已暂停");
  await page.reload();
  await expect(row).toContainText("已暂停");
  await row.getByRole("button", { name: "恢复", exact: true }).click();
  await expect(row).toContainText("持续跟踪");
  await row.getByRole("button", { name: "NVDA ↗", exact: true }).click();
  await expect(page.getByRole("link", { name: "测试公司公告" })).toBeVisible();
  await page.getByRole("button", { name: "关闭详情" }).click();
  await page.getByRole("button", { name: "预测记录", exact: true }).click();
  await expect(page.locator(".forecast-ticket")).toHaveCount(2);
  await page
    .getByRole("combobox", { name: "预测期限", exact: true })
    .selectOption("5");
  await expect(page.locator(".forecast-ticket")).toHaveCount(1);
  await expect(page.locator(".forecast-ticket")).toContainText(
    "已验证 · 实际 6.0%",
  );
  await page.getByRole("button", { name: "依据与结果 ↗" }).click();
  await expect(
    page.getByRole("heading", { name: "到期验证", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("落在区间内", { exact: true })).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("forecast-desktop.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "关闭详情" }).click();
  await page.getByRole("button", { name: "研究设置", exact: true }).click();
  await page.getByLabel("每日后台深研股票上限").fill("15");
  await page.getByLabel("其中新候选上限").fill("4");
  await page.getByLabel("启用后台持续研究").uncheck();
  await page.getByRole("button", { name: "保存研究设置" }).click();
  await expect(page.getByRole("status")).toContainText("研究设置已保存");
  await page.reload();
  await page.getByRole("button", { name: "研究设置", exact: true }).click();
  await expect(page.getByLabel("每日后台深研股票上限")).toHaveValue("15");
  await expect(page.getByLabel("启用后台持续研究")).not.toBeChecked();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "预测记录", exact: true }).click();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("forecast-mobile.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "研究池", exact: true }).click();
  await row.getByRole("button", { name: "移除", exact: true }).click();
  await expect(row).toHaveCount(0);
  await page.getByRole("button", { name: "预测记录", exact: true }).click();
  await expect(page.locator(".forecast-ticket")).toHaveCount(2);
  expect(errors).toEqual([]);
});
