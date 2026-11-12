import { chromium } from "@playwright/test";
import { writeFile } from "node:fs/promises";
const browser = await chromium.launch({
  channel: "chrome",
  ...(process.env.ALPHA_BROWSER_DIRECT === "1"
    ? { args: ["--no-proxy-server"] }
    : {}),
});
const samples = [];
// Run against the real public entry with an empty browser cache and no session.
// The byte budget guards eager workspace imports; timing captures network regressions.
try {
  for (let i = 0; i < 3; i++) {
    const context = await browser.newContext();
    const page = await context.newPage();
    const started = Date.now();
    const failed = [];
    page.on("requestfailed", (r) =>
      failed.push({
        path: new URL(r.url()).pathname,
        error: r.failure()?.errorText,
      }),
    );
    await page.goto("https://trading.youwei-agent.com/", {
      waitUntil: "domcontentloaded",
      timeout: 60000,
    });
    await page.getByRole("button", { name: "登录", exact: true }).click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "进入工作台", exact: true })
      .waitFor({ timeout: 60000 });
    await page.waitForFunction(
      () => !document.querySelector('button[type="submit"]')?.disabled,
      { timeout: 10000 },
    );
    const visibleMs = Date.now() - started;
    await page.waitForTimeout(500);
    const data = await page.evaluate(() => ({
      nav: performance.getEntriesByType("navigation").map((n) => ({
        ttfb: n.responseStart,
        start: n.startTime,
        dom: n.domContentLoadedEventEnd,
        load: n.loadEventEnd,
        transfer: n.transferSize,
      })),
      resources: performance.getEntriesByType("resource").map((r) => ({
        path: new URL(r.name).pathname,
        type: r.initiatorType,
        start: r.startTime,
        duration: r.duration,
        ttfb: r.responseStart - r.requestStart,
        transfer: r.transferSize,
        encoded: r.encodedBodySize,
        decoded: r.decodedBodySize,
      })),
      paint: performance
        .getEntriesByType("paint")
        .map((p) => ({ name: p.name, time: p.startTime })),
    }));
    const initialJsBytes = data.resources
      .filter((r) => r.type === "script" || r.path.endsWith(".js"))
      .reduce((sum, r) => sum + r.decoded, 0);
    samples.push({ visibleMs, initialJsBytes, ...data, failed });
    console.log(JSON.stringify(samples.at(-1)));
    await context.close();
  }
  await writeFile(
    process.argv[2] ?? ".local/performance/latest.json",
    JSON.stringify(samples, null, 2),
  );
  const median = samples.map((s) => s.visibleMs).sort((a, b) => a - b)[1];
  console.log(
    JSON.stringify({
      medianVisibleMs: median,
      budgetMs: 3000,
      passed: median < 3000,
    }),
  );
  if (
    median >= 3000 ||
    samples.some((s) => s.initialJsBytes > 600000 || s.failed.length)
  )
    process.exitCode = 1;
} finally {
  await browser.close();
}
