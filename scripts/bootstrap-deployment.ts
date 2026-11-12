// Explicit host operation. Credentials stay in the private deployment directory.
import { randomBytes } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { database } from "../packages/server/src/db.js";
import { AuthStore } from "../packages/server/src/auth.js";
import { AutoProfilePreparer } from "../packages/hermes-adapter/src/auto-preparation.js";
import { readProvisionSettings } from "../packages/hermes-adapter/src/provision.js";

const [root, image] = process.argv.slice(2);
if (
  root !== "/opt/hermes-alpha-lab" ||
  !/^sha256:[a-f0-9]{64}$/.test(image ?? "")
)
  throw new Error("Explicit deployment root and verified image required");
const pool = database();
try {
  const credentialFile = `${root}/private/initial-login.json`;
  let login: { email: string; password: string };
  try {
    login = JSON.parse(await readFile(credentialFile, "utf8"));
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    login = {
      email: "owner@alpha.local",
      password: randomBytes(24).toString("base64url"),
    };
    await writeFile(credentialFile, JSON.stringify(login) + "\n", {
      mode: 0o600,
      flag: "wx",
    });
  }
  const auth = new AuthStore(pool);
  const exists = await pool.query("SELECT id FROM users WHERE email=$1", [
    login.email,
  ]);
  const owner = exists.rowCount
    ? (await auth.login(login.email, login.password)).user
    : await auth.register(login.email, login.password);
  const model = await readProvisionSettings(
    `${root}/private/provider/claude-code-model.json`,
  );
  const settings = {
    image,
    gatewayOrigin: "http://host.docker.internal:3410",
    firstPort: 19000,
    models: { research: model, prediction: model, portfolio: model },
  };
  const settingsFile = `${root}/private/profile-settings.json`;
  try {
    await writeFile(settingsFile, JSON.stringify(settings) + "\n", {
      mode: 0o600,
      flag: "wx",
    });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
    if (
      JSON.stringify(await readProvisionSettings(settingsFile)) !==
      JSON.stringify(settings)
    )
      throw new Error("Existing deployment settings differ");
  }
  const preparer = await AutoProfilePreparer.open(
    pool,
    settings,
    `${root}/private/profiles`,
    19999,
  );
  const result = await preparer.prepare(owner.id);
  await writeFile(
    `${root}/private/initial-owner.json`,
    JSON.stringify(result) + "\n",
    { mode: 0o600 },
  );
  console.info("Initial account prepared", {
    ownerId: owner.id,
    status: result.status,
  });
  if (["blocked", "busy"].includes(result.status)) process.exitCode = 1;
} catch {
  console.error(
    "Deployment bootstrap refused; reconcile private state before retrying",
  );
  process.exitCode = 1;
} finally {
  await pool.end();
}
