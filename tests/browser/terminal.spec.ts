import { test, expect, type Page } from "@playwright/test";

async function terminal(
  page: Page,
  populated = true,
  loseAdmissionResponse = false,
) {
  let started = false;
  let admissionAttempts = 0;
  const rows = Array.from({ length: 100 }, (_, i) => ({
    session: new Date(Date.UTC(2026, 4, 1 + i)).toISOString().slice(0, 10),
    open: (110 + i * 0.25 + Math.sin(i / 3) * 3).toFixed(2),
    high: (114 + i * 0.25 + Math.sin(i / 3) * 3).toFixed(2),
    low: (108 + i * 0.25 + Math.sin(i / 3) * 3).toFixed(2),
    close: (112 + i * 0.25 + Math.sin(i / 3) * 3).toFixed(2),
    volume: "1234500",
    ma20: (110 + i * 0.25).toFixed(2),
    rsi14: "54.20",
  }));
  const components = [
    "stock_chart",
    "indicator_panel",
    "prediction_result",
    "portfolio_summary",
    "research_summary",
  ];
  const artifacts = components.map((component, i) => ({
    artifactId: `artifact-${i}`,
    component,
    title: [
      "NVDA 历史行情",
      "MA20 与 RSI14",
      "预测评估",
      "模拟组合",
      "研究摘要",
    ][i],
    ...(i === 0
      ? { datasetRef: `dataset-${i}` }
      : { resultRef: `result-${i}` }),
  }));
  let workspace = {
    id: "workspace",
    version: 1,
    revision: 1,
    snapshot: { artifacts },
  };
  const task = {
    id: "task",
    status: "completed",
    created_at: "2026-08-10T00:00:00Z",
  };
  const thread = {
    id: "thread",
    title: "NVDA 走势研究",
    workspace,
    tasks: [task],
    messages: [
      {
        id: "u",
        task_id: "task",
        role: "user",
        content: "分析 NVDA 的历史价格走势。",
      },
      {
        id: "a",
        task_id: "task",
        role: "assistant",
        content:
          "研究已完成。历史行情、技术指标与预测评估均已保存，可在画布中查看各项依据。\n\n这里使用的是浏览器测试数据，不代表实时市场行情。",
      },
    ],
  };
  let watchlist = populated
    ? ["AAPL", "NVDA", "MSFT"].map((symbol) => ({ symbol }))
    : [];
  const requests: string[] = [];
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      path = new URL(request.url()).pathname.slice(5);
    if (request.method() !== "GET") requests.push(path);
    let data: unknown;
    if (path === "auth/session")
      data = { id: "owner", email: "research@example.test" };
    else if (path === "threads")
      data =
        request.method() === "POST"
          ? { id: "new" }
          : populated
            ? [
                {
                  id: "thread",
                  title: thread.title,
                  created_at: task.created_at,
                },
              ]
            : [];
    else if (path === "threads/thread") data = { ...thread, workspace };
    else if (path === "research") {
      started = true;
      admissionAttempts++;
      if (loseAdmissionResponse && admissionAttempts === 1)
        return route.fulfill({
          status: 503,
          json: { error: "connection_failed" },
        });
      return route.fulfill({
        status: 202,
        json: { taskId: "task", threadId: "new", status: "queued" },
      });
    } else if (path === "threads/new")
      data = started
        ? { ...thread, id: "new", workspace }
        : {
            id: "new",
            messages: [],
            tasks: [],
            workspace: {
              id: "new-workspace",
              snapshot: { artifacts: [] },
              version: 0,
              revision: 0,
            },
          };
    else if (path === "profiles")
      data = ["research", "prediction", "portfolio"].map((role) => ({
        role,
        state: populated ? "stopped" : "unprovisioned",
      }));
    else if (path === "portfolios") data = [];
    else if (path === "watchlist") {
      if (request.method() === "POST")
        watchlist = [...watchlist, { symbol: request.postDataJSON().symbol }];
      data = watchlist;
    } else if (path.startsWith("datasets/"))
      data = {
        payload: rows,
        metadata: {
          symbol: "NVDA",
          feed: "iex",
          source: "browser-test",
          adjustment: "split",
          currency: "USD",
          exchangeTimezone: "America/New_York",
          asOf: "2026-08-10",
        },
      };
    else if (path === "results/result-1")
      data = {
        payload: { rows },
        lineage: { datasetRefs: ["dataset-0"], codeVersion: "fixture" },
      };
    else if (path === "results/result-2")
      data = {
        payload: {
          prediction: "0.012",
          target: "2026-08-11",
          origin: "2026-08-10",
          metrics: { mae: "0.02", rmse: "0.03" },
          zeroBaseline: { mae: "0.03", rmse: "0.04" },
        },
      };
    else if (path === "results/result-3")
      data = {
        payload: {
          asOf: "2026-08-10",
          total: "1800",
          concentration: "0.8",
          positions: [{ symbol: "NVDA", value: "1700", weight: "0.94" }],
        },
      };
    else if (path === "results/result-4")
      data = { payload: { text: "示例研究结果，仅用于界面测试。" } };
    else if (path === "tasks/task") data = { task, workspace, artifacts };
    else if (path === "workspaces/workspace") {
      if (request.method() === "PUT")
        workspace = {
          ...workspace,
          version: workspace.version + 1,
          snapshot: {
            artifacts: request
              .postDataJSON()
              .order.map((id: string) =>
                artifacts.find((a) => a.artifactId === id),
              ),
          },
        };
      data = workspace;
    } else return route.fulfill({ status: 404, json: { error: "not_found" } });
    await route.fulfill({ json: data });
  });
  return requests;
}

test("conversation workbench opens results, preserves charts, draft and history", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const requests = await terminal(page);
  await page.goto("/");
  await page
    .getByRole("navigation", { name: "研究记录" })
    .getByRole("button")
    .click();
  await expect(page.locator(".canvas-panel")).toHaveCount(0);
  await expect(page.locator(".canvas-toggle")).toContainText("5");
  await page.getByRole("button", { name: /研究画布，5 项结果/ }).click();
  await expect(page.locator(".artifact")).toHaveCount(5);
  await expect(page.getByRole("img", { name: "历史收盘价走势" })).toBeVisible();
  await expect(page.locator(".quote-symbol")).toHaveText("NVDA");
  await expect(page.locator(".quote-metrics")).toContainText("134.75");
  await expect(page.locator(".quote-metrics")).toContainText("54.20");

  await expect(page.getByText("非实时行情", { exact: true })).toBeVisible();
  const sidebar = await page.locator(".sidebar").boundingBox(),
    canvas = await page.locator(".canvas-panel").boundingBox(),
    chat = await page.locator(".chat-shell").boundingBox();
  expect(sidebar!.x + sidebar!.width).toBeLessThanOrEqual(chat!.x + 1);
  expect(chat!.x + chat!.width).toBeLessThanOrEqual(canvas!.x + 1);
  await page.screenshot({
    path: test.info().outputPath("terminal-desktop.png"),
    fullPage: true,
  });
  await page
    .getByRole("navigation", { name: "研究结果导航" })
    .getByRole("button", { name: "组合", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "模拟组合", exact: true }),
  ).toBeInViewport();
  await page
    .getByRole("navigation", { name: "研究结果导航" })
    .getByRole("button", { name: "行情", exact: true })
    .click();
  await expect(page.locator(".quote-symbol")).toBeInViewport();
  await page
    .getByRole("textbox", { name: "研究问题" })
    .fill("继续研究这只股票");
  await page.getByRole("button", { name: "放大画布" }).click();
  await expect(page.locator(".conversation-column")).not.toBeVisible();
  await page.getByRole("button", { name: "还原画布" }).click();
  await page.getByRole("button", { name: "收起画布" }).click();
  await expect(page.getByRole("textbox", { name: "研究问题" })).toHaveValue(
    "继续研究这只股票",
  );
  await page.screenshot({
    path: test.info().outputPath("conversation-collapsed.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: /研究画布，5 项结果/ }).click();
  await expect(page.getByRole("img", { name: "历史收盘价走势" })).toBeVisible();
  await page.getByRole("button", { name: "1M", exact: true }).click();
  await expect(page.locator(".chart-range")).toContainText("31 个数据日");
  await page.getByRole("button", { name: "K 线", exact: true }).click();
  await expect(page.getByRole("img", { name: "历史价格 K 线" })).toBeVisible();
  await page.getByRole("button", { name: "走势", exact: true }).click();
  await page.getByRole("button", { name: "下移 NVDA 历史行情" }).click();
  await expect(page.locator(".artifact h3").first()).toHaveText(
    "MA20 与 RSI14",
  );
  await page.getByText(/任务记录 ·/).click();
  await page.getByRole("button", { name: "查看此轮产物" }).click();
  await expect(page.getByText("正在查看历史产物")).toBeVisible();
  await page.getByRole("button", { name: "返回当前画布" }).click();
  await expect(page.getByText("正在查看历史产物")).not.toBeVisible();
  await page.getByRole("button", { name: "研究 AAPL", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "研究问题" })).toHaveValue(
    "分析 AAPL 最近三个月走势，并计算 MA20 和 RSI。",
  );
  await expect(page.locator(".artifact")).toHaveCount(0);
  await expect(page.locator(".canvas-toggle")).toContainText("0");
  expect(requests).not.toContain("research");
  expect(errors).toEqual([]);
});

test("empty terminal, mobile navigation, holdings dialog and landscape", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await terminal(page, false);
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "今天想研究哪只股票？" }),
  ).toBeVisible();
  await expect(page.locator(".canvas-panel")).toHaveCount(0);
  await page.screenshot({
    path: test.info().outputPath("terminal-empty.png"),
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "查一下 TSLA 最新走势", exact: true })
    .click();
  await expect(page.getByRole("textbox", { name: "研究问题" })).toHaveValue(
    /TSLA/,
  );
  await expect(page.getByRole("textbox", { name: "研究问题" })).toBeFocused();
  await page.setViewportSize({ width: 375, height: 812 });
  await page.getByRole("button", { name: "研究记录与持仓 ＋" }).click();
  await page.getByRole("textbox", { name: "自选股票代码" }).fill("NVDA");
  await page.getByRole("button", { name: "添加自选股", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "研究 NVDA", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "编辑模拟持仓 ↗" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByLabel("持仓日期").fill("2026-08-10");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await expect(
    page.getByRole("button", { name: "编辑模拟持仓 ↗" }),
  ).toBeFocused();
  await page.getByRole("button", { name: "研究 NVDA", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "研究问题" })).toHaveValue(
    /NVDA/,
  );
  await page.getByRole("textbox", { name: "研究问题" }).focus();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: test.info().outputPath("terminal-mobile.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 812, height: 375 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await expect(page.getByRole("button", { name: "发送研究" })).toBeEnabled();
  await page.locator(".canvas-toggle").click();
  await expect(page.locator(".canvas-empty")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.locator(".canvas-panel")).toHaveCount(0);
  await expect(page.locator(".canvas-toggle")).toBeFocused();
});

for (const loseResponse of [false, true]) {
  test(`welcome sends one research and ${loseResponse ? "recovers admission without duplicate" : "opens the saved result"}`, async ({
    page,
  }) => {
    const requests = await terminal(page, false, loseResponse);
    const keys: string[] = [];
    page.on("request", (r) => {
      if (new URL(r.url()).pathname === "/api/research")
        keys.push(r.headers()["idempotency-key"]!);
    });
    await page.goto("/");
    await page
      .getByRole("textbox", { name: "研究问题" })
      .fill("查询 TSLA 最近三个月走势");
    await page.getByRole("button", { name: "发送研究" }).click();
    if (loseResponse) {
      await expect(page.locator(".app [role=alert]")).toContainText("连接中断");
      await expect(page.getByRole("textbox", { name: "研究问题" })).toHaveValue(
        "查询 TSLA 最近三个月走势",
      );
      await page.getByRole("button", { name: "发送研究" }).click();
    }
    await expect(
      page.getByRole("button", { name: /研究画布，5 项结果/ }),
    ).toBeVisible();
    expect(requests.filter((p) => p === "threads")).toHaveLength(1);
    expect(keys).toHaveLength(loseResponse ? 2 : 1);
    expect(new Set(keys).size).toBe(1);
    await expect(page.locator(".canvas-panel")).toHaveCount(0);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole("button", { name: /研究画布，5 项结果/ }).click();
    await expect(
      page.getByRole("img", { name: "历史收盘价走势" }),
    ).toBeVisible();
    await expect(page.locator(".conversation-column")).not.toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: test.info().outputPath("canvas-mobile.png"),
      fullPage: true,
    });
    await page.getByRole("button", { name: "收起画布" }).click();
    await expect(
      page.getByRole("button", { name: /研究画布，5 项结果/ }),
    ).toBeFocused();
  });
}

test("failed follow-up explains recovery, retries one request and associates results with its reply", async ({
  page,
}) => {
  await terminal(page);
  let attempts = 0;
  const keys: string[] = [];
  await page.route("**/api/research", async (route) => {
    keys.push(route.request().headers()["idempotency-key"]!);
    attempts++;
    await route.fulfill(
      attempts === 1
        ? { status: 503, json: { error: "service_unavailable" } }
        : { status: 202, json: { taskId: "followup", status: "queued" } },
    );
  });
  await page.goto("/");
  await page
    .getByRole("navigation", { name: "研究记录", exact: true })
    .getByRole("button")
    .click();
  await expect(page.locator(".research-result-card")).toHaveCount(1);
  const original = await page.evaluate(async () =>
    (await fetch("/api/threads/thread")).json(),
  );
  const workspace = { ...original.workspace, revision: 2, version: 2 };
  const snapshot = {
    task: { id: "followup", status: "completed" },
    workspace,
    messages: [
      {
        id: "new-reply",
        role: "assistant",
        task_id: "followup",
        content: "这是追问的研究结果。",
      },
    ],
    lastSequence: 1,
  };
  await page.route("**/api/tasks/followup/events", (route) =>
    route.fulfill({
      contentType: "text/event-stream",
      body: `id: 1\nevent: state.snapshot\ndata: ${JSON.stringify({ event: "state.snapshot", taskId: "followup", sequence: 1, data: snapshot })}\n\n`,
    }),
  );
  await page
    .getByRole("textbox", { name: "研究问题" })
    .fill("继续分析，保留这条问题供重试");
  await page.getByRole("button", { name: "发送研究", exact: true }).click();
  await expect(page.locator(".notice[role=alert]")).toContainText(
    "服务暂时不可用",
  );
  await expect(
    page
      .locator('.chat-message[data-role="assistant"]')
      .last()
      .locator(".research-result-card"),
  ).toHaveCount(0);
  await expect(
    page
      .locator('.chat-message[data-role="assistant"]')
      .first()
      .locator(".research-result-card"),
  ).toHaveCount(1);
  await page.getByRole("button", { name: "重试这条问题" }).click();
  await expect(
    page.locator('.chat-message[data-role="assistant"]').last(),
  ).toContainText("这是追问的研究结果。");
  await expect(
    page
      .locator('.chat-message[data-role="assistant"]')
      .last()
      .locator(".research-result-card"),
  ).toHaveCount(1);
  await expect(page.locator(".research-result-card")).toHaveCount(1);
  await expect(
    page
      .locator('.chat-message[data-role="user"]')
      .filter({ hasText: "继续分析，保留这条问题供重试" }),
  ).toHaveCount(1);
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
});

test("unsent drafts survive switching research and opening the canvas", async ({
  page,
}) => {
  await terminal(page);
  await page.goto("/");
  await page
    .getByRole("textbox", { name: "研究问题" })
    .fill("欢迎页的未发送草稿");
  const history = page
    .getByRole("navigation", { name: "研究记录", exact: true })
    .getByRole("button");
  await history.click();
  await expect(page.locator(".chat-shell")).toBeVisible();
  await page
    .getByRole("textbox", { name: "研究问题" })
    .fill("已保存研究里的未发送草稿");
  await page.getByRole("button", { name: "＋ 新建研究", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "研究问题" })).toHaveValue(
    "欢迎页的未发送草稿",
  );
  await history.click();
  await expect(page.getByRole("textbox", { name: "研究问题" })).toHaveValue(
    "已保存研究里的未发送草稿",
  );
  await page.locator(".canvas-toggle").click();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("textbox", { name: "研究问题" })).toHaveValue(
    "已保存研究里的未发送草稿",
  );
});

test("holdings returns focus to its trigger without closing or refocusing the canvas", async ({
  page,
}) => {
  await terminal(page);
  await page.goto("/");
  await page
    .getByRole("navigation", { name: "研究记录", exact: true })
    .getByRole("button")
    .click();
  const result = page.locator(".research-result-card");
  await expect(result).toBeVisible();
  const edit = page.getByRole("button", { name: "编辑模拟持仓 ↗" });
  for (const canvasOpen of [false, true]) {
    if (canvasOpen) {
      await result.click();
      await expect(
        page.getByRole("button", { name: "收起画布" }),
      ).toBeVisible();
    }
    for (const closeWithEscape of [true, false]) {
      await edit.click();
      await expect(page.getByRole("dialog")).toBeVisible();
      if (closeWithEscape) await page.keyboard.press("Escape");
      else await page.getByRole("button", { name: "关闭持仓编辑" }).click();
      await expect(page.getByRole("dialog")).toHaveCount(0);
      await expect(edit).toBeFocused();
      await expect(page.locator(".canvas-panel")).toHaveCount(
        canvasOpen ? 1 : 0,
      );
    }
  }
  await page.keyboard.press("Escape");
  await expect(page.locator(".canvas-panel")).toHaveCount(0);
  await expect(result).toBeFocused();
});

test("short viewports keep canvas results and keyboard controls reachable", async ({
  page,
}) => {
  await terminal(page);
  await page.goto("/");
  await page
    .getByRole("navigation", { name: "研究记录", exact: true })
    .getByRole("button")
    .click();
  await page.locator(".research-result-card").click();
  await expect(page.locator(".quote-symbol")).toHaveText("NVDA");
  await page.setViewportSize({ width: 320, height: 228 });

  const candles = page.getByRole("button", { name: "K 线", exact: true });
  for (let step = 0; step < 40; step++) {
    if (await candles.evaluate((el) => el === document.activeElement)) break;
    await page.keyboard.press("Tab");
  }
  await expect(candles).toBeFocused();
  await expect(candles).toBeInViewport();
  await page.keyboard.press("Enter");
  await expect(candles).toHaveAttribute("aria-pressed", "true");

  const disclosure = page.locator(".chart-data summary").first();
  await page.keyboard.press("Tab");
  await expect(disclosure).toBeFocused();
  await expect(disclosure).toBeInViewport();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("region", { name: "行情数据表", exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(
    page.getByRole("region", { name: "行情数据表", exact: true }),
  ).toBeFocused();
  await expect(
    page.getByRole("region", { name: "行情数据表", exact: true }),
  ).toBeInViewport();

  await page
    .getByRole("navigation", { name: "研究结果导航" })
    .getByRole("button", { name: "组合", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "模拟组合", exact: true }),
  ).toBeInViewport();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
    320,
  );
  await page.keyboard.press("Escape");
  await expect(page.locator(".canvas-panel")).toHaveCount(0);
});

for (const height of [256, 228]) {
  test(`stacked errors keep research and recovery reachable at 320x${height}`, async ({
    page,
  }) => {
    await terminal(page);
    await page.goto("/");
    await page
      .getByRole("navigation", { name: "研究记录", exact: true })
      .getByRole("button")
      .click();
    await page.locator(".research-result-card").click();
    await expect(page.locator(".quote-symbol")).toHaveText("NVDA");
    await page.route("**/api/watchlist", (route) =>
      route.request().method() === "POST"
        ? route.fulfill({ status: 503, json: { error: "service_unavailable" } })
        : route.fallback(),
    );
    await page.getByRole("textbox", { name: "自选股票代码" }).fill("TSLA");
    await page.getByRole("button", { name: "添加自选股", exact: true }).click();
    await expect(page.locator(".account-error")).toContainText("未添加自选股");
    await page.route("**/api/auth/logout", (route) =>
      route.fulfill({ status: 503, json: { error: "service_unavailable" } }),
    );
    await page.getByRole("button", { name: "退出", exact: true }).click();
    await expect(page.getByRole("button", { name: "重试退出" })).toBeVisible();
    await page.setViewportSize({ width: 320, height });

    const candles = page.getByRole("button", { name: "K 线", exact: true });
    for (let step = 0; step < 50; step++) {
      if (await candles.evaluate((el) => el === document.activeElement)) break;
      await page.keyboard.press("Tab");
    }
    await expect(candles).toBeFocused();
    await expect(candles).toBeInViewport({ ratio: 1 });
    await page.keyboard.press("Enter");
    await expect(candles).toHaveAttribute("aria-pressed", "true");
    await page
      .getByRole("navigation", { name: "研究结果导航" })
      .getByRole("button", { name: "组合", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "模拟组合", exact: true }),
    ).toBeInViewport();
    await page.keyboard.press("Escape");
    const composer = page.getByRole("textbox", { name: "研究问题" });
    await composer.fill("错误提示后仍保留的草稿");
    await expect(composer).toBeInViewport();

    const retry = page.getByRole("button", { name: "重试退出", exact: true });
    const response = page.waitForResponse("**/api/auth/logout");
    await retry.click();
    await response;
    await expect(retry).toBeInViewport();
    await expect(composer).toHaveValue("错误提示后仍保留的草稿");
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth),
    ).toBe(320);
  });
}

test("320px charts keep full price axes and expose keyboard-readable data, including range changes", async ({
  page,
}) => {
  await terminal(page);
  await page.goto("/");
  await page
    .getByRole("navigation", { name: "研究记录", exact: true })
    .getByRole("button")
    .click();
  await page.locator(".canvas-toggle").click();
  await page.getByRole("img", { name: "历史收盘价走势" }).waitFor();
  await page.setViewportSize({ width: 320, height: 740 });
  const stock = page.locator('[data-component="stock_chart"]');
  await expect
    .poll(() =>
      stock.locator(".chart").evaluate((el) => {
        const bound = el.getBoundingClientRect();
        return [...el.querySelectorAll("canvas")].every(
          (c) => c.getBoundingClientRect().right <= bound.right + 1,
        );
      }),
    )
    .toBe(true);
  const disclosure = stock.locator(".chart-data summary");
  await disclosure.focus();
  await page.keyboard.press("Enter");
  await expect(
    stock.getByRole("table", { name: /当前显示区间/ }),
  ).toContainText("2026-05-01");
  await expect(
    stock.getByRole("table", { name: /当前显示区间/ }),
  ).toContainText("139.75");
  await stock.getByRole("button", { name: "1M", exact: true }).click();
  await expect(
    stock.getByRole("table", { name: /当前显示区间/ }),
  ).not.toContainText("2026-05-01");
  await expect(
    stock.getByRole("table", { name: /当前显示区间/ }),
  ).toContainText("2026-08-08");
  await page
    .getByRole("navigation", { name: "研究结果导航" })
    .getByRole("button", { name: "指标", exact: true })
    .click();
  const indicators = page.locator('[data-component="indicator_panel"]');
  await indicators.locator(".chart-data summary").focus();
  await page.keyboard.press("Enter");
  await expect(
    indicators.getByRole("table", { name: /历史指标/ }),
  ).toContainText("54.20");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: /研究记录与持仓/ }).click();
  await expect(page.getByRole("textbox", { name: "自选股票代码" })).toHaveCSS(
    "font-size",
    "16px",
  );
  await page.getByRole("button", { name: "编辑模拟持仓 ↗" }).click();
  for (const label of ["持仓日期", "股票与数量", "美元现金"])
    await expect(page.getByLabel(label)).toHaveCSS("font-size", "16px");
});

test("async collection failures show recovery instead of empty data", async ({
  page,
}) => {
  await terminal(page);
  let fail = true;
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  for (const path of ["watchlist", "portfolios"]) {
    await page.route("**/api/" + path, async (route) => {
      await gate;
      if (fail)
        return route.fulfill({
          status: 503,
          json: { error: "service_unavailable" },
        });
      await route.fulfill({
        json:
          path === "watchlist"
            ? [{ symbol: "TSLA" }]
            : [
                {
                  id: "p",
                  snapshot_id: "snapshot",
                  name: "模拟组合",
                  as_of_session: "2026-11-06",
                },
              ],
      });
    });
  }
  await page.goto("/");
  await expect(
    page.getByText("正在加载自选股…", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("正在加载模拟持仓…", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".watch-empty")).toHaveCount(0);
  release();
  await expect(page.locator(".watch-section [role=alert]")).toContainText(
    "自选股加载失败",
  );
  await expect(page.locator(".sidebar-bottom [role=alert]")).toContainText(
    "模拟持仓加载失败",
  );
  await expect(page.locator(".watch-count")).not.toHaveText("0");
  await expect(page.locator(".watch-empty")).toHaveCount(0);
  fail = false;
  await page
    .getByRole("button", { name: "重试加载自选股", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "研究 TSLA", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".watch-section [role=alert]")).toHaveCount(0);
  await expect(page.locator(".sidebar-bottom [role=alert]")).toBeVisible();
  await page
    .getByRole("button", { name: "重试加载模拟持仓", exact: true })
    .click();
  await expect(
    page.getByRole("combobox", { name: "模拟持仓", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".sidebar-bottom [role=alert]")).toHaveCount(0);
});

test("successful empty collections remain distinct from loading and errors", async ({
  page,
}) => {
  await terminal(page, false);
  await page.goto("/");
  await expect(page.locator(".watch-empty")).toHaveText(
    "添加关注的股票，随时开始研究。",
  );
  await expect(page.locator(".watch-count")).toHaveText("0");
  await expect(
    page.getByText("尚无模拟持仓，可保存一份新快照。", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".app [role=alert]")).toHaveCount(0);
});

test("logout failure is visible with expanded canvas and can be retried", async ({
  page,
}) => {
  await terminal(page);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  let attempts = 0;
  await page.route("**/api/auth/logout", async (route) => {
    attempts++;
    await route.fulfill(
      attempts === 1
        ? { status: 503, json: { error: "service_unavailable" } }
        : { json: { ok: true } },
    );
  });
  await page.goto("/");
  await page.locator(".canvas-toggle").click();
  await page.getByRole("button", { name: "放大画布", exact: true }).click();
  await page.getByRole("button", { name: "退出", exact: true }).click();
  await expect(page.locator(".account-error[role=alert]")).toContainText(
    "尚未退出",
  );
  await expect(page.locator(".app")).toBeVisible();
  await page.getByRole("button", { name: "重试退出", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "登录", exact: true }),
  ).toBeVisible();
  expect(attempts).toBe(2);
  expect(errors).toEqual([]);
});

test("late collection responses cannot restore a signed-out account", async ({
  page,
}) => {
  await terminal(page);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let reads = 0;
  await page.route("**/api/watchlist", async (route) => {
    reads++;
    if (reads === 1) {
      await gate;
      return route.fulfill({ json: [{ symbol: "OLD" }] });
    }
    await route.fulfill({ json: [{ symbol: "NEW" }] });
  });
  await page.route("**/api/auth/logout", (r) =>
    r.fulfill({ json: { ok: true } }),
  );
  await page.route("**/api/auth/login", (r) =>
    r.fulfill({ json: { id: "new-owner", email: "new@example.test" } }),
  );
  await page.goto("/");
  await expect(
    page.getByText("正在加载自选股…", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "退出", exact: true }).click();
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByLabel("邮箱", { exact: true }).fill("new@example.test");
  await page.getByLabel("密码", { exact: true }).fill("browser-test-password");
  await page
    .locator("form")
    .getByRole("button", { name: "进入工作台", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "研究 NEW", exact: true }),
  ).toBeVisible();
  const oldResponse = page.waitForResponse(
    (r) => new URL(r.url()).pathname === "/api/watchlist",
  );
  release();
  await (await oldResponse).finished();
  await page.waitForTimeout(100);
  await expect(
    page.getByRole("button", { name: "研究 OLD", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "研究 NEW", exact: true }),
  ).toBeVisible();
});

test("sidebar failures remain visible and recover with the canvas expanded", async ({
  page,
}) => {
  await terminal(page);
  await page.goto("/");
  const history = page
    .getByRole("navigation", { name: "研究记录", exact: true })
    .getByRole("button");
  await history.click();
  await page.locator(".research-result-card").click();
  await page.getByRole("button", { name: "放大画布", exact: true }).click();
  let fail = true;
  await page.route("**/api/watchlist", (r) =>
    fail && r.request().method() === "POST"
      ? r.fulfill({ status: 503, json: { error: "service_unavailable" } })
      : r.fallback(),
  );
  await page.getByRole("textbox", { name: "自选股票代码" }).fill("TSLA");
  await page.getByRole("button", { name: "添加自选股", exact: true }).click();
  await expect(page.locator(".app > [role=alert]")).toContainText(
    "未添加自选股",
  );
  await expect(page.locator(".app > [role=alert]")).toBeInViewport();
  fail = false;
  await page.getByRole("button", { name: "添加自选股", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "研究 TSLA", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".app > [role=alert]")).toHaveCount(0);
  fail = true;
  await page.route("**/api/threads/thread", (r) =>
    fail
      ? r.fulfill({ status: 503, json: { error: "service_unavailable" } })
      : r.fallback(),
  );
  await history.click();
  await expect(page.locator(".app > [role=alert]")).toContainText(
    "无法打开研究",
  );
  await expect(page.locator(".app > [role=alert]")).toBeInViewport();
  await page.setViewportSize({ width: 320, height: 740 });
  await expect(page.locator(".app > [role=alert]")).toBeInViewport();
  await page.getByRole("button", { name: /研究记录与持仓/ }).click();
  fail = false;
  await history.click();
  await expect(page.locator(".app > [role=alert]")).toHaveCount(0);
});

test("a missing result explains its scope and retries reading without new research", async ({
  page,
}) => {
  const requests = await terminal(page);
  let fail = true;
  await page.route("**/api/datasets/*", (r) =>
    fail
      ? r.fulfill({ status: 404, json: { error: "not_found" } })
      : r.fallback(),
  );
  await page.goto("/");
  await page
    .getByRole("navigation", { name: "研究记录", exact: true })
    .getByRole("button")
    .click();
  await page.locator(".research-result-card").click();
  const artifact = page.locator('[data-component="stock_chart"]');
  await expect(artifact.getByRole("alert")).toContainText("这项研究结果");
  await expect(artifact.getByRole("alert")).not.toContainText("此研究不存在");
  await expect(artifact.getByRole("alert")).toContainText("重新打开研究");
  await page.setViewportSize({ width: 320, height: 740 });
  const retry = artifact.getByRole("button", {
    name: "重新读取结果：NVDA 历史行情",
    exact: true,
  });
  await retry.focus();
  fail = false;
  await page.keyboard.press("Enter");
  await expect(artifact.locator(".quote-symbol")).toHaveText("NVDA");
  await expect(artifact.getByRole("alert")).toHaveCount(0);
  expect(requests).toEqual([]);
});

test("saving holdings prevents duplicates and refresh failures only retry the list", async ({
  page,
}) => {
  await terminal(page);
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  let saves = 0;
  let failRefresh = false;
  await page.route("**/api/portfolios", async (r) => {
    if (r.request().method() === "POST") {
      saves++;
      await gate;
      failRefresh = true;
      return r.fulfill({ json: { id: "p", snapshotId: "snapshot" } });
    }
    if (failRefresh)
      return r.fulfill({ status: 503, json: { error: "service_unavailable" } });
    return r.fulfill({
      json: saves
        ? [
            {
              id: "p",
              snapshot_id: "snapshot",
              name: "模拟组合",
              as_of_session: "2026-11-06",
            },
          ]
        : [],
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "编辑模拟持仓 ↗" }).click();
  await page.getByLabel("持仓日期").fill("2026-11-06");
  await page.getByLabel("股票与数量").fill("NVDA 10");
  await page
    .getByRole("button", { name: "保存新快照", exact: true })
    .dblclick();
  await expect(
    page.getByRole("button", { name: "正在保存…", exact: true }),
  ).toHaveAttribute("aria-disabled", "true");
  expect(saves).toBe(1);
  release();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.locator(".sidebar-bottom [role=status]")).toContainText(
    "模拟持仓已保存",
  );
  await expect(page.locator(".sidebar-bottom [role=alert]")).toContainText(
    "模拟持仓加载失败",
  );
  failRefresh = false;
  await page
    .getByRole("button", { name: "重试加载模拟持仓", exact: true })
    .click();
  await expect(
    page.getByRole("combobox", { name: "模拟持仓", exact: true }),
  ).toHaveValue("snapshot");
  expect(saves).toBe(1);
});

test("result card accessible name includes its visible research title", async ({
  page,
}) => {
  await terminal(page);
  await page.goto("/");
  await page
    .getByRole("navigation", { name: "研究记录", exact: true })
    .getByRole("button")
    .click();
  await expect(page.locator(".research-result-card")).toHaveAccessibleName(
    /NVDA 历史行情/,
  );
});

for (const status of [200, 503]) {
  test(`closed holdings editor ignores its late ${status} response`, async ({
    page,
  }) => {
    await terminal(page);
    let release!: () => void;
    const gate = new Promise<void>((r) => {
      release = r;
    });
    await page.route("**/api/portfolios", async (r) => {
      if (r.request().method() !== "POST") return r.fallback();
      await gate;
      await r.fulfill({
        status,
        json:
          status === 200
            ? { id: "p", snapshotId: "old-snapshot" }
            : { error: "service_unavailable" },
      });
    });
    await page.goto("/");
    const edit = page.getByRole("button", { name: "编辑模拟持仓 ↗" });
    await edit.click();
    await page.getByLabel("持仓日期").fill("2026-11-06");
    await page.getByLabel("股票与数量").fill("NVDA 10");
    await page.getByRole("button", { name: "保存新快照", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "正在保存…", exact: true }),
    ).toBeVisible();
    if (status === 200)
      await page
        .getByRole("button", { name: "关闭持仓编辑", exact: true })
        .click();
    else await page.keyboard.press("Escape");
    await edit.click();
    await page.getByLabel("持仓日期").fill("2026-11-08");
    await page.getByLabel("股票与数量").fill("TSLA 25");
    const response = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/portfolios") && r.request().method() === "POST",
    );
    release();
    await (await response).finished();
    await page.waitForTimeout(100);
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page.getByLabel("股票与数量")).toHaveValue("TSLA 25");
    await expect(page.getByLabel("持仓日期")).toHaveValue("2026-11-08");
    await expect(page.getByRole("dialog").getByRole("alert")).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "保存新快照", exact: true }),
    ).toHaveAttribute("aria-disabled", "false");
  });
}

for (const status of [200, 503]) {
  test(`previous account research ${status} response cannot affect the next login`, async ({
    page,
  }) => {
    await terminal(page);
    let release!: () => void;
    const gate = new Promise<void>((r) => {
      release = r;
    });
    let started = false,
      secondOwner = false;
    await page.route("**/api/threads/thread", async (r) => {
      started = true;
      await gate;
      if (status === 503)
        return r.fulfill({ status, json: { error: "service_unavailable" } });
      await r.fallback();
    });
    await page.route("**/api/threads", (r) =>
      secondOwner ? r.fulfill({ json: [] }) : r.fallback(),
    );
    await page.route("**/api/auth/logout", (r) =>
      r.fulfill({ json: { ok: true } }),
    );
    await page.route("**/api/auth/login", (r) => {
      secondOwner = true;
      return r.fulfill({
        json: { id: "second-owner", email: "second@example.test" },
      });
    });
    await page.goto("/");
    await page
      .getByRole("navigation", { name: "研究记录", exact: true })
      .getByRole("button")
      .click();
    await expect.poll(() => started).toBe(true);
    await page.getByRole("button", { name: "退出", exact: true }).click();
    await page.getByRole("button", { name: "登录", exact: true }).click();
    await page.getByLabel("邮箱", { exact: true }).fill("second@example.test");
    await page
      .getByLabel("密码", { exact: true })
      .fill("browser-test-password");
    await page
      .locator("form")
      .getByRole("button", { name: "进入工作台", exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name: "今天想研究哪只股票？" }),
    ).toBeVisible();
    const response = page.waitForResponse((r) =>
      r.url().endsWith("/api/threads/thread"),
    );
    release();
    await (await response).finished();
    await page.waitForTimeout(100);
    await expect(
      page.getByRole("heading", { name: "今天想研究哪只股票？" }),
    ).toBeVisible();
    await expect(
      page.getByText("分析 NVDA 的历史价格走势。", { exact: true }),
    ).toHaveCount(0);
    await expect(page.locator(".canvas-toggle")).toContainText("0");
    await expect(page.locator(".app [role=alert]")).toHaveCount(0);
  });
}

test("new research supersedes a delayed history selection", async ({
  page,
}) => {
  await terminal(page);
  let release!: () => void;
  const gate = new Promise<void>((r) => {
    release = r;
  });
  let started = false;
  await page.route("**/api/threads/thread", async (r) => {
    started = true;
    await gate;
    await r.fallback();
  });
  await page.goto("/");
  await page
    .getByRole("navigation", { name: "研究记录", exact: true })
    .getByRole("button")
    .click();
  await expect.poll(() => started).toBe(true);
  await page.getByRole("button", { name: "＋ 新建研究", exact: true }).click();
  await page
    .getByRole("textbox", { name: "研究问题", exact: true })
    .fill("新的 TSLA 研究草稿");
  const response = page.waitForResponse((r) =>
    r.url().endsWith("/api/threads/thread"),
  );
  release();
  await (await response).finished();
  await page.waitForTimeout(100);
  await expect(
    page.getByRole("heading", { name: "今天想研究哪只股票？" }),
  ).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "研究问题", exact: true }),
  ).toHaveValue("新的 TSLA 研究草稿");
  await expect(page.locator(".canvas-toggle")).toContainText("0");
});

test("an older save cannot finish or unlock a newer editor's pending save", async ({
  page,
}) => {
  await terminal(page);
  const releases: Array<() => void> = [];
  const submissions: string[] = [];
  await page.route("**/api/portfolios", async (r) => {
    if (r.request().method() !== "POST") return r.fallback();
    const index = submissions.length;
    submissions.push(r.request().postDataJSON().positions[0].symbol);
    await new Promise<void>((resolve) => releases.push(resolve));
    await r.fulfill(
      index === 0
        ? { status: 503, json: { error: "service_unavailable" } }
        : { json: { id: "p", snapshotId: "new-snapshot" } },
    );
  });
  await page.goto("/");
  const edit = page.getByRole("button", { name: "编辑模拟持仓 ↗" });
  for (const symbol of ["NVDA", "TSLA"]) {
    await edit.click();
    await page.getByLabel("持仓日期").fill("2026-11-06");
    await page.getByLabel("股票与数量").fill(symbol + " 10");
    await page.getByRole("button", { name: "保存新快照", exact: true }).click();
    await expect.poll(() => submissions.includes(symbol)).toBe(true);
    if (symbol === "NVDA") await page.keyboard.press("Escape");
  }
  const old = page.waitForResponse(
    (r) => r.url().endsWith("/api/portfolios") && r.status() === 503,
  );
  releases[0]!();
  await (await old).finished();
  await page.waitForTimeout(100);
  await expect(page.getByLabel("股票与数量")).toHaveValue("TSLA 10");
  await expect(
    page.getByRole("button", { name: "正在保存…", exact: true }),
  ).toHaveAttribute("aria-disabled", "true");
  await expect(page.getByRole("dialog").getByRole("alert")).toHaveCount(0);
  releases[1]!();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(submissions).toEqual(["NVDA", "TSLA"]);
});

test("a previous login's delayed research list cannot restore cleared history", async ({
  page,
}) => {
  await terminal(page);
  let release!: () => void;
  const gate = new Promise<void>((r) => {
    release = r;
  });
  let reads = 0;
  await page.route("**/api/threads", async (r) => {
    reads++;
    if (reads === 1) {
      await gate;
      return r.fallback();
    }
    return r.fulfill({ json: [] });
  });
  await page.route("**/api/auth/logout", (r) =>
    r.fulfill({ json: { ok: true } }),
  );
  // Re-enter the same account: an owner-ID comparison alone is insufficient.
  await page.route("**/api/auth/login", (r) =>
    r.fulfill({ json: { id: "owner", email: "research@example.test" } }),
  );
  await page.goto("/");
  await expect.poll(() => reads).toBe(1);
  await page.getByRole("button", { name: "退出", exact: true }).click();
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByLabel("邮箱", { exact: true }).fill("research@example.test");
  await page.getByLabel("密码", { exact: true }).fill("browser-test-password");
  await page
    .locator("form")
    .getByRole("button", { name: "进入工作台", exact: true })
    .click();
  await expect.poll(() => reads).toBe(2);
  const response = page.waitForResponse((r) =>
    r.url().endsWith("/api/threads"),
  );
  release();
  await (await response).finished();
  await page.waitForTimeout(100);
  await expect(
    page
      .getByRole("navigation", { name: "研究记录", exact: true })
      .getByRole("button"),
  ).toHaveCount(0);
  await expect(page.getByText("尚无研究记录", { exact: true })).toBeVisible();
});
