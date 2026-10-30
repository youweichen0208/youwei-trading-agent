import { expect, it } from "vitest";
import { eventQuerySchema, idempotencyKeySchema, loginRequestSchema, messageQuerySchema, pageQuerySchema, predictionQuerySchema } from "../src/index.js";

it("preserves strict HTTP pagination boundaries without permissive coercion", () => {
  for (const limit of ["1", "25", "99", "100"]) expect(pageQuerySchema.parse({limit}).limit).toBe(Number(limit));
  for (const limit of ["0", "101", "01", "1.0", "1e2", "+1", " 1", "1 ", ["1", "2"], 1]) {
    expect(pageQuerySchema.safeParse({limit}).success).toBe(false);
  }
  for (const schema of [pageQuerySchema, predictionQuerySchema, messageQuerySchema, eventQuerySchema]) {
    expect(schema.safeParse({owner_id: "forged"}).success).toBe(false);
    expect(schema.safeParse({data_mode: "LIVE_RESEARCH"}).success).toBe(false);
    expect(schema.safeParse({cursor: ["0", "1"]}).success).toBe(false);
  }
  expect(predictionQuerySchema.parse({status: "UNSCORABLE"}).status).toBe("UNSCORABLE");
  expect(pageQuerySchema.safeParse({status: "SETTLED"}).success).toBe(false);
});

it("keeps bigint message cursors exact and separates them from UUID event cursors", () => {
  expect(messageQuerySchema.parse({}).cursor).toBe("0");
  expect(messageQuerySchema.parse({cursor: "999999999999999999"}).cursor).toBe("999999999999999999");
  for (const cursor of ["-1", "1.1", "1000000000000000000", ""]) expect(messageQuerySchema.safeParse({cursor}).success).toBe(false);
  expect(eventQuerySchema.safeParse({cursor: "1"}).success).toBe(false);
  expect(eventQuerySchema.parse({cursor: "0193f00a-1234-7000-8000-000000000001"}).cursor).toBeDefined();
});

it("normalizes email only and keeps credentials and idempotency keys bounded", () => {
  expect(loginRequestSchema.parse({email: "TEST@EXAMPLE.COM", password: "  password  "}))
    .toEqual({email: "test@example.com", password: "  password  "});
  expect(loginRequestSchema.safeParse({email: "test@example.com", password: "a".repeat(257)}).success).toBe(false);
  expect(loginRequestSchema.safeParse({email: "test@example.com", password: "valid", owner_id: "forged"}).success).toBe(false);
  expect(idempotencyKeySchema.safeParse("a".repeat(128)).success).toBe(true);
  for (const value of ["", "a".repeat(129), "has space", ["a", "b"]]) expect(idempotencyKeySchema.safeParse(value).success).toBe(false);
});
