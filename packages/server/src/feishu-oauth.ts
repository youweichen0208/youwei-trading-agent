import { createHash, randomBytes } from "node:crypto";
import type { Pool } from "pg";
import { AuthStore } from "./auth.js";
import { FeishuIdentityStore } from "./feishu-identity.js";
import { DomainError } from "./tasks.js";
const hash = (v: string) => createHash("sha256").update(v).digest("hex");
export class FeishuOAuth {
  constructor(
    readonly pool: Pool,
    readonly config: {
      appId: string;
      appSecret: string;
      origin: string;
      allowRegistration: boolean;
      limit?: number;
    },
    readonly request: typeof fetch = fetch,
  ) {}
  get redirectUri() {
    return new URL("/api/auth/feishu/oauth-callback", this.config.origin).href;
  }
  async start() {
    await new AuthStore(this.pool).rateLimit("feishu-oauth-start", 100);
    const state = randomBytes(32).toString("hex"),
      browser = randomBytes(32).toString("hex"),
      verifier = randomBytes(32).toString("base64url");
    await this.pool.query(
      "INSERT INTO feishu_oauth_states(state_hash,browser_hash,verifier) VALUES($1,$2,$3)",
      [hash(state), hash(browser), verifier],
    );
    const url = new URL(
      "https://accounts.feishu.cn/open-apis/authen/v1/authorize",
    );
    url.search = new URLSearchParams({
      client_id: this.config.appId,
      response_type: "code",
      redirect_uri: this.redirectUri,
      state,
      code_challenge: createHash("sha256").update(verifier).digest("base64url"),
      code_challenge_method: "S256",
    }).toString();
    return { url: url.href, browser };
  }
  async callback(state: string, browser: string, code: string) {
    if (
      !/^[a-f0-9]{64}$/.test(state) ||
      !/^[a-f0-9]{64}$/.test(browser) ||
      !code ||
      code.length > 2048
    )
      throw new DomainError("oauth_state_invalid", 400);
    const row = (
      await this.pool.query(
        "UPDATE feishu_oauth_states SET consumed_at=now() WHERE state_hash=$1 AND browser_hash=$2 AND expires_at>now() AND consumed_at IS NULL RETURNING verifier",
        [hash(state), hash(browser)],
      )
    ).rows[0];
    if (!row) throw new DomainError("oauth_state_invalid", 400);
    // Endpoint and JSON contract follow the installed official Feishu SDK's AccessToken implementation.
    const response = await this.request(
      "https://accounts.feishu.cn/oauth/v3/token",
      {
        method: "POST",
        redirect: "error",
        signal: AbortSignal.timeout(15000),
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          client_id: this.config.appId,
          client_secret: this.config.appSecret,
          grant_type: "authorization_code",
          code,
          redirect_uri: this.redirectUri,
          code_verifier: row.verifier,
        }),
      },
    );
    const token = await response.json();
    if (
      !response.ok ||
      token.error ||
      (token.code && token.code !== "0") ||
      typeof token.access_token !== "string"
    )
      throw new DomainError("oauth_exchange_failed", 502);
    const info = await this.request(
      "https://open.feishu.cn/open-apis/authen/v1/user_info",
      {
        redirect: "error",
        signal: AbortSignal.timeout(15000),
        headers: { Authorization: `Bearer ${token.access_token}` },
      },
    );
    const identity = await info.json();
    if (
      !info.ok ||
      identity.code !== 0 ||
      typeof identity.data?.open_id !== "string"
    )
      throw new DomainError("oauth_identity_failed", 502);
    const user = await new FeishuIdentityStore(this.pool).resolve(
      this.config.appId,
      identity.data.open_id,
      {
        allowRegistration: this.config.allowRegistration,
        limit: this.config.limit,
        displayName: identity.data.name,
      },
    );
    const session = randomBytes(32).toString("hex");
    await this.pool.query(
      "INSERT INTO login_sessions(token_hash,owner_id,expires_at) VALUES($1,$2,now()+interval '7 days')",
      [hash(session), user.id],
    );
    return session;
  }
}
