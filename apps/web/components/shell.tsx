"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, HttpError, type Me } from "../lib/api";

export function Shell({ children }: { children: (me: Me) => React.ReactNode }) {
  const [me, setMe] = useState<Me>();
  const [error, setError] = useState("");
  const router = useRouter(); const pathname = usePathname();
  useEffect(() => { void api<Me>("/me").then(setMe).catch(e => { if (e instanceof HttpError && e.status === 401) router.replace("/login"); else setError(e.message); }); }, [router]);
  if (!me) return <main className="loading-screen"><div className="brand">有为<span>YTA / RESEARCH</span></div><p role="status">{error || "正在打开研究工作台…"}</p>{error && <button onClick={() => location.reload()}>重新连接</button>}</main>;
  return <div className="app-shell">
    <header className="topbar"><Link href="/" className="brand">有为<span>RESEARCH</span></Link>
      <nav aria-label="主导航"><Link className={pathname === "/" ? "active" : ""} aria-current={pathname === "/" ? "page" : undefined} href="/">NVDA 概览</Link><Link className={pathname.startsWith("/research") ? "active" : ""} href="/research">研究工作台</Link><Link className={pathname.startsWith("/predictions") ? "active" : ""} href="/predictions">预测记录</Link>{me.experimental_models_enabled && <Link className={(pathname.startsWith("/backtests") || pathname.startsWith("/models") || pathname.startsWith("/model-evaluations") || pathname.startsWith("/model-proposals") || pathname.startsWith("/model-quality") || pathname.startsWith("/model-calibrations") || pathname.startsWith("/probability-quality") || pathname.startsWith("/probability-checks")) ? "active" : ""} aria-current={(pathname.startsWith("/backtests") || pathname.startsWith("/models") || pathname.startsWith("/model-evaluations") || pathname.startsWith("/model-proposals") || pathname.startsWith("/model-quality") || pathname.startsWith("/model-calibrations") || pathname.startsWith("/probability-quality") || pathname.startsWith("/probability-checks")) ? "page" : undefined} href="/models">实验模型</Link>}{me.experimental_models_enabled && me.can_review_models && <Link href="/model-review-proposals" className={pathname.startsWith("/model-review") ? "active" : ""}>模型审核</Link>}</nav>
      <div className="account"><span>{me.user.email}</span><button className="text-button" onClick={() => { void api("/auth/logout", { body: {}, csrf: me.csrf_token }).then(() => router.replace("/login")).catch(e => setError(e.message)); }}>退出</button></div>
    </header>
    <div className={`environment-bar ${me.data_mode === "REPLAY" ? "replay" : ""}`}><span className="status-dot"/><strong>{me.data_mode === "REPLAY" ? "回放演练" : "前瞻研究"}</strong><span>{me.data_mode === "REPLAY" ? (me.runtime === "hermes" ? "合成行情 · 模型研究 · 不计入真实前瞻成绩" : "合成行情 · 固定流程验证 · 未连接真实 Hermes") : "价格研究，不代表实际交易收益"}</span><span className="mode-code">{me.data_mode}</span></div>
    {error && <div role="alert" className="alert global-alert">{error}</div>}
    {children(me)}
  </div>;
}
