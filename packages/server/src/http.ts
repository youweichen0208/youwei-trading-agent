import type { Pool } from "pg";
import { z } from "zod";
import { FeishuOAuth } from "./feishu-oauth.js";
import { FeishuLoginStore } from "./feishu-login.js";
import { ExecutionContextStore } from "./execution-context.js";
import { AuthStore } from "./auth.js";
import { BusinessStore, holdings, uuid, symbol } from "./business.js";
import { DomainError, TaskStore } from "./tasks.js";
import { ResearchAPI } from "./research-api.js";

const credentials = z
  .object({ email: z.string().max(254), password: z.string().max(256) })
  .strict();
const research = z
  .object({
    threadId: uuid,
    input: z.string().trim().min(1).max(16000),
    portfolioSnapshotRef: uuid.optional(),
  })
  .strict();
const cookieName = "alpha_session";
export function createHandler(pool: Pool, origin: string) {
  const allowed = new URL(origin).origin;
  if (process.env.NODE_ENV === "production" && !allowed.startsWith("https:"))
    throw new Error("HTTPS APP_ORIGIN required in production");
  const auth = new AuthStore(pool),
    tasks = new TaskStore(pool),
    business = new BusinessStore(pool);
  const json = (data: unknown, status = 200) =>
    Response.json(data, {
      status,
      headers: {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
      },
    });
  const cookie = (value: string, maxAge = 604800) =>
    `${cookieName}=${value}; HttpOnly; SameSite=Strict; Path=/; Max-Age=${maxAge}${allowed.startsWith("https:") ? "; Secure" : ""}`;
  async function body(req: Request) {
    if (!req.headers.get("content-type")?.startsWith("application/json"))
      throw new DomainError("json_required", 415);
    const reader = req.body?.getReader();
    if (!reader) throw new DomainError("invalid_body", 400);
    const chunks: Uint8Array[] = [];
    let size = 0;
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > 65536) {
          await reader.cancel();
          throw new DomainError("body_too_large", 413);
        }
        chunks.push(value);
      }
    } finally {
      reader.releaseLock();
    }
    try {
      return JSON.parse(Buffer.concat(chunks).toString("utf8"));
    } catch {
      throw new DomainError("invalid_json", 400);
    }
  }
  return async function handle(req: Request): Promise<Response> {
    try {
      const url = new URL(req.url),
        path = url.pathname.split("/").filter(Boolean).slice(1),
        method = req.method;
      if (!["GET", "POST", "PUT", "DELETE"].includes(method))
        return json({ error: "method_not_allowed" }, 405);
      if (method !== "GET" && req.headers.get("origin") !== allowed)
        throw new DomainError("invalid_origin", 403);
      if (path.join("/") === "auth/options" && method === "GET")
        return json({
          feishuLogin: process.env.FEISHU_LOGIN_ENABLED === "1",
          feishuOAuth: process.env.FEISHU_OAUTH_ENABLED === "1",
        });
      if (
        path[0] === "auth" &&
        path[1] === "feishu" &&
        path.length === 3 &&
        process.env.FEISHU_OAUTH_ENABLED === "1"
      ) {
        const appId = process.env.FEISHU_APP_ID,
          appSecret = process.env.FEISHU_APP_SECRET;
        if (!appId || !appSecret)
          throw new DomainError("oauth_not_configured", 503);
        const oauth = new FeishuOAuth(pool, {
          appId,
          appSecret,
          origin: allowed,
          allowRegistration: process.env.FEISHU_ADMISSION_ENABLED === "1",
          limit: Number(process.env.FEISHU_TRIAL_LIMIT ?? 10),
        });
        if (path[2] === "oauth-start" && method === "POST") {
          const started = await oauth.start();
          const res = json({ url: started.url });
          res.headers.set(
            "Set-Cookie",
            `alpha_feishu_oauth=${started.browser}; HttpOnly; SameSite=Lax; Path=/api/auth/feishu; Max-Age=300${allowed.startsWith("https:") ? "; Secure" : ""}`,
          );
          return res;
        }
        if (path[2] === "oauth-callback" && method === "GET") {
          const browser =
            req.headers
              .get("cookie")
              ?.split(";")
              .map((s) => s.trim())
              .find((s) => s.startsWith("alpha_feishu_oauth="))
              ?.slice("alpha_feishu_oauth=".length) ?? "";
          const session = await oauth.callback(
            url.searchParams.get("state") ?? "",
            browser,
            url.searchParams.get("code") ?? "",
          );
          return new Response(null, {
            status: 303,
            headers: {
              Location: allowed + "/",
              "Set-Cookie": cookie(session),
              "Cache-Control": "no-store",
              "Referrer-Policy": "no-referrer",
            },
          });
        }
      }
      const token = req.headers
        .get("cookie")
        ?.split(";")
        .map((s) => s.trim())
        .find((s) => s.startsWith(`${cookieName}=`))
        ?.slice(cookieName.length + 1);
      if (
        path[0] === "auth" &&
        path[1] === "feishu" &&
        path.length === 3 &&
        process.env.FEISHU_LOGIN_ENABLED === "1"
      ) {
        const store = new FeishuLoginStore(pool);
        const browser =
          req.headers
            .get("cookie")
            ?.split(";")
            .map((s) => s.trim())
            .find((s) => s.startsWith("alpha_feishu_browser="))
            ?.slice("alpha_feishu_browser=".length) ?? "";
        if (
          ["challenge", "bind-challenge"].includes(path[2]!) &&
          method === "POST"
        ) {
          const bindingOwner =
            path[2] === "bind-challenge"
              ? (await auth.authenticate(token)).id
              : undefined;
          const challenge = await store.create(bindingOwner);
          const res = json(
            {
              id: challenge.id,
              command: challenge.command,
              expiresIn: challenge.expiresIn,
            },
            201,
          );
          res.headers.set(
            "Set-Cookie",
            `alpha_feishu_browser=${challenge.browser}; HttpOnly; SameSite=Strict; Path=/api/auth/feishu; Max-Age=300${allowed.startsWith("https:") ? "; Secure" : ""}`,
          );
          return res;
        }
        if (path[2] === "status" && method === "GET")
          return json(
            await store.status(uuid.parse(url.searchParams.get("id")), browser),
          );
        if (path[2] === "complete" && method === "POST") {
          const data = z
            .object({ id: uuid })
            .strict()
            .parse(await body(req));
          const session = await store.complete(
            data.id,
            browser,
            token ? (await auth.authenticate(token)).id : undefined,
          );
          const res = json({ ok: true });
          res.headers.set("Set-Cookie", cookie(session));
          return res;
        }
      }
      if (path[0] === "auth" && path.length === 2) {
        if (path[1] === "register" && method === "POST") {
          await auth.rateLimit("registration_global", 30);
          const data = credentials.parse(await body(req));
          await auth.register(data.email, data.password);
          const login = await auth.login(data.email, data.password),
            res = json(login.user, 201);
          res.headers.set("Set-Cookie", cookie(login.token));
          return res;
        }
        if (path[1] === "login" && method === "POST") {
          await auth.rateLimit("login_global", 100);
          const data = credentials.parse(await body(req)),
            login = await auth.login(data.email, data.password),
            res = json(login.user);
          res.headers.set("Set-Cookie", cookie(login.token));
          return res;
        }
        if (path[1] === "logout" && method === "POST") {
          if (token) await auth.logout(token);
          const res = json({ ok: true });
          res.headers.set("Set-Cookie", cookie("", 0));
          return res;
        }
        if (path[1] === "session" && method === "GET")
          return json(await auth.authenticate(token));
      }
      const user = await auth.authenticate(token),
        owner = user.id;
      const researchResponse = await new ResearchAPI(pool).handle(
        owner,
        path,
        method,
        () => body(req),
      );
      if (researchResponse !== undefined) return json(researchResponse);
      if (path[0] === "threads") {
        if (path.length === 1 && method === "GET")
          return json(await business.threads(owner));
        if (path.length === 1 && method === "POST")
          return json(await tasks.createThread(owner), 201);
        if (path.length === 2 && method === "GET")
          return json(await business.thread(owner, uuid.parse(path[1])));
        if (path.length === 2 && method === "DELETE")
          return json(await business.deleteThread(owner, uuid.parse(path[1])));
      }
      if (path[0] === "research" && path.length === 1 && method === "POST") {
        const data = research.parse(await body(req));
        if (
          (await new ExecutionContextStore(pool).thread(owner, data.threadId))
            .kind !== "personal"
        )
          throw new DomainError("group_research_read_only", 403);
        if (
          data.portfolioSnapshotRef &&
          !(
            await pool.query(
              "SELECT id FROM portfolio_snapshots WHERE id=$1 AND owner_id=$2",
              [data.portfolioSnapshotRef, owner],
            )
          ).rowCount
        )
          throw new DomainError("not_found", 404);
        const { threadId, ...input } = data;
        const task = await tasks.submit(
          owner,
          threadId,
          req.headers.get("idempotency-key") ?? "",
          input,
        );
        return json({ taskId: task.id, threadId, status: task.status }, 202);
      }
      if (path[0] === "tasks" && path.length >= 2) {
        const id = uuid.parse(path[1]);
        if (path.length === 2 && method === "GET")
          return json(await tasks.snapshot(owner, id));
        if (path.length === 3 && path[2] === "audit" && method === "GET")
          return json(
            await tasks.audit(owner, id, url.searchParams.get("after") ?? "0"),
          );
        if (path.length === 3 && path[2] === "stop" && method === "POST") {
          const current = await tasks.get(owner, id);
          if (
            (
              await new ExecutionContextStore(pool).thread(
                owner,
                current.thread_id,
              )
            ).kind !== "personal"
          )
            throw new DomainError("group_research_read_only", 403);
          const t = await tasks.stop(owner, id);
          return json({ taskId: t.id, status: t.status });
        }
        if (path.length === 3 && path[2] === "events" && method === "GET") {
          const subscribedTask = await tasks.get(owner, id);
          if (subscribedTask.parent_id)
            throw new DomainError("root_task_required", 400);
          const resumeCursor =
            req.headers.get("last-event-id") ??
            url.searchParams.get("afterSequence");
          let cursor = Number(resumeCursor ?? 0);
          if (
            !Number.isSafeInteger(cursor) ||
            cursor < 0 ||
            cursor > subscribedTask.sequence
          )
            throw new DomainError("invalid_cursor", 400);
          let cancelled = false;
          let keepaliveAt = 0;
          let authAt = 0;
          const encoder = new TextEncoder();
          const stream = new ReadableStream<Uint8Array>({
            async start(controller) {
              const send = (event: string, data: unknown, id: number) => {
                if (!cancelled)
                  controller.enqueue(
                    encoder.encode(
                      `id: ${id}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`,
                    ),
                  );
              };
              try {
                const sendSnapshot = async () => {
                  // Data and cursor share one consistent transaction.
                  const snap = await tasks.snapshot(owner, id);
                  cursor = snap.lastSequence;
                  send(
                    "state.snapshot",
                    {
                      event: "state.snapshot",
                      taskId: id,
                      sequence: cursor,
                      data: snap,
                    },
                    cursor,
                  );
                };
                let replay =
                  resumeCursor === null
                    ? undefined
                    : await tasks.events(owner, id, cursor);
                if (
                  !replay?.length ||
                  replay.some((e, i) => e.sequence !== cursor + i + 1)
                ) {
                  await sendSnapshot();
                  replay = undefined;
                }
                while (!cancelled && !req.signal.aborted) {
                  const now = Date.now();
                  // The cursor poll below runs every 200ms; re-checking the
                  // session at that rate only adds database load. Revocation
                  // still takes effect within a couple of seconds.
                  if (now - authAt >= 2000) {
                    authAt = now;
                    await auth.authenticate(token);
                  }
                  const events =
                    replay ?? (await tasks.events(owner, id, cursor));
                  replay = undefined;
                  if (events.some((e, i) => e.sequence !== cursor + i + 1)) {
                    await sendSnapshot();
                  } else
                    for (const e of events) {
                      cursor = e.sequence;
                      send(
                        e.event,
                        {
                          eventId: e.id,
                          taskId: id,
                          threadId: subscribedTask.thread_id,
                          sequence: e.sequence,
                          event: e.event,
                          data: e.data,
                        },
                        cursor,
                      );
                    }
                  const t = await tasks.get(owner, id);
                  if (!events.length && cursor < t.sequence)
                    await sendSnapshot();
                  if (
                    [
                      "completed",
                      "failed",
                      "cancelled",
                      "interrupted",
                    ].includes(t.status) &&
                    cursor >= t.sequence
                  )
                    break;
                  if (!cancelled && now - keepaliveAt >= 5000) {
                    keepaliveAt = now;
                    controller.enqueue(encoder.encode(": keepalive\n\n"));
                  }
                  // Poll the persisted cursor frequently so streamed answer
                  // deltas reach the browser within a few hundred ms. The
                  // query is an indexed range scan; the keepalive above holds
                  // the connection open for proxies between polls.
                  await new Promise((r) => setTimeout(r, 200));
                }
              } catch {
                if (!cancelled)
                  controller.enqueue(
                    encoder.encode(
                      'event: connection.error\ndata: {"error":"connection_lost"}\n\n',
                    ),
                  );
              } finally {
                if (!cancelled) {
                  cancelled = true;
                  controller.close();
                }
              }
            },
            cancel() {
              cancelled = true;
            },
          });
          return new Response(stream, {
            headers: {
              "Content-Type": "text/event-stream",
              "Cache-Control": "no-store",
              "X-Accel-Buffering": "no",
            },
          });
        }
      }
      if (
        path.length === 2 &&
        ["datasets", "results", "workspaces"].includes(path[0] ?? "")
      ) {
        const id = uuid.parse(path[1]),
          kind = path[0] as "datasets" | "results" | "workspaces";
        if (method === "GET")
          return json(await business.resource(owner, kind, id));
        if (kind === "workspaces" && method === "PUT") {
          const data = z
            .object({
              expectedVersion: z.number().int().nonnegative(),
              order: z.array(uuid).max(100),
            })
            .strict()
            .parse(await body(req));
          return json(
            await business.saveLayout(
              owner,
              id,
              data.expectedVersion,
              data.order,
            ),
          );
        }
      }
      if (path[0] === "portfolios") {
        if (path.length === 1 && method === "GET")
          return json(await business.portfolios(owner));
        if (path.length === 1 && method === "POST")
          return json(
            await business.saveHoldings(owner, holdings.parse(await body(req))),
            201,
          );
        if (path.length === 2 && method === "GET")
          return json(
            await business.portfolio(
              owner,
              uuid.parse(path[1]),
              url.searchParams.has("version")
                ? uuid.parse(url.searchParams.get("version"))
                : undefined,
            ),
          );
        if (path.length === 3 && path[2] === "snapshots" && method === "POST")
          return json(
            await business.saveHoldings(
              owner,
              holdings.parse(await body(req)),
              uuid.parse(path[1]),
            ),
            201,
          );
      }
      if (path[0] === "watchlist" && path.length === 1) {
        if (method === "GET") return json(await business.watchlist(owner));
        if (method === "POST")
          return json(
            await business.watchlist(
              owner,
              z
                .object({ symbol })
                .strict()
                .parse(await body(req)).symbol,
            ),
          );
      }
      if (path[0] === "profiles" && path.length === 1 && method === "GET")
        return json(
          (
            await pool.query(
              "SELECT role,state FROM profile_instances WHERE owner_id=$1 ORDER BY role",
              [owner],
            )
          ).rows,
        );
      return json({ error: "not_found" }, 404);
    } catch (e) {
      if (e instanceof DomainError)
        return json({ error: e.code }, e.httpStatus);
      if (e instanceof z.ZodError)
        return json({ error: "invalid_request" }, 400);
      console.error(
        "BFF request failed",
        e instanceof Error ? `${e.name}: ${e.message}` : "unknown",
      );
      return json({ error: "service_unavailable" }, 503);
    }
  };
}
