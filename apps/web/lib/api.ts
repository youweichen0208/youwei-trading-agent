export class HttpError extends Error {
  constructor(public readonly code: string, message: string, public readonly status: number) { super(message); }
}
const friendly: Record<string, string> = {
  INVALID_CREDENTIALS: "邮箱或密码不正确。",
  UNAUTHORIZED: "登录已失效，请重新登录。",
  CSRF_REJECTED: "登录凭证已更新，请刷新页面后重试。",
  ALPHA_UNAVAILABLE: "计算服务暂时不可用。请保留当前页面，稍后重试。",
  DATA_UNAVAILABLE: "真实行情尚未就绪，当前不能生成或冻结预测。",
  DRAFT_EXPIRED: "该草稿已超过冻结窗口，请重新研究。",
  HERMES_UNVERIFIED: "真实研究服务尚未完成接入验证。",
  SESSION_BUSY: "本会话还有未完成的研究，请等待或停止当前任务。",
  RATE_LIMITED: "请求过于频繁，请稍后重试。",
  RESEARCH_QUOTA_EXCEEDED: "过去 24 小时的研究请求已达到上限，请待较早的请求移出该时间窗口后再试。已有研究仍可查看。",
};

export async function api<T>(path: string, options: { body?: unknown; csrf?: string; key?: string; signal?: AbortSignal } = {}): Promise<T> {
  const response = await fetch(`/api/yta/v1${path}`, {
    method: options.body === undefined ? "GET" : "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.csrf ? { "X-CSRF-Token": options.csrf } : {}), ...(options.key ? { "Idempotency-Key": options.key } : {}) },
    body: options.body === undefined ? undefined : JSON.stringify(options.body), signal: options.signal,
  });
  let value;
  try { value = await response.json(); } catch { throw new HttpError("UNAVAILABLE", "服务返回异常，请稍后重试。", response.status); }
  if (!response.ok) throw new HttpError(value.error?.code ?? "UNKNOWN", friendly[value.error?.code] ?? value.error?.message ?? "请求未完成。", response.status);
  return value as T;
}

export type Me = { user: { id: string; email: string }; csrf_token: string; data_mode: "REPLAY" | "LIVE_RESEARCH"; runtime?: string; experimental_models_enabled?: boolean; can_review_models?: boolean };
export const percent = (value: string) => `${(Number(value) * 100).toFixed(2)}%`; // Display only; Alpha computes all facts.
export const dateTime = (value: string) => new Intl.DateTimeFormat("zh-CN", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value));
