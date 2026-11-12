import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";
test("register, research, cancel, refresh, holdings and mobile layout", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByRole("button", { name: "还没有账户？注册" }).click();
  await page
    .getByLabel("邮箱", { exact: true })
    .fill(`${randomUUID()}@browser.test`);
  await page.getByLabel("密码", { exact: true }).fill("browser-test-password");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "创建账户", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "今天想研究哪只股票？" }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/workspace-desktop.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "＋ 新建研究" }).click();
  await page
    .getByRole("textbox", { name: "研究问题" })
    .fill("分析 NVDA 最近三个月走势");
  await page.getByRole("button", { name: "发送研究" }).click();
  await expect(page.getByRole("button", { name: "停止任务" })).toBeVisible();
  await page.getByRole("button", { name: "停止任务" }).click();
  await expect(
    page.getByRole("button", { name: "停止任务" }),
  ).not.toBeVisible();
  await page.reload();
  await page
    .getByRole("navigation", { name: "研究记录" })
    .getByRole("button")
    .first()
    .click();
  await expect(
    page.getByText("分析 NVDA 最近三个月走势", { exact: true }).last(),
  ).toBeVisible();
  await page.getByRole("button", { name: "编辑模拟持仓 ↗" }).click();
  await page.getByLabel("持仓日期").fill("2025-07-03");
  await page.getByLabel("股票与数量").fill("NVDA 10\nAAPL 2");
  await page.getByLabel("美元现金").fill("500");
  await page.getByRole("button", { name: "保存新快照" }).click();
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await expect(page.getByRole("combobox")).not.toHaveValue("");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("textbox", { name: "研究问题" })).toBeVisible();
  await page.getByRole("button", { name: "研究记录与持仓 ＋" }).click();
  await expect(
    page.getByRole("button", { name: "编辑模拟持仓 ↗" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "研究记录与持仓 −" }).click();
  await page.screenshot({
    path: "test-results/workspace-mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  expect(errors).toEqual([]);
});
