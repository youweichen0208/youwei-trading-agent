import { randomBytes, scrypt, timingSafeEqual } from "node:crypto";
import { ApiError } from "./errors.js";

// Bound submitted work, including libuv's queue, across all Auth instances in this process.
// Each scrypt uses roughly 32 MiB; do not build an unbounded queue holding passwords.
const MAX_ACTIVE = 4;
let active = 0;
// Missing users still incur the same bounded derivation. This is not an account credential.
const DUMMY_HASH = `scrypt-v1:${"0".repeat(32)}:${"0".repeat(128)}`;

async function derive(password: string, salt: string): Promise<Buffer> {
  if (active >= MAX_ACTIVE) throw new ApiError("AUTH_BUSY", "Login is busy; try again shortly", 503, true);
  active++;
  try {
    return await new Promise<Buffer>((resolve, reject) => {
      scrypt(password, salt, 64, { N: 32768, r: 8, p: 1, maxmem: 64 * 1024 * 1024 },
        (error, key) => error ? reject(error) : resolve(key));
    });
  } finally { active--; }
}

export async function hashPassword(password: string) {
  const salt = randomBytes(16).toString("hex");
  return `scrypt-v1:${salt}:${(await derive(password, salt)).toString("hex")}`;
}

export async function verifyPassword(password: string, encoded?: string) {
  const [version, salt, expected] = (encoded ?? DUMMY_HASH).split(":");
  if (version !== "scrypt-v1" || !salt || !expected || !/^[a-f0-9]{128}$/.test(expected)) return false;
  const matches = timingSafeEqual(await derive(password, salt), Buffer.from(expected, "hex"));
  return encoded !== undefined && matches;
}
