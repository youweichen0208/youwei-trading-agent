import { test } from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { toolDescriptors } from "../../packages/server/src/tool-gateway.js";
import { quantInput } from "../../packages/server/src/data-service.js";
import {
  canvasUpsert,
  canvasModelInput,
} from "../../packages/server/src/canvas-store.js";

const roles = ["research", "prediction", "portfolio"] as const;
test("generated role catalogs preserve the current model-facing schemas", async () => {
  const catalog = JSON.parse(
    await readFile(
      new URL("../../services/hermes/alpha_hermes/tools.json", import.meta.url),
      "utf8",
    ),
  );
  for (const role of roles) {
    const tools = toolDescriptors(role);
    assert.deepEqual(catalog[role], tools);
    for (const tool of tools) {
      assert.equal(tool.inputSchema.anyOf, undefined);
      assert.equal(tool.inputSchema.oneOf, undefined);
      assert.equal(tool.inputSchema.allOf, undefined);
    }
    const quant = tools.find((t) => t.name === "quant_calculate")!.inputSchema;
    const kind = quant.properties!.kind;
    assert.ok(kind && typeof kind === "object");
    assert.equal(kind.const, role === "research" ? "indicators" : role);
    if (role === "portfolio") {
      assert.ok(quant.required!.includes("portfolioSnapshotRef"));
      assert.ok(quant.required!.includes("datasetRefs"));
      assert.equal(quant.properties!.datasetRef, undefined);
    } else {
      assert.ok(quant.required!.includes("datasetRef"));
      assert.equal(quant.properties!.portfolioSnapshotRef, undefined);
    }
  }
});

test("plain model-facing Canvas schemas retain authoritative reference and role validation", () => {
  const datasetRef = randomUUID(),
    resultRef = randomUUID();
  const chart = { component: "stock_chart", title: "NVDA", datasetRef };
  assert.ok(canvasModelInput("research").safeParse(chart).success);
  assert.ok(canvasUpsert.safeParse(chart).success);
  assert.equal(canvasModelInput("prediction").safeParse(chart).success, false);
  assert.equal(canvasUpsert.safeParse({ ...chart, resultRef }).success, false);
  assert.equal(
    canvasUpsert.safeParse({ component: "stock_chart", title: "NVDA" }).success,
    false,
  );
  assert.equal(
    canvasUpsert.safeParse({ ...chart, artifactId: "stock_chart" }).success,
    false,
  );
  assert.equal(
    quantInput.safeParse({ kind: "prediction", datasetRefs: [datasetRef] })
      .success,
    false,
  );
  assert.equal(
    quantInput.safeParse({ kind: "prediction", datasetRef, prices: [1, 2] })
      .success,
    false,
  );
  assert.ok(quantInput.safeParse({ kind: "prediction", datasetRef }).success);
});
