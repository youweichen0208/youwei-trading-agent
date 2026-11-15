// Terminal task text. The reason code is recorded by the Worker; the user
// should see what actually blocked completion instead of a generic message.

const FAILURE_TEXT: Record<string, string> = {
  required_child_failed: "专项分析任务未能完成，本次研究已停止。",
  required_tool_failed: "必需的研究工具调用失败，本次研究未完成。",
  final_output_missing: "研究流程结束但没有返回结论，请重试。",
  deadline_exceeded: "任务超过执行时限，已被停止。",
  unsupported_upstream_control: "运行环境不支持停止指令，任务已中断。",
  user_requested: "任务已停止。",
  failed: "研究服务返回失败，本次研究未完成。",
  interrupted: "研究进程被中断，请重试。",
};

export function failureText(
  status: "cancelled" | "failed" | "interrupted",
  reason: string,
): string {
  if (status === "cancelled" && reason !== "deadline_exceeded")
    return "任务已停止。";
  return FAILURE_TEXT[reason] ?? "任务未完整完成，请查看任务状态。";
}
