"use client";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import dynamic from "next/dynamic";
import {
  HermesMark,
  WelcomeHeading,
  researchSuggestions,
} from "../components/research-welcome";
import { FeishuLogin } from "../components/feishu-login";
import { LoginLanding } from "../components/login-landing";
import { api, message, statusName } from "../lib/client";
import { useCanvas } from "../lib/canvas";
import { useAccountCollection } from "../lib/use-account-collection";
import { useResponseScope } from "../lib/use-response-scope";

const Chat = dynamic(() => import("../components/chat").then((m) => m.Chat), {
  ssr: false,
  loading: () => <p role="status">正在加载研究对话…</p>,
});
const Canvas = dynamic(
  () => import("../components/canvas").then((m) => m.Canvas),
  {
    ssr: false,
    loading: () => <p role="status">正在加载研究画布…</p>,
  },
);

const profileStates: Record<string, string> = {
  ready: "已连接",
  starting: "启动中",
  stopping: "停止中",
  stopped: "休眠",
  unknown: "待核对",
  unprovisioned: "待连接",
};

function CollectionFeedback({
  name,
  status,
  error,
  retry,
}: {
  name: string;
  status: "loading" | "ready" | "error";
  error: string;
  retry: () => Promise<void>;
}) {
  if (status === "loading")
    return (
      <p className="collection-feedback" role="status">
        正在加载{name}…
      </p>
    );
  if (status !== "error") return null;
  return (
    <div className="collection-feedback collection-error" role="alert">
      <p>
        {name}加载失败。{error}
      </p>
      <button type="button" onClick={() => void retry()}>
        重试加载{name}
      </button>
    </div>
  );
}

export default function Page() {
  const accountScope = useResponseScope();
  const researchScope = useResponseScope();
  const recordsScope = useResponseScope();
  const editorScope = useResponseScope();
  const [menuOpen, setMenuOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [canvasOpen, setCanvasOpen] = useState(false);
  const [canvasExpanded, setCanvasExpanded] = useState(false);
  const artifacts = useCanvas((s) => s.artifacts);
  const canvasTrigger = useRef<HTMLElement | null>(null);
  const draftInput = useRef<HTMLTextAreaElement>(null);
  const starting = useRef(false);
  const drafts = useRef(new Map<string, string>());
  const startRequest = useRef<{
    threadId: string;
    key: string;
    input: string;
    portfolioSnapshotRef?: string;
  } | null>(null);
  const showCanvas = useCallback(() => {
    canvasTrigger.current = document.activeElement as HTMLElement | null;
    setCanvasOpen(true);
  }, []);
  const closeCanvas = useCallback(() => {
    setCanvasOpen(false);
    setCanvasExpanded(false);
    requestAnimationFrame(() => {
      const target = canvasTrigger.current;
      if (target?.isConnected) target.focus();
      else document.querySelector<HTMLButtonElement>(".canvas-toggle")?.focus();
    });
  }, []);
  const [user, setUser] = useState<any>(null),
    [loading, setLoading] = useState(true),
    [register, setRegister] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [threads, setThreads] = useState<any[]>([]),
    [selected, setSelected] = useState<any>(null),
    [profiles, setProfiles] = useState<any[]>([]),
    [portfolioRef, setPortfolioRef] = useState(""),
    [showHoldings, setShowHoldings] = useState(false);
  const holdingsLoad = useAccountCollection<any>(
    "portfolios",
    user?.id ?? null,
  );
  const watchlistLoad = useAccountCollection<any>(
    "watchlist",
    user?.id ?? null,
  );
  const { items: holdings } = holdingsLoad;
  const { items: watchlist, replace: setWatchlist } = watchlistLoad;
  const [logoutError, setLogoutError] = useState("");
  const [loggingOut, setLoggingOut] = useState(false);
  const logoutPending = useRef(false);
  const holdingsPending = useRef(false);
  const [savingHoldings, setSavingHoldings] = useState(false);
  const [holdingsSaved, setHoldingsSaved] = useState("");
  // Bind callbacks to the login that rendered them, including re-login as the same user.
  const isAccountCurrent = useMemo(
    () => accountScope.capture(),
    [accountScope, user],
  );
  const closeHoldings = useCallback(() => {
    editorScope.invalidate();
    holdingsPending.current = false;
    setSavingHoldings(false);
    setShowHoldings(false);
  }, [editorScope]);
  const changeUser = useCallback(
    (next: any) => {
      accountScope.invalidate();
      researchScope.invalidate();
      recordsScope.invalidate();
      closeHoldings();
      starting.current = false;
      setBusy(false);
      setUser(next);
    },
    [accountScope, researchScope, recordsScope, closeHoldings],
  );
  useEffect(() => {
    if (!canvasOpen) return;
    document.querySelector<HTMLButtonElement>(".canvas-close")?.focus();
  }, [canvasOpen]);
  useEffect(() => {
    if (!canvasOpen || showHoldings) return;
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeCanvas();
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, [canvasOpen, showHoldings, closeCanvas]);
  useEffect(() => {
    if (!selected && draft) draftInput.current?.focus();
  }, [selected, draft]);
  useEffect(() => {
    if (!showHoldings) return;
    const previous = document.activeElement as HTMLElement | null;
    const dialog = document.querySelector<HTMLElement>('[role="dialog"]');
    const focusable = () =>
      Array.from(
        dialog?.querySelectorAll<HTMLElement>("button,input,textarea,select") ??
          [],
      );
    focusable()[0]?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeHoldings();
      if (e.key === "Tab") {
        const items = focusable(),
          first = items[0],
          last = items.at(-1);
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last?.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("keydown", key);
      previous?.focus();
    };
  }, [showHoldings, closeHoldings]);
  const refresh = useCallback(() => {
    if (!isAccountCurrent()) return;
    const isLatest = recordsScope.begin();
    api<any[]>("threads")
      .then((value) => {
        if (isAccountCurrent() && isLatest()) setThreads(value);
      })
      .catch((e) => {
        if (isAccountCurrent() && isLatest())
          setError(`研究记录加载失败。${message(e)} 可刷新页面重试。`);
      });
  }, [isAccountCurrent, recordsScope]);
  const open = useCallback(
    async (id: string) => {
      if (!isAccountCurrent()) return false;
      const isLatest = researchScope.begin();
      try {
        const thread = await api(`threads/${id}`);
        if (!isAccountCurrent() || !isLatest()) return false;
        useCanvas.getState().load(thread.workspace);
        setSelected(thread);
        setCanvasOpen(false);
        setCanvasExpanded(false);
        setMenuOpen(false);
        setError("");
        return true;
      } catch (e) {
        if (isAccountCurrent() && isLatest())
          setError(`无法打开研究。${message(e)} 请在研究记录中重新选择研究。`);
        return false;
      }
    },
    [isAccountCurrent, researchScope],
  );
  useEffect(() => {
    const isCurrent = accountScope.capture();
    api("auth/session")
      .then((value) => {
        if (isCurrent()) changeUser(value);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [accountScope, changeUser]);
  useEffect(() => {
    if (!user) return;
    refresh();
  }, [user, refresh]);
  useEffect(() => {
    let active = true;
    setProfiles([]);
    if (!user) return;
    const update = () => {
      if (document.visibilityState !== "visible") return;
      void api<any[]>("profiles")
        .then((value) => {
          if (active && isAccountCurrent()) setProfiles(value);
        })
        .catch(() => {});
    };
    update();
    const timer = setInterval(update, 5000);
    document.addEventListener("visibilitychange", update);
    return () => {
      active = false;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", update);
    };
  }, [user, isAccountCurrent]);
  async function login(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError("");
    const form = new FormData(e.currentTarget);
    try {
      changeUser(
        await api(`auth/${register ? "register" : "login"}`, "POST", {
          email: form.get("email"),
          password: form.get("password"),
        }),
      );
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  function newThread(symbol?: string) {
    if (starting.current) return;
    researchScope.invalidate();
    setSelected(null);
    setDraft((previous) =>
      symbol ? `分析 ${symbol} 最近三个月走势，并计算 MA20 和 RSI。` : previous,
    );
    startRequest.current = null;
    useCanvas.getState().load(null);
    setCanvasOpen(false);
    setCanvasExpanded(false);
    setMenuOpen(false);
    setError("");
    requestAnimationFrame(() => draftInput.current?.focus());
  }
  async function startResearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (starting.current || !draft.trim()) return;
    starting.current = true;
    setBusy(true);
    setError("");
    try {
      if (!startRequest.current) {
        const thread = await api("threads", "POST");
        if (!isAccountCurrent()) return;
        startRequest.current = {
          threadId: thread.id,
          key: crypto.randomUUID(),
          input: draft.trim(),
          ...(portfolioRef ? { portfolioSnapshotRef: portfolioRef } : {}),
        };
      }
      const { key, ...body } = startRequest.current;
      await api("research", "POST", body, { "Idempotency-Key": key });
      if (!isAccountCurrent()) return;
      // Keep the request key until both admission and opening succeed. A retry
      // after a lost response resumes the same task instead of submitting twice.
      const opened = await open(body.threadId);
      if (!isAccountCurrent()) return;
      if (!opened) throw new Error("connection_failed");
      startRequest.current = null;
      setDraft("");
      refresh();
    } catch (e) {
      if (isAccountCurrent()) setError(message(e));
    } finally {
      if (isAccountCurrent()) {
        starting.current = false;
        setBusy(false);
      }
    }
  }
  async function logout() {
    if (logoutPending.current) return;
    logoutPending.current = true;
    setLoggingOut(true);
    setLogoutError("");
    try {
      await api("auth/logout", "POST");
      changeUser(null);
      setSelected(null);
      setThreads([]);
      setPortfolioRef("");
      setDraft("");
      drafts.current = new Map();
      startRequest.current = null;
      setCanvasOpen(false);
      setCanvasExpanded(false);
      setShowHoldings(false);
      setHoldingsSaved("");
      setError("");
      useCanvas.getState().load(null);
    } catch (error) {
      setLogoutError(`尚未退出。${message(error)}`);
    } finally {
      logoutPending.current = false;
      setLoggingOut(false);
    }
  }
  async function saveHoldings(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (holdingsPending.current) return;
    holdingsPending.current = true;
    const isEditorCurrent = editorScope.capture();
    setSavingHoldings(true);
    setHoldingsSaved("");
    setError("");
    const f = new FormData(e.currentTarget);
    try {
      const positions = String(f.get("positions"))
        .split("\n")
        .filter((l) => l.trim())
        .map((l) => {
          const [symbol, quantity] = l.trim().split(/[ ,，]+/);
          return { symbol: symbol?.toUpperCase(), quantity };
        });
      const saved = await api("portfolios", "POST", {
        asOfSession: f.get("date"),
        cashUsd: f.get("cash"),
        positions,
      });
      if (!isAccountCurrent()) return;
      // A completed save still refreshes this account's list, but never changes a new editor.
      void holdingsLoad.reload();
      if (!isEditorCurrent()) return;
      setPortfolioRef(saved.snapshotId);
      closeHoldings();
      setHoldingsSaved("模拟持仓已保存。");
    } catch (e) {
      if (isAccountCurrent() && isEditorCurrent()) setError(message(e));
    } finally {
      if (isAccountCurrent() && isEditorCurrent()) {
        holdingsPending.current = false;
        setSavingHoldings(false);
      }
    }
  }
  if (loading)
    return (
      <main className="entry-loading" role="status">
        <span className="entry-loading-mark" aria-hidden="true">
          ▲
        </span>
        正在打开研究工作台…
      </main>
    );
  if (!user)
    return (
      <LoginLanding
        register={register}
        busy={busy}
        error={error}
        onSubmit={login}
        onMode={(mode) => {
          setRegister(mode);
          setError("");
        }}
      />
    );
  return (
    <div className="app">
      <header className="topbar">
        <span className="product-label">美股研究工作台</span>
        <span className="workspace-mode">RESEARCH / WORKSPACE</span>
        <button
          className="canvas-toggle"
          aria-expanded={canvasOpen}
          aria-controls="research-canvas"
          onClick={canvasOpen ? closeCanvas : showCanvas}
        >
          <svg viewBox="0 0 20 20" aria-hidden="true">
            <rect
              x="3"
              y="4"
              width="14"
              height="12"
              rx="1"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
            />
            <path d="M10 4v12" stroke="currentColor" strokeWidth="1.5" />
          </svg>
          画布 <span>{artifacts.length}</span>
        </button>
        <div className="account">
          <span className="account-avatar" aria-hidden="true">
            {(user.email || "飞书用户").slice(0, 1).toUpperCase()}
          </span>
          <span>{user.email || "飞书用户"}</span>
          <details>
            <summary>绑定飞书</summary>
            <FeishuLogin binding />
          </details>
          <button onClick={() => void logout()} disabled={loggingOut}>
            {loggingOut ? "正在退出…" : "退出"}
          </button>
        </div>
      </header>
      {error && !showHoldings && (
        <div className="account-error" role="alert">
          {error}
        </div>
      )}
      {logoutError && (
        <div className="account-error" role="alert">
          <span>{logoutError}</span>
          <button
            type="button"
            onClick={() => void logout()}
            disabled={loggingOut}
          >
            重试退出
          </button>
        </div>
      )}
      <div className="workspace">
        <aside className={menuOpen ? "sidebar expanded" : "sidebar"}>
          <div className="wordmark">
            <HermesMark /> Hermes <span>ALPHA LAB</span>
          </div>

          <button
            className="mobile-menu"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen(!menuOpen)}
          >
            研究记录与持仓 {menuOpen ? "−" : "＋"}
          </button>
          <button
            className="primary new-research"
            disabled={busy}
            onClick={() => void newThread()}
          >
            ＋ 新建研究
          </button>
          <div className="watch-section">
            <a href="/lab" className="research-hub-link">
              研究池 · 预测记录 · 经验试验 ↗
            </a>
            <div className="sidebar-label">
              自选股{" "}
              <span className="watch-count">
                {watchlistLoad.status === "ready" ? watchlist.length : "—"}
              </span>
            </div>
            <form
              className="watch-add"
              onSubmit={async (e) => {
                e.preventDefault();
                const form = e.currentTarget;
                setError("");
                try {
                  const updated = await api("watchlist", "POST", {
                    symbol: String(
                      new FormData(form).get("symbol"),
                    ).toUpperCase(),
                  });
                  if (!isAccountCurrent()) return;
                  setWatchlist(updated);
                  form.reset();
                } catch (err) {
                  if (!isAccountCurrent()) return;
                  setError(
                    `未添加自选股。${message(err)} 请再次点击添加自选股。`,
                  );
                }
              }}
            >
              <input
                name="symbol"
                placeholder="添加股票代码"
                aria-label="自选股票代码"
                required
              />
              <button aria-label="添加自选股">＋</button>
            </form>
            <CollectionFeedback
              name="自选股"
              status={watchlistLoad.status}
              error={watchlistLoad.error}
              retry={watchlistLoad.reload}
            />
            <div className="symbols">
              {watchlist.map((w) => (
                <button
                  key={w.symbol}
                  disabled={busy}
                  onClick={() => void newThread(w.symbol)}
                  aria-label={`研究 ${w.symbol}`}
                >
                  <strong>{w.symbol}</strong>
                  <span>研究 ↗</span>
                </button>
              ))}
              {watchlistLoad.status === "ready" && !watchlist.length && (
                <p className="watch-empty">添加关注的股票，随时开始研究。</p>
              )}
            </div>
          </div>
          <div className="sidebar-label">研究记录</div>
          <nav aria-label="研究记录">
            {threads.map((t) => (
              <button
                key={t.id}
                className={
                  selected?.id === t.id ? "thread-link active" : "thread-link"
                }
                disabled={busy}
                onClick={() => open(t.id)}
              >
                <span>◷</span>
                <span className="thread-title">
                  {t.source === "group"
                    ? "群研究 · "
                    : t.channel === "feishu_private"
                      ? "飞书私聊 · "
                      : "网页 · "}
                  {t.title}
                </span>
                <small>
                  {new Date(t.created_at).toLocaleDateString("zh-CN", {
                    month: "2-digit",
                    day: "2-digit",
                  })}
                </small>
              </button>
            ))}
            {!threads.length && <p className="sidebar-empty">尚无研究记录</p>}
          </nav>
          <div className="profile-strip" aria-label="Agent 状态">
            <div className="sidebar-label">AGENT 状态</div>
            {["research", "prediction", "portfolio"].map((role, i) => (
              <div
                key={role}
                data-state={
                  profiles.find((p) => p.role === role)?.state ??
                  "unprovisioned"
                }
              >
                <span className="profile-dot" />
                <strong>{["研究", "预测", "组合"][i]}</strong>
                <span>
                  {profileStates[
                    profiles.find((p) => p.role === role)?.state
                  ] ?? "待连接"}
                </span>
              </div>
            ))}
          </div>
          <div className="sidebar-bottom">
            <p className="collection-feedback" role="status">
              {holdingsSaved}
            </p>
            <CollectionFeedback
              name="模拟持仓"
              status={holdingsLoad.status}
              error={holdingsLoad.error}
              retry={holdingsLoad.reload}
            />
            {holdingsLoad.status === "ready" && !holdings.length && (
              <p className="portfolio-empty">
                尚无模拟持仓，可保存一份新快照。
              </p>
            )}
            {holdings.length > 0 && (
              <label className="portfolio-select">
                模拟持仓
                <select
                  value={portfolioRef}
                  onChange={(e) => setPortfolioRef(e.target.value)}
                >
                  <option value="">未选择</option>
                  {holdings.map((h) => (
                    <option key={h.id} value={h.snapshot_id}>
                      {h.name} · {h.as_of_session}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <button
              className="holdings-button"
              onClick={() => {
                editorScope.invalidate();
                holdingsPending.current = false;
                setSavingHoldings(false);
                setError("");
                setShowHoldings(true);
              }}
            >
              编辑模拟持仓 ↗
            </button>
            <p className="caption">
              研究与模拟用途
              <br />
              不执行真实交易
            </p>
          </div>
        </aside>
        <main
          className={`desk${canvasOpen ? " canvas-visible" : ""}${canvasExpanded ? " canvas-expanded" : ""}`}
        >
          <div className="conversation-column">
            {selected ? (
              <Chat
                key={selected.id}
                thread={selected}
                drafts={drafts.current}
                portfolioRef={portfolioRef || undefined}
                onChange={refresh}
                onOpenCanvas={showCanvas}
                canvasOpen={canvasOpen}
                profileState={
                  profiles.find((p) => p.role === "research")?.state
                }
              />
            ) : (
              <section className="research-welcome" aria-label="开始研究">
                <WelcomeHeading />
                <form
                  className="composer welcome-composer"
                  onSubmit={startResearch}
                >
                  <textarea
                    ref={draftInput}
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    aria-label="研究问题"
                    placeholder="向 Hermes 提问，例如：查一下特斯拉最近的走势"
                    rows={2}
                    maxLength={16000}
                    disabled={busy}
                    readOnly={!!startRequest.current}
                    onKeyDown={(e) => {
                      if (
                        e.key === "Enter" &&
                        !e.shiftKey &&
                        !e.nativeEvent.isComposing
                      ) {
                        e.preventDefault();
                        e.currentTarget.form?.requestSubmit();
                      }
                    }}
                  />
                  <div className="composer-bottom">
                    <small className="composer-hint">
                      Enter 发送 · Shift + Enter 换行
                    </small>
                    <span
                      className="agent-label"
                      data-state={
                        profiles.find((p) => p.role === "research")?.state
                      }
                    >
                      <i />
                      Hermes Agent
                    </span>
                    <button
                      type="submit"
                      aria-label="发送研究"
                      disabled={busy || !draft.trim()}
                    >
                      {busy ? "…" : "↑"}
                    </button>
                  </div>
                  {error && startRequest.current && (
                    <small className="retry-hint">
                      再次发送将恢复同一项研究。
                    </small>
                  )}
                </form>
                <div className="research-suggestions" aria-label="示例问题">
                  {researchSuggestions.map((s) => (
                    <button
                      key={s.label}
                      disabled={busy || !!startRequest.current}
                      onClick={() => setDraft(s.prompt)}
                    >
                      {s.label}
                    </button>
                  ))}
                </div>
              </section>
            )}
            {selected?.tasks?.length > 0 && (
              <details className="history">
                <summary>任务记录 · {selected.tasks.length} 项</summary>
                {selected.tasks.map((t: any) => (
                  <div key={t.id}>
                    <span>
                      {new Date(t.created_at).toLocaleString("zh-CN")}
                    </span>
                    <span>{statusName[t.status]}</span>
                    <button
                      onClick={async () => {
                        const isLatest = researchScope.begin();
                        try {
                          const result = await api(`tasks/${t.id}`);
                          if (!isAccountCurrent() || !isLatest()) return;
                          useCanvas.getState().showHistory(result);
                          showCanvas();
                        } catch (e) {
                          if (isAccountCurrent() && isLatest())
                            setError(message(e));
                        }
                      }}
                    >
                      查看此轮产物
                    </button>
                  </div>
                ))}
              </details>
            )}
          </div>
          {canvasOpen && (
            <aside id="research-canvas" className="canvas-drawer">
              <Canvas
                onStart={() => newThread()}
                onClose={closeCanvas}
                expanded={canvasExpanded}
                onToggleExpanded={() => setCanvasExpanded((v) => !v)}
              />
            </aside>
          )}
        </main>
      </div>
      {showHoldings && (
        <div className="modal-backdrop">
          <section
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="holdings-title"
          >
            <button
              className="modal-close"
              aria-label="关闭持仓编辑"
              onClick={closeHoldings}
            >
              ×
            </button>
            <h2 id="holdings-title">模拟持仓</h2>
            <p>保存为新快照，已有研究使用的持仓版本不会改变。</p>
            <form onSubmit={saveHoldings}>
              <label>
                持仓日期
                <input name="date" type="date" required />
              </label>
              <label>
                股票与数量
                <textarea
                  name="positions"
                  placeholder={"NVDA 10\nAAPL 5"}
                  rows={5}
                />
              </label>
              <small>每行一只股票，代码和数量用空格分隔</small>
              <label>
                美元现金
                <input
                  name="cash"
                  defaultValue="0"
                  inputMode="decimal"
                  required
                />
              </label>
              {error && (
                <p role="alert" className="notice">
                  {error}
                </p>
              )}
              <button className="primary" aria-disabled={savingHoldings}>
                {savingHoldings ? "正在保存…" : "保存新快照"}
              </button>
            </form>
          </section>
        </div>
      )}
    </div>
  );
}
