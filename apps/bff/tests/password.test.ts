import { expect, it } from "vitest";
import { hashPassword, verifyPassword } from "../src/password.js";

it("bounds real password computations and recovers capacity after completion", async () => {
  const encoded = await hashPassword("test-only-password");
  const results = await Promise.allSettled(Array.from({length: 12}, (_, i) =>
    i % 2 === 0 ? verifyPassword("test-only-password", encoded) : verifyPassword("unknown-password")));
  const completed = results.filter(result => result.status === "fulfilled");
  expect(completed.map(result => result.value)).toEqual([true, false, true, false]);
  const rejected = results.filter(result => result.status === "rejected");
  expect(rejected).toHaveLength(8);
  for (const result of rejected) expect(result.reason).toMatchObject({code: "AUTH_BUSY", status: 503, retryable: true});
  expect(await verifyPassword("test-only-password", encoded)).toBe(true);
  expect(await verifyPassword("wrong-password", encoded)).toBe(false);
  expect(await verifyPassword("unknown-password")).toBe(false);
});

it("shares the capacity between credential creation and verification", async () => {
  const pending = Array.from({length: 4}, () => hashPassword("test-only-password"));
  await expect(verifyPassword("unknown-password")).rejects.toMatchObject({code: "AUTH_BUSY"});
  await expect(hashPassword("test-only-password")).rejects.toMatchObject({code: "AUTH_BUSY"});
  const hashes = await Promise.all(pending);
  expect(new Set(hashes).size).toBe(4);
  expect(await verifyPassword("test-only-password", hashes[0])).toBe(true);
});
