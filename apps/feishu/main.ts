import {
  priceReply,
  lookupTarget,
} from "../../packages/server/src/feishu-price.js";
import { AlpacaProvider } from "../../packages/market/src/alpaca.js";
import { feishuProgress } from "../../packages/server/src/feishu-progress.js";
import { acceptQuickReply } from "../../packages/server/src/feishu-quick-reply.js";
import {
  NotificationStore,
  deliverNextFeishu,
} from "../../packages/server/src/notifications.js";
import { ExecutionContextStore } from "../../packages/server/src/execution-context.js";
import { FeishuLoginStore } from "../../packages/server/src/feishu-login.js";
import { FeishuIdentityStore } from "../../packages/server/src/feishu-identity.js";
import { loadTaskCards } from "../../packages/server/src/feishu-cards.js";
import * as lark from "@larksuiteoapi/node-sdk";
import { createHash, randomUUID } from "node:crypto";
import {
  database,
  transaction,
  lockOwner,
} from "../../packages/server/src/db.js";
import { TaskStore } from "../../packages/server/src/tasks.js";
const required = (k: string) => {
  const v = process.env[k];
  if (!v) throw Error(`${k} required`);
  return v;
};
const appId = required("FEISHU_APP_ID"),
  appSecret = required("FEISHU_APP_SECRET");
const groupResearch = process.env.FEISHU_GROUP_RESEARCH_ENABLED === "1";
const multiuser = process.env.FEISHU_MULTIUSER_ENABLED === "1";
const discover = process.env.FEISHU_DISCOVER === "1";
const tenant = discover ? "" : required("FEISHU_TENANT_KEY"),
  sender = discover ? "" : required("FEISHU_OWNER_OPEN_ID");
const owner = discover ? "" : required("FEISHU_OWNER_ID");
const groups = new Set(
  (process.env.FEISHU_GROUP_CHAT_IDS ?? "").split(",").filter(Boolean),
);
const origin = required("APP_ORIGIN");
const priceProvider =
  process.env.ALPACA_DATA_KEY &&
  process.env.ALPACA_DATA_SECRET &&
  ["iex", "sip"].includes(process.env.ALPACA_DATA_FEED ?? "")
    ? new AlpacaProvider(
        process.env.ALPACA_DATA_KEY,
        process.env.ALPACA_DATA_SECRET,
        process.env.ALPACA_DATA_FEED as "iex" | "sip",
      )
    : undefined;
// SDK errors can include request headers and credentials. Never log raw objects.
const sdkLogger = {
  error: () => console.error("feishu_sdk_error"),
  warn: () => console.warn("feishu_sdk_warning"),
  info: () => {},
  debug: () => {},
  trace: () => {},
};
const client = new lark.Client({
  logger: sdkLogger,
  appId,
  appSecret,
  domain: lark.Domain.Feishu,
});
const ws = new lark.WSClient({
  logger: sdkLogger,
  appId,
  appSecret,
  domain: lark.Domain.Feishu,
  loggerLevel: lark.LoggerLevel.warn,
});
const pool = discover ? undefined : database();
if (pool) await new FeishuIdentityStore(pool).bind(appId, sender, owner);
let stopped = false;
process.on("SIGTERM", () => {
  stopped = true;
  ws.close();
});
process.on("SIGINT", () => {
  stopped = true;
  ws.close();
});
const hash = (s: string) => createHash("sha256").update(s).digest("hex");
const terminal = (s: string) =>
  ["completed", "failed", "cancelled", "interrupted"].includes(s);
async function enqueue(c: any, key: string, phase: string, text: string) {
  const chunks: string[] = [];
  let chunk = "";
  for (const ch of text) {
    if (Buffer.byteLength(JSON.stringify({ text: chunk + ch })) > 12000) {
      chunks.push(chunk);
      chunk = "";
    }
    chunk += ch;
  }
  if (chunk) chunks.push(chunk);
  for (const [part, text] of chunks.entries())
    await c.query(
      "INSERT INTO feishu_outbox(id,inbox_key,phase,part,text) VALUES($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING",
      [randomUUID(), key, phase, part, text],
    );
}
const info = (await client.request({
  url: "/open-apis/bot/v3/info",
  method: "GET",
})) as any;
const botId = info.bot?.open_id;
if (!botId) throw Error("bot_identity_unavailable");
await ws.start({
  eventDispatcher: new lark.EventDispatcher({}).register({
    "im.message.receive_v1": async (data: any) => {
      const m = data.message,
        s = data.sender;
      if (s?.sender_type !== "user" || !m?.message_id) return;
      if (discover) {
        console.info("feishu_identity", {
          tenant: data.tenant_key ?? s.tenant_key,
          openId: s.sender_id?.open_id,
          chatId: m.chat_id,
          chatType: m.chat_type,
        });
        return;
      }
      if (
        !multiuser &&
        ((data.tenant_key ?? s.tenant_key) !== tenant ||
          s.sender_id?.open_id !== sender)
      )
        return;
      if (
        m.chat_type === "group" &&
        !groupResearch &&
        !groups.has(m.chat_id) &&
        m.mentions?.some((x: any) => x.id?.open_id === botId)
      ) {
        console.info("feishu_group_pending", {
          chatId: m.chat_id,
          openId: s.sender_id.open_id,
        });
        return;
      }
      if (
        m.chat_type !== "p2p" &&
        (m.chat_type !== "group" ||
          (!groupResearch && !groups.has(m.chat_id)) ||
          !m.mentions?.some((x: any) => x.id?.open_id === botId))
      )
        return;
      let input = "";
      if (m.message_type === "text") {
        input = JSON.parse(m.content).text ?? "";
        for (const x of m.mentions ?? [])
          if (x.id?.open_id === botId) input = input.replaceAll(x.key, "");
        input = input.trim();
      }
      const actualSender = s.sender_id?.open_id;
      if (!actualSender) return;
      let messageOwner = owner;
      if (multiuser && !input.startsWith("/login bind ")) {
        // Group admission is enabled separately once per-group runtimes are available.
        if (m.chat_type !== "p2p" && !groupResearch) return;
        try {
          messageOwner = (
            await new FeishuIdentityStore(pool!).resolve(appId, actualSender, {
              allowRegistration: process.env.FEISHU_ADMISSION_ENABLED === "1",
              limit: Number(process.env.FEISHU_TRIAL_LIMIT ?? 10),
            })
          ).id;
        } catch {
          await client.im.message.reply({
            path: { message_id: m.message_id },
            data: {
              msg_type: "text",
              content: JSON.stringify({
                text: "当前试用名额已满或暂未开放新用户，请稍后再试。",
              }),
            },
          });
          return;
        }
      }
      if (input.startsWith("/login")) {
        let reply = "网页登录暂未开放。";
        if (process.env.FEISHU_LOGIN_ENABLED === "1") {
          try {
            await new FeishuLoginStore(pool!).confirm(
              input,
              m.chat_type,
              appId,
              s.sender_id.open_id,
            );
            reply =
              "已确认网页登录，请返回发起登录的浏览器完成。仅确认你本人发起的登录。";
          } catch {
            reply =
              "登录确认未成功。请仅在私聊发送网页提供的完整确认命令；登录码五分钟有效且只能使用一次。";
          }
        }
        await client.im.message.reply({
          path: { message_id: m.message_id },
          data: { msg_type: "text", content: JSON.stringify({ text: reply }) },
        });
        return;
      }
      const contextId =
        m.chat_type === "group" && groupResearch
          ? (
              await new ExecutionContextStore(pool!).group(
                messageOwner,
                appId,
                m.chat_id,
              )
            ).id
          : messageOwner;
      const key = hash(
          `${appId}:${data.tenant_key ?? s.tenant_key}:${m.message_id}`,
        ),
        chat = hash(
          `${appId}:${data.tenant_key ?? s.tenant_key}:${m.chat_id}:${actualSender}${m.chat_type === "group" && groupResearch ? `:${contextId}` : ""}`,
        );
      const previousLookup = (
        await pool!.query(
          "SELECT lookup_context FROM feishu_chats WHERE key=$1 AND owner_id=$2",
          [chat, messageOwner],
        )
      ).rows[0]?.lookup_context;
      const quote = await priceReply(input, priceProvider, previousLookup);
      await transaction(pool!, async (c) => {
        await c.query("SET LOCAL statement_timeout='2000ms'");
        await c.query(
          "INSERT INTO feishu_chats(key,owner_id,context_id) VALUES($1,$2,$3) ON CONFLICT DO NOTHING",
          [chat, messageOwner, contextId],
        );
        await c.query(
          "INSERT INTO feishu_inbox(key,chat_key,message_id,chat_id,input) VALUES($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING",
          [key, chat, m.message_id, m.chat_id, input],
        );
        if (quote !== null) {
          const updated = await c.query(
            "UPDATE feishu_inbox SET processed=true,finished=true WHERE key=$1 AND NOT processed AND task_id IS NULL RETURNING key",
            [key],
          );
          if (updated.rowCount) {
            const target = lookupTarget(input);
            if (target)
              await c.query(
                "UPDATE feishu_chats SET lookup_context=$2 WHERE key=$1",
                [
                  chat,
                  JSON.stringify({ target, at: new Date().toISOString() }),
                ],
              );
            await enqueue(c, key, "accepted", quote);
          }
          return;
        }
        await c.query(
          "UPDATE feishu_chats SET lookup_context=NULL WHERE key=$1",
          [chat],
        );
        if (await acceptQuickReply(c, key, input, enqueue)) return;
        const preparation = (
          await c.query(
            "SELECT status,(SELECT count(*) FROM profile_instances p WHERE p.context_id=$1 AND p.lifecycle_managed AND p.deployment_hash IS NOT NULL)=3 AS registered FROM profile_preparation_jobs WHERE owner_id=$1",
            [contextId],
          )
        ).rows[0];
        if (
          preparation &&
          (!["prepared", "manual"].includes(preparation.status) ||
            !preparation.registered) &&
          !["/help", "/new", "/status", "/stop"].includes(input)
        )
          await enqueue(
            c,
            key,
            "preparing",
            preparation.status === "blocked"
              ? "环境准备未成功，问题已保存，恢复后继续处理。"
              : "正在准备你的独立研究环境，问题已保存，准备完成后会加入研究队列。",
          );
      });
    },
  }),
});
const connectionDeadline = Date.now() + 30000;
while (!stopped && ws.getConnectionStatus().state !== "connected") {
  if (Date.now() > connectionDeadline) {
    ws.close();
    throw Error("feishu_connection_timeout");
  }
  await new Promise((r) => setTimeout(r, 250));
}
if (!stopped)
  console.info("feishu_connected", {
    mode: discover ? "discovery" : "research",
  });
if (pool) {
  const leader = await pool.connect();
  if (
    !(
      await leader.query(
        "SELECT pg_try_advisory_lock(hashtext('alpha_feishu')) AS ok",
      )
    ).rows[0].ok
  )
    throw Error("feishu_already_running");
  leader.on("error", () => {
    stopped = true;
    ws.close();
  });
  while (!stopped) {
    try {
      await transaction(pool, async (c) => {
        const tasks = new TaskStore(pool!, c);
        const blocked = await c.query(
          "SELECT i.key FROM feishu_inbox i JOIN feishu_chats ch ON ch.key=i.chat_key JOIN acceptance_capacity a ON a.context_id=ch.context_id WHERE NOT i.processed AND a.status='unknown'",
        );
        for (const item of blocked.rows)
          await enqueue(
            c,
            item.key,
            "preparation_failed",
            "独立环境验收未通过，研究问题已保存。系统正在核对恢复，请勿重复发送。",
          );
        const row = (
          await c.query(
            "SELECT i.*,ch.thread_id,ch.owner_id,ch.context_id FROM feishu_inbox i JOIN feishu_chats ch ON ch.key=i.chat_key WHERE NOT i.processed AND (i.input IN ('/help','/new','/status','/stop') OR NOT EXISTS (SELECT 1 FROM profile_preparation_jobs p WHERE p.owner_id=coalesce(ch.context_id,ch.owner_id) AND (p.status NOT IN ('prepared','manual') OR (SELECT count(*) FROM profile_instances ready WHERE ready.context_id=coalesce(ch.context_id,ch.owner_id) AND ready.lifecycle_managed AND ready.deployment_hash IS NOT NULL)<3))) ORDER BY i.created_at,i.key LIMIT 1",
          )
        ).rows[0];
        if (row) {
          const owner = row.owner_id;
          await lockOwner(c, owner);
          let reply = "";
          let thread = row.thread_id;
          const active = thread
            ? (
                await c.query(
                  "SELECT id,status FROM tasks WHERE owner_id=$1 AND thread_id=$2 AND parent_id IS NULL AND status NOT IN ('completed','failed','cancelled','interrupted') ORDER BY ordinal DESC LIMIT 1",
                  [owner, thread],
                )
              ).rows[0]
            : undefined;
          if (row.input === "/help")
            reply =
              "发送“查看 AAPL 研究”读取已有结果，发送“分析 AAPL”启动研究。/new 新会话 · /status 当前任务 · /stop 停止任务";
          else if (row.input === "/new") {
            await c.query(
              "UPDATE feishu_chats SET thread_id=NULL WHERE key=$1",
              [row.chat_key],
            );
            reply = "下次提问将开始新会话。";
          } else if (row.input === "/status")
            reply = active
              ? `任务状态：${active.status}`
              : "当前没有进行中的任务。";
          else if (row.input === "/stop") {
            if (active) await tasks.stop(owner, active.id);
            reply = active ? "已请求停止任务。" : "当前没有进行中的任务。";
          } else if (!row.input || row.input.length > 16000)
            reply = "请发送 1–16000 字的文字研究问题。";
          else if (active)
            reply = "当前会话已有研究进行中，请等待完成，或使用 /stop、/new。";
          else {
            const pending = (
              await c.query(
                "SELECT count(*)::int AS n FROM tasks WHERE owner_id=$1 AND parent_id IS NULL AND origin='interactive' AND status IN ('queued','running','stopping','unknown')",
                [owner],
              )
            ).rows[0].n;
            if (pending >= 10) reply = "研究队列已满，请稍后再试。";
            else {
              if (!thread) {
                thread = (
                  await tasks.createThread(owner, row.context_id ?? owner)
                ).id;
                await c.query(
                  "UPDATE threads SET source=$2 WHERE id=$1 AND owner_id=$3",
                  [
                    thread,
                    row.context_id && row.context_id !== owner
                      ? "feishu_group"
                      : "feishu_private",
                    owner,
                  ],
                );
                await c.query(
                  "UPDATE feishu_chats SET thread_id=$2 WHERE key=$1",
                  [row.chat_key, thread],
                );
              }
              const task = await tasks.submit(
                owner,
                thread,
                `feishu:${row.key}`,
                { input: row.input },
              );
              await c.query("UPDATE feishu_inbox SET task_id=$2 WHERE key=$1", [
                row.key,
                task.id,
              ]);
              reply = "已收到，研究已加入队列，完成后会在这里回复。";
            }
          }
          await enqueue(c, row.key, "accepted", reply);
          await c.query(
            "UPDATE feishu_inbox SET processed=true,finished=(task_id IS NULL) WHERE key=$1",
            [row.key],
          );
        }
        const results = await c.query(
          "SELECT i.key,t.status,t.output,t.id,t.owner_id,t.created_at,ch.title FROM feishu_inbox i JOIN tasks t ON t.id=i.task_id JOIN threads ch ON ch.id=t.thread_id WHERE NOT i.finished AND i.processed",
        );
        for (const t of results.rows) {
          if (terminal(t.status)) {
            await enqueue(
              c,
              t.key,
              "final",
              `${t.output?.text ?? `研究状态：${t.status}`}\n\n会话：${t.title}\n工作台：${origin}`,
            );
            if (t.status === "completed") {
              const cards = await loadTaskCards(c, t.owner_id, t.id, origin);
              for (const [part, card] of cards.entries())
                await c.query(
                  "INSERT INTO feishu_outbox(id,inbox_key,phase,part,text,card) VALUES($1,$2,'canvas',$3,$4,$5) ON CONFLICT DO NOTHING",
                  [
                    randomUUID(),
                    t.key,
                    part,
                    `图表与数据请查看工作台：${origin}`,
                    JSON.stringify(card),
                  ],
                );
            }
            await c.query(
              "UPDATE feishu_inbox SET finished=true WHERE key=$1",
              [t.key],
            );
          } else if (t.status === "unknown")
            await enqueue(
              c,
              t.key,
              "unknown",
              "研究状态待核对，系统不会自动重跑。请在工作台查看。",
            );
          else {
            const progress = feishuProgress(t.status, t.created_at);
            if (progress)
              await enqueue(c, t.key, progress.phase, progress.text);
          }
        }
      });
      await new NotificationStore(pool).prepareFeishu(appId, origin);
      const delivered = await deliverNextFeishu(pool, client, appId);
      if (delivered.status === "uncertain")
        console.error("feishu_delivery_needs_review", { id: delivered.id });
    } catch (e) {
      console.error("feishu_iteration_failed", {
        code: e instanceof Error ? e.message : "unknown",
      });
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  leader.release();
  await pool.end();
} else {
  while (!stopped) await new Promise((r) => setTimeout(r, 1000));
}
