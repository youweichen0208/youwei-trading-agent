"use client";
import { useEffect, useState } from "react";
import { api, message } from "../lib/client";

export function FeishuLogin({
  onStateChange,
}: {
  onStateChange?: (available: boolean) => void;
}) {
  const [oauthEnabled, setOAuthEnabled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api<{ feishuOAuth: boolean }>("auth/options")
      .then((data) => {
        if (!active) return;
        setOAuthEnabled(data.feishuOAuth);
        onStateChange?.(data.feishuOAuth);
      })
      .catch(() => {
        if (active) onStateChange?.(false);
      });
    return () => {
      active = false;
    };
  }, [onStateChange]);
  if (!oauthEnabled) return null;
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
  return (
    <section className="entry-feishu" aria-label="飞书登录" aria-busy={busy}>
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
      {error && (
        <p className="entry-feishu-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
