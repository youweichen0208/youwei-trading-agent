import { expect, it, vi } from "vitest";

const scrypt = vi.hoisted(() => vi.fn());
vi.mock("node:crypto", async importOriginal => ({
  ...await importOriginal<typeof import("node:crypto")>(), scrypt,
}));
import { hashPassword } from "../src/password.js";

it("releases capacity after synchronous and callback failures from the crypto backend", async () => {
  const native = await vi.importActual<typeof import("node:crypto")>("node:crypto");
  scrypt.mockImplementationOnce(() => { throw new Error("synthetic backend failure"); });
  await expect(hashPassword("test-only-password")).rejects.toThrow("synthetic backend failure");
  scrypt.mockImplementationOnce((_password, _salt, _length, _options, callback) => {
    callback(new Error("synthetic callback failure"));
  });
  await expect(hashPassword("test-only-password")).rejects.toThrow("synthetic callback failure");
  scrypt.mockImplementation(native.scrypt);
  const recovered = await Promise.all(Array.from({length: 4}, () => hashPassword("test-only-password")));
  expect(recovered).toHaveLength(4);
  expect(new Set(recovered).size).toBe(4);
});
