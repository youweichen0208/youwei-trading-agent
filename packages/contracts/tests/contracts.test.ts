import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { canvasCommandSchema, chartDataSchema, forecastSchema, freezeRequestSchema } from "../src/index.js";

describe("boundary contracts", () => {
  it("validates the generated Python baseline response without coercing decimals", () => {
    const data = JSON.parse(readFileSync(new URL("../fixtures/forecast.json", import.meta.url), "utf8"));
    const forecast = forecastSchema.parse(data);
    expect(forecast.expected_return).toBe("0.005000000000");
    expect(forecast.data_mode).toBe("REPLAY");
    expect(forecastSchema.safeParse({ ...data, expected_return: 0.005 }).success).toBe(false);
    expect(forecastSchema.safeParse({ ...data, probability_up: 0.9 }).success).toBe(false);
  });

  it("rejects forged prediction values and missing explicit confirmation", () => {
    const valid = { draft_id: "0193f00a-1234-7000-8000-000000000001", confirmed: true };
    expect(freezeRequestSchema.safeParse(valid).success).toBe(true);
    expect(freezeRequestSchema.safeParse({ ...valid, expected_return: "1.0" }).success).toBe(false);
    expect(freezeRequestSchema.safeParse({ ...valid, confirmed: "true" }).success).toBe(false);
    expect(freezeRequestSchema.safeParse({ ...valid, confirmed: false }).success).toBe(false);
  });

  it("rejects arbitrary executable canvas payloads", () => {
    const command = {
      command_id: "0193f00a-1234-7000-8000-000000000001",
      session_id: "0193f00a-1234-7000-8000-000000000002",
      run_id: "0193f00a-1234-7000-8000-000000000003",
      version: 1, type: "chart.show",
      payload: { symbol: "NVDA", timeframe: "1D", indicators: ["RSI14"], as_of: "2024-11-22T21:10:00Z", data_mode: "REPLAY" },
    };
    expect(canvasCommandSchema.safeParse(command).success).toBe(true);
    expect(canvasCommandSchema.safeParse({ ...command, type: "eval" }).success).toBe(false);
    expect(canvasCommandSchema.safeParse({ ...command, payload: { ...command.payload, html: "<script>alert(1)</script>" } }).success).toBe(false);
  });
});



describe("chart snapshot contract", () => {
  const data = JSON.parse(readFileSync(new URL("../fixtures/chart.json", import.meta.url), "utf8"));
  it("reads Alpha chart bars and series from one split-adjusted snapshot", () => {
    expect(chartDataSchema.parse(data).bars).toHaveLength(21);
    expect(data.bars[0].volume).toBeTypeOf("string");
  });
  it("rejects raw price basis, mismatched sessions and missing chart series", () => {
    expect(chartDataSchema.safeParse({...data, adjustment: "RAW"}).success).toBe(false);
    expect(chartDataSchema.safeParse({...data, sessions: data.sessions.slice(1)}).success).toBe(false);
    expect(chartDataSchema.safeParse({...data, bars: [...data.bars].reverse()}).success).toBe(false);
    expect(chartDataSchema.safeParse({...data, share_basis_session: "2024-01-01"}).success).toBe(false);
    expect(chartDataSchema.safeParse({...data, series: {...data.series, SMA20: []}}).success).toBe(false);
    const {MACD: _, ...series} = data.series;
    expect(chartDataSchema.safeParse({...data, series}).success).toBe(false);
  });
});
