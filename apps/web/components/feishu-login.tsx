"use client";
import { useEffect, useId, useState } from "react";
import { api, message } from "../lib/client";

export function FeishuLogin({
  binding = false,
  onStateChange,
}: {
  binding?: boolean;
  onStateChange?: (available: boolean) => void;
}) {
  const [oauthEnabled, setOAuthEnabled] = useState(false);
  const [enabled, setEnabled] = useState(false);
  const [challenge, setChallenge] = useState<{ id: string; command: string }>();
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const commandId = useId();
  useEffect(() => {
    let active = true;
    api<{ feishuLogin: boolean; feishuOAuth: boolean }>("auth/options")
      .then((data) => {
        if (!active) return;
        setEnabled(data.feishuLogin);
        setOAuthEnabled(data.feishuOAuth);
        onStateChange?.(data.feishuLogin || data.feishuOAuth);
      })
      .catch(() => {
        if (active) onStateChange?.(false);
      });
    return () => {
      active = false;
    };
  }, [onStateChange]);
  useEffect(() => {
    if (!challenge || confirmed) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const result = await api<{ confirmed: boolean }>(
          `auth/feishu/status?id=${challenge.id}`,
        );
        if (!active) return;
        if (result.confirmed) setConfirmed(true);
        else timer = setTimeout(poll, 2500);
      } catch (e) {
        if (active) {
          setError(message(e));
          setChallenge(undefined);
        }
      }
    };
    timer = setTimeout(poll, 2500);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [challenge, confirmed]);
  if (!enabled && !oauthEnabled) return null;
  async function oauth() {
    setBusy(true);
    setError("");
    try {
      const result = await api<{ url: string }>(
        "auth/feishu/oauth-start",
        "POST",
        {},
      );
      window.location.assign(result.url);
    } catch (e) {
      setError(message(e));
      setBusy(false);
    }
  }
  async function start() {
    setBusy(true);
    setError("");
    setConfirmed(false);
    setCopied(false);
    try {
      setChallenge(
        await api(
          binding ? "auth/feishu/bind-challenge" : "auth/feishu/challenge",
          "POST",
          {},
        ),
      );
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  async function copy() {
    if (!challenge) return;
    try {
      await navigator.clipboard.writeText(challenge.command);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      document.getElementById(commandId)?.focus();
    }
  }
  async function finish() {
    setBusy(true);
    setError("");
    try {
      await api("auth/feishu/complete", "POST", { id: challenge?.id });
      window.location.reload();
    } catch (e) {
      setError(message(e));
      setBusy(false);
    }
  }
  return (
    <section className="entry-feishu" aria-label="飞书登录" aria-busy={busy}>
      {oauthEnabled && !binding && (
        <button
          type="button"
          className="entry-feishu-button"
          disabled={busy}
          onClick={oauth}
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 8.9 8.9 0 0 1-3.8-.9L3 20l1-4.6a8.5 8.5 0 0 1-1-4A8.4 8.4 0 0 1 12 3a8.4 8.4 0 0 1 9 8.5Z" />
          </svg>
          企业内部飞书授权登录
        </button>
      )}
      {enabled && !challenge && (
        <button
          type="button"
          className="entry-feishu-button"
          disabled={busy}
          onClick={start}
        >
          {binding ? "绑定飞书账号" : "通过飞书助手登录"}
        </button>
      )}
      {challenge && (
        <div className="entry-feishu-card">
          <p className="entry-feishu-hint">
            将以下命令发送到「陈家美股助手」私聊，五分钟内有效。仅确认你本人发起的登录，不要转发给他人。
          </p>
          <div className="entry-feishu-command">
            <label htmlFor={commandId}>登录确认命令</label>
            <span className="entry-feishu-input">
              <input
                id={commandId}
                readOnly
                value={challenge.command}
                onFocus={(event) => event.currentTarget.select()}
              />
              <button
                type="button"
                className="entry-feishu-copy"
                onClick={copy}
              >
                {copied ? "已复制" : "复制"}
              </button>
            </span>
          </div>
          <div className="entry-feishu-actions">
            <p className="entry-feishu-status" role="status">
              <span
                className={`entry-feishu-dot${confirmed ? " is-done" : ""}`}
                aria-hidden="true"
              />
              {confirmed ? "飞书已确认，请完成登录。" : "等待私聊确认…"}
            </p>
            <button
              type="button"
              className="entry-feishu-regenerate"
              disabled={busy}
              onClick={start}
            >
              重新生成
            </button>
          </div>
          {confirmed && (
            <button
              type="button"
              className="entry-feishu-finish"
              disabled={busy}
              onClick={finish}
            >
              {binding ? "完成绑定" : "完成登录"}
            </button>
          )}
        </div>
      )}
      {error && (
        <p className="entry-feishu-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
