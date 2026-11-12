// Synthetic evidence is confined to tests. Production never imports this module.
import { randomUUID, randomBytes } from "node:crypto";
import { readFile } from "node:fs/promises";
import { Pool } from "pg";
import { TaskStore, type Task } from "../../packages/server/src/tasks.js";
import { ToolCapabilities } from "../../packages/server/src/tool-context.js";
import { ToolGateway } from "../../packages/server/src/tool-gateway.js";
import { DataService } from "../../packages/server/src/data-service.js";

export async function completeFixtureResearch(
  email: string,
  afterFirstArtifact: (appendText: () => Promise<void>) => Promise<void>,
) {
  const url = process.env.TEST_DATABASE_URL;
  if (!url || !new URL(url).pathname.endsWith("_test"))
    throw new Error("Dedicated TEST_DATABASE_URL ending in _test required");
  const pool = new Pool({ connectionString: url }),
    store = new TaskStore(pool),
    worker = randomUUID();
  const rows: any[] = JSON.parse(
    await readFile(
      new URL("../fixtures/synthetic-market.json", import.meta.url),
      "utf8",
    ),
  );
  const gateway = new ToolGateway(
    pool,
    new DataService(pool, {
      source: "synthetic-test-only",
      version: "fixture-v1",
      feed: "fixture",
      async bars(q) {
        return rows.filter((r) => r.session >= q.start && r.session <= q.end);
      },
    }),
  );
  const cap = new ToolCapabilities(pool, randomBytes(32).toString("hex"));
  try {
    const owner = (
      await pool.query("SELECT id FROM users WHERE email=$1", [email])
    ).rows[0].id;
    const root = (await store.claim(owner, worker))!.task;
    async function bind(t: Task) {
      const profile = (
        await pool.query(
          "SELECT id FROM profile_instances WHERE owner_id=$1 AND role=$2",
          [owner, t.role],
        )
      ).rows[0].id;
      await store.prepareExecution(
        owner,
        t.id,
        worker,
        t.generation,
        { input: t.role },
        profile,
        randomUUID(),
      );
      const token = await cap.issue(owner, t.id, worker, t.generation);
      await store.beginSubmission(
        owner,
        t.id,
        worker,
        t.generation,
        "synthetic-test-runtime",
      );
      return ToolCapabilities.authenticate(pool, token);
    }
    const rootCtx = await bind(root);
    const fixtureRun = `fixture-${root.id}`;
    await store.bindUpstream(
      owner,
      root.id,
      worker,
      root.generation,
      fixtureRun,
    );
    let streamSequence = 0;
    const appendText = async () => {
      await store.archiveEvents(
        owner,
        root.id,
        worker,
        root.generation,
        fixtureRun,
        {
          events: [
            {
              sequence: streamSequence++,
              kind: "message.delta",
              text:
                streamSequence === 1 ? "已保存研究前半段" : "，继续研究后半段",
            },
          ],
          gap: false,
          closed: false,
          limited: false,
        },
      );
    };
    await appendText();
    const call = (tool: string, args: unknown) =>
      gateway.invoke(rootCtx, tool, args, randomUUID()) as Promise<any>;
    const history = {
      symbol: "NVDA",
      start: "2025-01-02",
      end: "2025-08-01",
      adjustment: "split",
    };
    const dataset = (await call("market_history", history)).result.datasetRef;
    await call("canvas_upsert", {
      component: "stock_chart",
      title: "NVDA 日线（测试数据）",
      datasetRef: dataset,
      props: { showVolume: true },
    });
    await afterFirstArtifact(appendText);
    const indicators = (
      await call("quant_calculate", { kind: "indicators", datasetRef: dataset })
    ).result.resultRef;
    await call("canvas_upsert", {
      component: "indicator_panel",
      title: "MA20 与 RSI14",
      resultRef: indicators,
    });
    const summary = await call("save_research_summary", {
      text: "测试研究解释；数据仅用于验证图表和恢复流程。",
      datasetRefs: [dataset],
      resultRefs: [indicators],
    });
    await call("canvas_upsert", {
      component: "research_summary",
      title: "研究解释",
      resultRef: summary.resultRef,
    });
    for (const role of ["prediction", "portfolio"] as const) {
      const ref =
        role === "prediction"
          ? dataset
          : (await call("market_history", { ...history, adjustment: "raw" }))
              .result.datasetRef;
      await call("submit_specialist", {
        role,
        requestKey: role,
        question: role,
        datasetRefs: [ref],
        ...(role === "portfolio"
          ? { portfolioSnapshotRef: root.input.portfolioSnapshotRef }
          : {}),
      });
      const child = (await store.claim(owner, worker))!.task,
        ctx = await bind(child);
      const result: any = await gateway.invoke(
        ctx,
        "quant_calculate",
        role === "prediction"
          ? { kind: role, datasetRef: ref }
          : {
              kind: role,
              portfolioSnapshotRef: root.input.portfolioSnapshotRef,
              datasetRefs: [ref],
            },
        randomUUID(),
      );
      await gateway.invoke(
        ctx,
        "canvas_upsert",
        {
          component:
            role === "prediction" ? "prediction_result" : "portfolio_summary",
          title: role === "prediction" ? "探索性预测" : "模拟组合",
          resultRef: result.result.resultRef,
        },
        randomUUID(),
      );
      await store.finish(
        owner,
        child.id,
        worker,
        child.generation,
        "completed",
        { resultRefs: [result.result.resultRef] },
      );
    }
    await store.finish(owner, root.id, worker, root.generation, "completed", {
      text: "五类产物已保存（测试数据）。",
    });
    return root;
  } finally {
    await pool.end();
  }
}
