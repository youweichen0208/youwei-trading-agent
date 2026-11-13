import type { PoolClient } from "pg";

export function quickReplyText(input: string): string | null {
  const text = input.trim().toLowerCase();
  if (/^(hello|hi|你好|您好|嗨)[！!？?。.]*$/u.test(text))
    return "你好，我是研究助手。你可以查看研究结果、询问股票问题，或用 /status 查看当前任务。";
  if (/^(你是谁|你是什么|你能做什么)[！!？?。.]*$/u.test(text))
    return "我是股票研究助手，可以整理证据、分析行情、查看已保存的研究与预测。研究任务异步执行；/status 查看进度，/stop 请求停止。";
  return null;
}

export function savedResearchSymbol(input: string): string | null {
  const text = input.trim();
  const match =
    /^(?:\/research\s+|(?:查看|查询|看看)\s*)([A-Za-z][A-Za-z0-9.-]{0,9})(?:\s*(?:的)?(?:最新|已有|已保存)?(?:研究结果|研究结论|预测|研究))?[？?。.!！]*$/u.exec(
      text,
    );
  return match?.[1]?.toUpperCase() ?? null;
}

async function savedResearchReply(c: PoolClient, key: string, symbol: string) {
  const scope = (
    await c.query(
      "SELECT ch.owner_id,coalesce(ch.context_id,ch.owner_id) AS context_id FROM feishu_inbox i JOIN feishu_chats ch ON ch.key=i.chat_key WHERE i.key=$1",
      [key],
    )
  ).rows[0];
  if (!scope) return null;
  const forecasts = (
    await c.query(
      `SELECT DISTINCT ON (f.horizon) f.horizon,f.published_at,f.target_session::text,f.target_close_at,f.payload
     FROM forecasts f JOIN tasks t ON t.id=f.task_id
     JOIN thread_contexts tc ON tc.thread_id=t.thread_id AND tc.owner_id=f.owner_id
     WHERE f.owner_id=$1 AND tc.context_id=$2 AND f.symbol=$3
     ORDER BY f.horizon,f.published_at DESC,f.id DESC`,
      [scope.owner_id, scope.context_id, symbol],
    )
  ).rows;
  const summary = (
    await c.query(
      `SELECT r.payload,r.created_at FROM results r JOIN tasks t ON t.id=r.task_id
     JOIN thread_contexts tc ON tc.thread_id=t.thread_id AND tc.owner_id=r.owner_id
     WHERE r.owner_id=$1 AND tc.context_id=$2 AND r.kind='research_summary'
       AND (r.payload->>'symbol'=$3 OR t.input->>'symbol'=$3 OR EXISTS (SELECT 1 FROM research_contexts x WHERE x.owner_id=r.owner_id AND x.id::text=r.lineage->>'researchContextRef' AND x.symbol=$3))
     ORDER BY r.created_at DESC,r.id DESC LIMIT 1`,
      [scope.owner_id, scope.context_id, symbol],
    )
  ).rows[0];
  if (!forecasts.length && !summary)
    return `${symbol}：当前会话范围内还没有已保存的研究结果。若需新研究，请发送“分析 ${symbol}”。`;
  const percent = (value: unknown) =>
    typeof value === "string" && Number.isFinite(Number(value))
      ? `${(Number(value) * 100).toFixed(1)}%`
      : "未提供";
  const parts = [`${symbol} · 已保存研究（本次查询未启动新研究）`];
  for (const f of forecasts)
    parts.push(
      `${f.horizon} 交易日预测 · 发布于 ${new Date(f.published_at).toISOString()}\n目标交易日：${f.target_session}${new Date(f.target_close_at).getTime() <= Date.now() ? "（期限已结束，仅供历史参考）" : ""}\n预期收益：${percent(f.payload.meanReturn)}；上涨概率：${percent(f.payload.upProbability)}\n${String(f.payload.thesis ?? "").slice(0, 1200)}`,
    );
  if (summary)
    parts.push(
      `研究摘要 · ${new Date(summary.created_at).toISOString()}\n${String(summary.payload.text ?? summary.payload.summary ?? summary.payload.thesis ?? "已保存摘要，请在工作台查看详情。").slice(0, 1500)}`,
    );
  parts.push(
    "结果截至上述研究时间，之后的新信息尚未核实。需要更新请明确发送“重新分析 " +
      symbol +
      "”。",
  );
  return parts.join("\n\n");
}

/** Called after authenticated inbox admission, before environment preparation. */
export async function acceptQuickReply(
  c: PoolClient,
  key: string,
  input: string,
  enqueue: (
    c: PoolClient,
    key: string,
    phase: string,
    text: string,
  ) => Promise<unknown>,
): Promise<boolean> {
  const symbol = savedResearchSymbol(input);
  const reply =
    quickReplyText(input) ??
    (symbol ? await savedResearchReply(c, key, symbol) : null);
  if (reply === null) return false;
  const updated = await c.query(
    "UPDATE feishu_inbox SET processed=true,finished=true WHERE key=$1 AND NOT processed AND task_id IS NULL RETURNING key",
    [key],
  );
  if (updated.rowCount) await enqueue(c, key, "accepted", reply);
  return true;
}
