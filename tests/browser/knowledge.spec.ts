import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { seedKnowledge } from "../support/knowledge-fixture.js";

test("knowledge base archives lessons and research summaries on desktop and mobile", async ({
  page,
  context,
  baseURL,
}, testInfo) => {
  test.setTimeout(60000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const email = `${randomUUID()}@knowledge.test`;
  const registered = await context.request.post("/api/auth/register", {
    headers: { Origin: baseURL! },
    data: { email, password: "knowledge-test-password" },
  });
  expect(registered.status()).toBe(201);
  const seed = await seedKnowledge(email);
  await page.goto("/knowledge");
  await expect(page.locator(".kb-bar")).toBeVisible();
  await expect(page.locator(".topbar-title")).toHaveText("知识沉淀");
  await expect(
    page.getByRole("link", { name: "知识沉淀", exact: true }),
  ).toHaveAttribute("aria-current", "page");
  const reader = page.locator(".kb-reader");
  // Knowledge is shared, so only this run's tagged documents are asserted.
  await expect(
    page.locator(".kb-doc").filter({ hasText: seed.tag }),
  ).toHaveCount(3);
  // Research summaries nest under one folder per stock symbol.
  const primaryFolder = page.getByRole("button", {
    name: new RegExp(`^${seed.primary}\\s*1$`),
  });
  await expect(
    page.getByRole("button", { name: new RegExp(`^${seed.secondary}\\s*1$`) }),
  ).toBeVisible();
  await expect(primaryFolder).toHaveAttribute("aria-expanded", "true");
  await primaryFolder.click();
  await expect(primaryFolder).toHaveAttribute("aria-expanded", "false");
  await expect(
    page.getByRole("button", { name: new RegExp(`${seed.primary} 研究摘要`) }),
  ).toHaveCount(0);
  await primaryFolder.click();
  await expect(
    page.getByRole("button", { name: new RegExp(`${seed.primary} 研究摘要`) }),
  ).toBeVisible();
  await expect(
    page.getByText("无研究上下文的摘要，不应出现在知识库。"),
  ).toHaveCount(0);
  await page.getByRole("button", { name: new RegExp(seed.lessonTitle) }).click();
  await expect(
    reader.getByRole("heading", { name: new RegExp(seed.lessonTitle) }),
  ).toBeVisible();
  await expect(
    reader.getByText("指引上调且公告后首个交易日成交量高于 20 日均量。"),
  ).toBeVisible();
  await expect(reader.locator(".kb-progress")).toHaveText(
    /0\s*\/\s*30 组独立事件/,
  );
  await page
    .getByRole("button", { name: new RegExp(`${seed.primary} 研究摘要`) })
    .click();
  await expect(
    reader.getByText("合成研究摘要：证据支持温和上涨，需关注指引变化。"),
  ).toBeVisible();
  // Structured parsing: 一、/二、 sections become headings, not one wall of text.
  await expect(page.locator(".kb-h3")).toHaveCount(2);
  await expect(page.locator(".kb-h3").first()).toHaveText("一、研究背景");
  await expect(page.locator(".kb-list li")).toHaveCount(2);
  await expect(
    page.locator(".kb-outline li").first(),
  ).toHaveText("研究背景");
  expect(await page.locator(".kb-new").count()).toBeGreaterThan(0);
  await page.screenshot({
    path: testInfo.outputPath("knowledge-desktop.png"),
    fullPage: true,
  });
  // Starting a search expands folders so matches are never hidden.
  await page.getByRole("button", { name: /^个股研究/ }).click();
  await expect(
    page.getByRole("button", { name: new RegExp(`${seed.primary} 研究摘要`) }),
  ).toHaveCount(0);
  await page.getByLabel("搜索知识").fill(seed.primary);
  await expect(
    page.getByRole("button", { name: new RegExp(`${seed.primary} 研究摘要`) }),
  ).toBeVisible();
  await page.getByLabel("搜索知识").fill("");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("knowledge-mobile.png"),
    fullPage: true,
  });
  expect(errors).toEqual([]);
});
