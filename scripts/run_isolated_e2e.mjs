// Next regenerates this shared declaration even when distDir and tsconfig are isolated.
// Preserve it so a browser run does not leave development pointing at test artifacts.
import { readFile, writeFile } from "node:fs/promises";
import { spawn } from "node:child_process";

const declaration = new URL("../apps/web/next-env.d.ts", import.meta.url);
const original = await readFile(declaration);
try {
  process.exitCode = await new Promise((resolve, reject) => {
    const child = spawn("pnpm", ["exec", "playwright", "test", "--config", "playwright.isolated.config.ts", ...process.argv.slice(2)], {
      cwd: new URL("..", import.meta.url), stdio: "inherit",
    });
    child.once("error", reject);
    child.once("exit", code => resolve(code ?? 1));
  });
} finally {
  await writeFile(declaration, original);
}
