// Opt-in live notification verification. Preparation alone never sends a message.
import assert from "node:assert/strict";
import { Pool } from "pg";
import * as lark from "@larksuiteoapi/node-sdk";
import {
  NotificationStore,
  deliverNextFeishu,
} from "../../packages/server/src/notifications.js";
import { FeishuIdentityStore } from "../../packages/server/src/feishu-identity.js";
const [schema, owner, mode] = process.argv.slice(2);
assert.match(schema ?? "", /^test_[a-f0-9]{32}$/);
assert.match(owner ?? "", /^[a-f0-9-]{36}$/);
const url = process.env.TEST_DATABASE_URL!;
assert.ok(new URL(url).pathname.endsWith("_test"));
const pool = new Pool({
  connectionString: url,
  options: `-c search_path=${schema}`,
});
try {
  const app = process.env.FEISHU_APP_ID!,
    recipient = process.env.FEISHU_OWNER_OPEN_ID!;
  assert.ok(app && recipient);
  await new FeishuIdentityStore(pool).bind(app, recipient, owner!);
  const store = new NotificationStore(pool);
  const notice = await store.enqueue(
    owner!,
    "acceptance",
    "forecast-live-acceptance-notification",
    {
      title: "Hermes 端到端验收",
      text: "这是一条主动通知链路测试消息。网页研究页与预测账本正在验收；本条消息用于验证飞书投递。",
      href: "/lab",
    },
  );
  await store.prepareFeishu(app, process.env.APP_ORIGIN!);
  if (mode !== "send") {
    console.log(
      JSON.stringify({
        prepared: true,
        notificationId: notice.id,
        sent: false,
      }),
    );
  } else {
    const logger = { error() {}, warn() {}, info() {}, debug() {}, trace() {} };
    const client = new lark.Client({
      appId: app,
      appSecret: process.env.FEISHU_APP_SECRET!,
      domain: lark.Domain.Feishu,
      logger,
    });
    const result = await deliverNextFeishu(pool, client, app);
    const row = (
      await pool.query(
        "SELECT sent,message_id FROM feishu_outbox WHERE notification_id=$1",
        [notice.id],
      )
    ).rows[0];
    assert.equal(row.sent, true);
    assert.ok(row.message_id);
    console.log(
      JSON.stringify({ result, sent: true, messageId: row.message_id }),
    );
  }
} finally {
  await pool.end();
}
