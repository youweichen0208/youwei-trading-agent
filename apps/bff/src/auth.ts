import { createHash, randomBytes, timingSafeEqual } from "node:crypto";
import type { Request, Response } from "express";
import type { Pool } from "pg";
import { loginRequestSchema } from "@yta/contracts";
import type { Config } from "./config.js";
import { ApiError } from "./errors.js";
import { LoginLimiter } from "./login-limiter.js";
import { verifyPassword } from "./password.js";
export { hashPassword } from "./password.js";

export const digest = (value: string) => createHash("sha256").update(value).digest("hex");

export interface Identity { ownerId: string; email: string; tokenHash: string; csrfToken: string; }

export class Auth {
  readonly cookieName: string;
  private readonly secure: boolean;
  private readonly limiter: LoginLimiter;
  constructor(private pool: Pool, private config: Config) {
    this.limiter = new LoginLimiter(pool);
    this.secure = ["production", "staging"].includes(config.YTA_ENV);
    this.cookieName = this.secure ? "__Host-yta_session" : "yta_session";
  }

  checkOrigin(request: Pick<Request, "headers">) {
    if (request.headers.origin !== new URL(this.config.YTA_WEB_ORIGIN).origin) {
      throw new ApiError("ORIGIN_REJECTED", "Request origin is not allowed", 403);
    }
  }

  async login(request: Request, response: Response, body: unknown) {
    this.checkOrigin(request);
    const data = loginRequestSchema.parse(body);
    // The direct peer comes from Express; forwarded headers are not trusted by this BFF.
    // Count successful and failed password checks, across processes and restarts.
    await this.limiter.consume(request.ip ?? "unknown", data.email);
    const now = Date.now();
    const result = await this.pool.query(
      "SELECT u.id, u.email, c.id AS credential_id, c.password_hash FROM yta.users u " +
      "JOIN LATERAL (SELECT * FROM yta.login_credentials WHERE owner_id=u.id ORDER BY id DESC LIMIT 1) c ON true WHERE lower(u.email)=$1",
      [data.email],
    );
    const user = result.rows[0];
    const valid = await verifyPassword(data.password, user?.password_hash);
    if (!user || !valid) throw new ApiError("INVALID_CREDENTIALS", "Email or password is incorrect", 401);
    const token = randomBytes(32).toString("base64url");
    await this.pool.query("INSERT INTO yta.login_sessions(token_hash,owner_id,credential_id,expires_at) VALUES ($1,$2,$3,$4)", [digest(token), user.id, user.credential_id, new Date(now + 12 * 3600000)]);
    response.cookie(this.cookieName, token, { httpOnly: true, secure: this.secure, sameSite: "strict", path: "/", maxAge: 12 * 3600000 });
    return { user: { id: user.id, email: user.email }, csrf_token: digest(`csrf:${token}`), data_mode: this.config.YTA_DATA_MODE };
  }

  async require(request: Pick<Request, "headers">): Promise<Identity> {
    const cookies = (request.headers.cookie ?? "").split(";").map(v => v.trim()).filter(v => v.startsWith(`${this.cookieName}=`));
    if (cookies.length !== 1) throw new ApiError("UNAUTHORIZED", "Login required", 401);
    const token = cookies[0]!.slice(this.cookieName.length + 1);
    if (!/^[\w-]{43}$/.test(token)) throw new ApiError("UNAUTHORIZED", "Login required", 401);
    const tokenHash = digest(token);
    const result = await this.pool.query(
      "SELECT s.owner_id,u.email FROM yta.login_sessions s JOIN yta.users u ON u.id=s.owner_id " +
      "WHERE s.token_hash=$1 AND s.expires_at>now() AND NOT EXISTS " +
      "(SELECT 1 FROM yta.login_revocations r WHERE r.token_hash=s.token_hash) " +
      "AND s.credential_id=(SELECT max(c.id) FROM yta.login_credentials c WHERE c.owner_id=s.owner_id)", [tokenHash],
    );
    const row = result.rows[0];
    if (!row) throw new ApiError("UNAUTHORIZED", "Login expired or revoked", 401);
    return { ownerId: row.owner_id, email: row.email, tokenHash, csrfToken: digest(`csrf:${token}`) };
  }

  async mutation(request: Pick<Request, "headers">) {
    const identity = await this.require(request);
    this.checkOrigin(request);
    const csrf = request.headers["x-csrf-token"];
    if (typeof csrf !== "string" || !/^[a-f0-9]{64}$/.test(csrf) || !timingSafeEqual(Buffer.from(csrf), Buffer.from(identity.csrfToken))) {
      throw new ApiError("CSRF_REJECTED", "CSRF token is missing or invalid", 403);
    }
    return identity;
  }

  canReviewModels(identity: Identity): boolean {
    return this.config.YTA_EXPERIMENTAL_MODELS === "ENABLED"
      && this.config.YTA_MODEL_REVIEWER_IDS.includes(identity.ownerId);
  }

  async requireModelReviewer(request: Pick<Request, "headers">) {
    const identity = await this.require(request);
    if (!this.canReviewModels(identity)) {
      throw new ApiError("MODEL_REVIEW_FORBIDDEN", "Model review permission is required", 403);
    }
    return identity;
  }

  async modelReviewMutation(request: Pick<Request, "headers">) {
    const identity = await this.mutation(request);
    if (!this.canReviewModels(identity)) {
      throw new ApiError("MODEL_REVIEW_FORBIDDEN", "Model review permission is required", 403);
    }
    return identity;
  }

  async logout(request: Request, response: Response) {
    const identity = await this.mutation(request);
    await this.pool.query("INSERT INTO yta.login_revocations(token_hash) VALUES ($1) ON CONFLICT DO NOTHING", [identity.tokenHash]);
    response.clearCookie(this.cookieName, { httpOnly: true, secure: this.secure, sameSite: "strict", path: "/" });
    return { ok: true };
  }
}
