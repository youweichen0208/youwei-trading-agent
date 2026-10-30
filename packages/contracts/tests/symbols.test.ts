import { readFileSync } from "node:fs";
import { expect, it } from "vitest";
import { stockSymbolSchema } from "../src/requests.js";
import { forecastSchema } from "../src/index.js";

const cases = JSON.parse(readFileSync(new URL("../fixtures/symbol-inputs.json", import.meta.url), "utf8"));
it("normalizes the shared ASCII request examples to canonical NVDA", () => {
  for (const input of cases.accepted) expect(stockSymbolSchema.parse(input)).toBe("NVDA");
});
it("rejects unsupported securities and malformed or Unicode request spelling", () => {
  for (const input of [...cases.unsupported, ...cases.invalid]) expect(stockSymbolSchema.safeParse(input).success).toBe(false);
});
it("does not normalize immutable forecast symbols", () => {
  const forecast = JSON.parse(readFileSync(new URL("../fixtures/forecast.json", import.meta.url), "utf8"));
  expect(forecastSchema.safeParse({...forecast, symbol: "nvda"}).success).toBe(false);
});
