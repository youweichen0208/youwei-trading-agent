import { readFileSync } from "node:fs";
import { expect, it } from "vitest";
import { experimentalModelPageSchema, experimentalModelQuerySchema } from "../src/index.js";

const page = JSON.parse(readFileSync(new URL("../fixtures/experimental-model-page.json", import.meta.url), "utf8"));
it("accepts Python-generated experimental metadata with exact decimal strings", () => {
  expect(experimentalModelPageSchema.parse(page)).toEqual(page);
  expect(experimentalModelPageSchema.parse({items: [], next_cursor: null}).items).toEqual([]);
});
it("rejects qualified status, native text, ambiguous decimals and cursor inconsistencies", () => {
  const row = page.items[0];
  for (const change of [{model_text: "private"}, {qualification: "VERIFIED"}, {reserved_window_mae: "1e-3"},
    {reserved_window_mae: "-0.1"}, {training_samples: 0}, {id: "lightgbm-experimental-" + "0".repeat(64)}]) {
    expect(experimentalModelPageSchema.safeParse({items: [{...row, ...change}], next_cursor: null}).success).toBe(false);
  }
  expect(experimentalModelPageSchema.safeParse({items: [row, row], next_cursor: null}).success).toBe(false);
  expect(experimentalModelPageSchema.safeParse({items: [], next_cursor: row.id}).success).toBe(false);
});
it("accepts only bounded scalar query strings and supported horizons", () => {
  expect(experimentalModelQuerySchema.parse({limit: "50", horizon_sessions: "20"})).toEqual({limit: 50, horizon_sessions: "20"});
  for (const value of [{limit: 2}, {limit: "51"}, {limit: ["1", "2"]}, {limit: "01"},
    {data_mode: "REPLAY"}, {horizon_sessions: "2"}, {cursor: "../file"}]) {
    expect(experimentalModelQuerySchema.safeParse(value).success).toBe(false);
  }
});
