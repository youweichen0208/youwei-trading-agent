export async function api<T = any>(
  path: string,
  method = "GET",
  body?: unknown,
  extra?: HeadersInit,
): Promise<T> {
  const res = await fetch("/api/" + path, {
    method,
    headers: { "Content-Type": "application/json", ...extra },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error ?? "connection_failed");
  }
  return res.json();
}
export const errors: Record<string, string> = {
  login_challenge_invalid: "登录码已失效或不属于当前浏览器，请重新生成",
  invalid_credentials: "邮箱或密码不正确",
  password_length: "密码需要 12–256 个字符",
  account_unavailable: "该邮箱暂时无法注册",
  invalid_email: "请输入有效邮箱",
  rate_limited: "请求过于频繁，请稍后再试",
  queue_full: "已有 10 个未结束的研究，请等待完成或取消排队中的研究后再提交",
  service_unavailable: "服务暂时不可用，请稍后重试",
  invalid_request: "请检查填写内容",
  connection_failed: "连接中断，请检查网络后重试",
  version_conflict: "画布已更新，请重新加载",
  provider_unavailable: "研究服务尚未连接，本次任务未执行",
  unauthorized: "登录已失效，请重新登录",
  not_found: "此研究不存在或已无权访问",
  thread_busy: "该会话仍有研究正在运行，请等待结束或停止后再删除",
  invalid_event_stream: "研究连接返回了无效数据，请重新打开此研究",
};
export const message = (e: unknown) =>
  e instanceof Error
    ? (errors[e.message] ?? "操作未完成，请稍后重试")
    : "操作未完成";
export const statusName: Record<string, string> = {
  queued: "排队中",
  running: "研究中",
  stopping: "正在停止",
  unknown: "正在核对执行状态",
  completed: "已完成",
  failed: "未完成",
  cancelled: "已取消",
  interrupted: "已中断",
};
export const isTerminal = (s: string) =>
  ["completed", "failed", "cancelled", "interrupted"].includes(s);
export const taskEvents = subscribeTask;
import { subscribeTask } from "../../../packages/events-client/src/task-stream";
