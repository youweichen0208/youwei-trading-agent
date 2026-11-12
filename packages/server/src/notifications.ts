import { randomUUID } from "node:crypto";
import type { Pool, PoolClient } from "pg";
import { transaction } from "./db.js";
import { DomainError } from "./tasks.js";

export type NotificationPayload = {
  title: string;
  text: string;
  href?: string;
  [key: string]: unknown;
};
export class NotificationStore {
  constructor(
    readonly pool: Pool,
    private client?: PoolClient,
  ) {}
  async enqueue(
    owner: string,
    kind: string,
    eventKey: string,
    payload: NotificationPayload,
  ) {
    if (
      !kind ||
      !eventKey ||
      !payload.title ||
      !payload.text ||
      eventKey.length > 500
    )
      throw new DomainError("invalid_notification", 400);
    const db = this.client ?? this.pool;
    return (
      await db.query(
        `INSERT INTO notifications(id,owner_id,kind,event_key,payload) VALUES($1,$2,$3,$4,$5)
       ON CONFLICT(owner_id,event_key) DO UPDATE SET event_key=excluded.event_key RETURNING *`,
        [randomUUID(), owner, kind, eventKey, JSON.stringify(payload)],
      )
    ).rows[0];
  }
  async list(owner: string, limit = 50) {
    return (
      await this.pool.query(
        "SELECT * FROM notifications WHERE owner_id=$1 ORDER BY created_at DESC,id DESC LIMIT $2",
        [owner, Math.max(1, Math.min(100, limit))],
      )
    ).rows;
  }
  async markRead(owner: string, id: string) {
    const row = (
      await this.pool.query(
        "UPDATE notifications SET read_at=coalesce(read_at,now()) WHERE id=$1 AND owner_id=$2 RETURNING *",
        [id, owner],
      )
    ).rows[0];
    if (!row) throw new DomainError("not_found", 404);
    return row;
  }
  /** Run by the configured bot. Active notifications do not require a fake inbox message. */
  async prepareFeishu(appId: string, origin: string) {
    return transaction(this.pool, async (c) => {
      const rows = (
        await c.query(
          `SELECT n.*,i.open_id FROM notifications n JOIN feishu_identities i ON i.owner_id=n.owner_id AND i.app_id=$1
         LEFT JOIN research_settings s ON s.owner_id=n.owner_id
         WHERE coalesce((s.payload->>'feishuNotifications')::boolean,true) AND NOT EXISTS(SELECT 1 FROM feishu_outbox o WHERE o.notification_id=n.id AND o.app_id=$1)
         ORDER BY n.created_at,n.id LIMIT 50`,
          [appId],
        )
      ).rows;
      let inserted = 0;
      for (const row of rows) {
        const payload = row.payload as NotificationPayload;
        const href = payload.href
          ? new URL(payload.href, origin).toString()
          : new URL(origin).toString();
        const chunks = splitFeishuText(
          `${payload.title}\n\n${payload.text}\n\n工作台：${href}`,
        );
        for (const [part, text] of chunks.entries()) {
          inserted +=
            (
              await c.query(
                `INSERT INTO feishu_outbox(id,inbox_key,phase,part,text,delivery_mode,notification_id,app_id,receive_open_id)
             VALUES($1,NULL,'notification',$2,$3,'create',$4,$5,$6) ON CONFLICT DO NOTHING`,
                [randomUUID(), part, text, row.id, appId, row.open_id],
              )
            ).rowCount ?? 0;
        }
      }
      return inserted;
    });
  }
}
export function splitFeishuText(text: string) {
  const chunks: string[] = [];
  let chunk = "";
  for (const character of text) {
    if (
      Buffer.byteLength(JSON.stringify({ text: chunk + character })) > 12000
    ) {
      chunks.push(chunk);
      chunk = "";
    }
    chunk += character;
  }
  if (chunk) chunks.push(chunk);
  return chunks;
}
export interface FeishuDeliveryClient {
  im: {
    message: {
      reply: (request: any) => Promise<any>;
      create: (request: any) => Promise<any>;
    };
  };
}
/** Caller serializes delivery through the existing bot leader lock. UUID survives retries. */
export async function deliverNextFeishu(
  pool: Pool,
  client: FeishuDeliveryClient,
  appId: string,
  now = new Date(),
) {
  const row = (
    await pool.query(
      `SELECT o.*,i.message_id AS reply_message_id FROM feishu_outbox o LEFT JOIN feishu_inbox i ON i.key=o.inbox_key
     LEFT JOIN notifications n ON n.id=o.notification_id LEFT JOIN research_settings s ON s.owner_id=n.owner_id
     WHERE NOT o.sent AND NOT o.uncertain AND o.next_attempt<=$2
       AND (o.delivery_mode='reply' OR (o.app_id=$1 AND coalesce((s.payload->>'feishuNotifications')::boolean,true)))
     ORDER BY coalesce(i.created_at,o.created_at),CASE o.phase WHEN 'accepted' THEN 0 WHEN 'final' THEN 1 ELSE 2 END,o.part LIMIT 1`,
      [appId, now],
    )
  ).rows[0];
  if (!row) return { status: "idle" as const };
  if (
    row.first_attempt &&
    now.getTime() - new Date(row.first_attempt).getTime() > 30 * 60 * 1000
  ) {
    await pool.query("UPDATE feishu_outbox SET uncertain=true WHERE id=$1", [
      row.id,
    ]);
    return { status: "uncertain" as const, id: row.id };
  }
  await pool.query(
    "UPDATE feishu_outbox SET first_attempt=coalesce(first_attempt,$2),next_attempt=$2::timestamptz+interval '30 seconds' WHERE id=$1",
    [row.id, now],
  );
  const data = {
    msg_type: row.card ? "interactive" : "text",
    content: JSON.stringify(row.card ?? { text: row.text }),
    uuid: row.id,
  };
  const result =
    row.delivery_mode === "create"
      ? await client.im.message.create({
          params: { receive_id_type: "open_id" },
          data: { ...data, receive_id: row.receive_open_id },
        })
      : await client.im.message.reply({
          path: { message_id: row.reply_message_id },
          data,
        });
  if (row.card && result.code === 230099) {
    await pool.query(
      "UPDATE feishu_outbox SET id=$2,card=NULL,first_attempt=NULL,next_attempt=$3 WHERE id=$1",
      [row.id, randomUUID(), now],
    );
    return { status: "fallback" as const };
  }
  if (result.code !== 0) throw new DomainError("feishu_delivery_failed", 503);
  await pool.query(
    "UPDATE feishu_outbox SET sent=true,message_id=$2 WHERE id=$1",
    [row.id, result.data?.message_id ?? null],
  );
  return { status: "sent" as const, id: row.id };
}
