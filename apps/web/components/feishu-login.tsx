"use client";
import { useEffect, useState } from "react";
import { api, message } from "../lib/client";

export function FeishuLogin({ binding = false }: { binding?: boolean }) {
  const [oauthEnabled, setOAuthEnabled] = useState(false);
  const [enabled, setEnabled] = useState(false);
  const [challenge, setChallenge] = useState<{ id: string; command: string }>();
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api<{ feishuLogin: boolean; feishuOAuth: boolean }>("auth/options")
      .then((data) => {
        if (active) {
          setEnabled(data.feishuLogin);
          setOAuthEnabled(data.feishuOAuth);
        }
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);
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
    <section aria-label="飞书登录" aria-busy={busy}>
      {oauthEnabled && !binding && (
        <button type="button" disabled={busy} onClick={oauth}>
          企业内部飞书授权登录
        </button>
      )}
      {enabled && (
        <button type="button" disabled={busy} onClick={start}>
          {challenge
            ? "重新生成登录码"
            : binding
              ? "绑定飞书账号"
              : "通过飞书助手登录"}
        </button>
      )}
      {challenge && (
        <>
          <p>
            将以下命令发送到「陈家美股助手」私聊，五分钟内有效。仅确认你本人发起的登录，不要转发给他人。
          </p>
          <label>
            登录确认命令
            <input
              readOnly
              value={challenge.command}
              onFocus={(event) => event.currentTarget.select()}
            />
          </label>
          <p role="status">
            {confirmed ? "飞书已确认，请完成登录。" : "等待私聊确认…"}
          </p>
          {confirmed && (
            <button type="button" disabled={busy} onClick={finish}>
              {binding ? "完成绑定" : "完成登录"}
            </button>
          )}
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
