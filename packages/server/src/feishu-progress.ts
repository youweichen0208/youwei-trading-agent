/** One durable receipt per state; the outbox unique key suppresses repeats. */
export function feishuProgress(
  status: string,
  createdAt: Date | string,
  now = Date.now(),
) {
  if (now - new Date(createdAt).getTime() < 30_000) return null;
  if (status === "queued")
    return {
      phase: "progress_queued",
      text: "研究仍在排队，尚未开始执行。你可以继续查看已有研究，或用 /stop 取消本次任务。",
    };
  if (status === "running")
    return {
      phase: "progress_running",
      text: "任务正在处理，可能包含环境启动、资料读取和分析；结果保存后会在这里回复。/status 可查看状态。",
    };
  if (status === "stopping")
    return {
      phase: "progress_stopping",
      text: "已请求停止，正在等待执行退出，确认后会更新状态。",
    };
  return null;
}
