import { test, expect } from "@playwright/test";

// Visual/interaction seam only. Real authentication is separately checked on the deployment.
test.beforeEach(async ({ page }) => {
  await page.route("**/api/auth/session", (route) =>
    route.fulfill({ status: 401, json: { error: "unauthorized" } }),
  );
});

test("reference landing, keyboard modal, registration and failed login retain input", async ({
  page,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "研究，洞察，进化" }),
  ).toBeVisible();
  await expect(page.locator(".entry-feature-grid article")).toHaveCount(6);
  await page.screenshot({
    path: testInfo.outputPath("login-desktop.png"),
    fullPage: true,
  });
  const login = page.getByRole("button", { name: "登录", exact: true });
  await login.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(page.getByLabel("邮箱", { exact: true })).toBeFocused();
  await page.getByLabel("邮箱", { exact: true }).fill("sample@example.com");
  await page.getByLabel("密码", { exact: true }).fill("example-password");
  await page.getByRole("button", { name: "显示密码", exact: true }).click();
  await expect(page.getByLabel("密码", { exact: true })).toHaveAttribute(
    "type",
    "text",
  );
  await page.route("**/api/auth/login", (route) =>
    route.fulfill({ status: 401, json: { error: "invalid_credentials" } }),
  );
  await dialog.getByRole("button", { name: "进入工作台", exact: true }).click();
  await expect(dialog.getByRole("alert")).toHaveText("邮箱或密码不正确");
  await expect(page.getByLabel("邮箱", { exact: true })).toHaveValue(
    "sample@example.com",
  );
  await page.getByRole("button", { name: "还没有账户？注册" }).click();
  await expect(dialog.getByRole("heading")).toHaveText("开始你的研究旅程");
  await expect(page.getByLabel("密码", { exact: true })).toHaveAttribute(
    "minlength",
    "12",
  );
  await expect(dialog.getByRole("alert")).toHaveCount(0);
  await page.getByRole("button", { name: "已有账户？登录" }).click();
  await page.screenshot({ path: testInfo.outputPath("login-dialog.png") });
  // Native dialog keeps keyboard focus inside and restores it to the opener.
  for (let i = 0; i < 10; i++) {
    await page.keyboard.press("Tab");
    expect(
      await page.evaluate(() => !!document.activeElement?.closest("dialog")),
    ).toBe(true);
  }
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await expect(login).toBeFocused();
  await page
    .getByRole("link", { name: "研究能力", exact: true })
    .first()
    .click();
  await expect(page).toHaveURL(/#features$/);
  expect(errors).toEqual([]);
});

test("mobile and landscape preserve navigation, form, reduced motion and page width", async ({
  page,
}, testInfo) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "登录", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("login-mobile.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "注册", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByLabel("邮箱", { exact: true })).toBeFocused();
  await page.screenshot({
    path: testInfo.outputPath("login-mobile-dialog.png"),
  });
  await page.setViewportSize({ width: 812, height: 375 });
  await page
    .getByRole("button", { name: "创建账户", exact: true })
    .last()
    .scrollIntoViewIfNeeded();
  await expect(
    page
      .getByRole("dialog")
      .getByRole("button", { name: "创建账户", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});
