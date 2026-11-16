import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";

test("personal notebook saves, survives reload, edits and deletes", async ({
  page,
  context,
  baseURL,
}, testInfo) => {
  test.setTimeout(60000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const email = `${randomUUID()}@notes.test`;
  const registered = await context.request.post("/api/auth/register", {
    headers: { Origin: baseURL! },
    data: { email, password: "notes-test-password" },
  });
  expect(registered.status()).toBe(201);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/notes");
  await expect(page.locator(".topbar-title")).toHaveText("个人笔记");
  await expect(
    page.getByRole("link", { name: "个人笔记", exact: true }),
  ).toHaveAttribute("aria-current", "page");
  await expect(page.getByText(/还没有笔记/).first()).toBeVisible();
  await page.getByRole("button", { name: "＋ 新建笔记" }).first().click();
  await page.getByLabel("笔记标题").fill("我的投资风格");
  await page
    .getByLabel("笔记正文")
    .fill("偏好现金流稳定的成长股，回避高杠杆。");
  await page.getByLabel("关注股票").fill("nvda");
  await page.getByLabel("笔记标签").fill("估值，风险，估值");
  await page.getByRole("checkbox", { name: "置顶" }).check();
  const reference = page.getByRole("checkbox", {
    name: "让 Hermes 在分析时参考",
  });
  await expect(reference).toBeChecked();
  await page.getByRole("button", { name: "保存笔记" }).click();
  const items = page.locator(".notes-item");
  await expect(items).toHaveCount(1);
  await expect(items.first()).toContainText("置顶 · 我的投资风格");
  await expect(items.first()).toContainText("参考中");
  await expect(items.first()).toContainText("NVDA");
  // The server trims and de-duplicates tags; the editor shows the stored value.
  await expect(page.getByLabel("笔记标签")).toHaveValue("估值，风险");
  await page.screenshot({
    path: testInfo.outputPath("notes-desktop.png"),
    fullPage: true,
  });
  await page.reload();
  await expect(items).toHaveCount(1);
  await items.first().click();
  await expect(page.getByLabel("笔记标题")).toHaveValue("我的投资风格");
  await expect(page.getByLabel("笔记正文")).toHaveValue(
    "偏好现金流稳定的成长股，回避高杠杆。",
  );
  await page.getByLabel("笔记标题").fill("我的投资风格（修订）");
  await page.getByRole("button", { name: "保存笔记" }).click();
  await expect(items.first()).toContainText("我的投资风格（修订）");
  await page.reload();
  await expect(items.first()).toContainText("我的投资风格（修订）");
  await items.first().click();
  await expect(page.getByLabel("笔记标题")).toHaveValue(
    "我的投资风格（修订）",
  );
  page.on("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "删除笔记" }).click();
  await expect(items).toHaveCount(0);
  await expect(page.getByText(/还没有笔记/).first()).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "＋ 新建笔记" }).first().click();
  await expect(page.locator(".notes-body")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.addStyleTag({
    content: ".app .sidebar,.app .nav-scrim{display:none !important}",
  });
  await page.screenshot({ path: testInfo.outputPath("notes-mobile.png") });
  expect(errors).toEqual([]);
});
