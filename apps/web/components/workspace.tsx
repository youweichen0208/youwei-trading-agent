"use client";
import { ComposerTools } from "./composer-tools";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import dynamic from "next/dynamic";
import {
  HermesMark,
  SuggestionIcon,
  WelcomeHeading,
  researchSuggestions,
} from "./research-welcome";
import { FeishuLogin } from "./feishu-login";
import { LoginLanding } from "./login-landing";
import { api, message, statusName } from "../lib/client";
import { useCanvas } from "../lib/canvas";
import { useAccountCollection } from "../lib/use-account-collection";
import { useResponseScope } from "../lib/use-response-scope";

const Chat = dynamic(() => import("./chat").then((m) => m.Chat), {
  ssr: false,
  loading: () => <p role="status">正在加载研究对话…</p>,
});
const Canvas = dynamic(() => import("./canvas").then((m) => m.Canvas), {
  ssr: false,
  loading: () => <p role="status">正在加载研究画布…</p>,
});

const CHAT_MIN_WIDTH = 420;
const CHAT_CANVAS_MIN_WIDTH = 320;
const CHAT_DEFAULT_RATIO = 0.5;
const CHAT_MAX_RATIO = 0.6;
const CHAT_RATIO_KEY = "hermes:chat-ratio:v3";

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

const StockDetail = dynamic(() =>
  import("./stock-detail").then((m) => m.StockDetail),
);
const ResearchHub = dynamic(
  () => import("./research-hub").then((m) => m.ResearchHub),
  { loading: () => <p role="status">正在加载研究中心…</p> },
);
const KnowledgeBase = dynamic(
  () => import("./knowledge-base").then((m) => m.KnowledgeBase),
  { loading: () => <p role="status">正在加载知识沉淀…</p> },
);
const PersonalNotes = dynamic(
  () => import("./personal-notes").then((m) => m.PersonalNotes),
  { loading: () => <p role="status">正在加载个人笔记…</p> },
);
export default function Workspace() {
  const pathname = usePathname();
  const router = useRouter();
  const hubOpen = pathname === "/lab";
  const knowledgeOpen = pathname === "/knowledge";
  const notesOpen = pathname === "/notes";
  const stockSymbol = pathname.startsWith("/stocks/")
    ? decodeURIComponent(pathname.split("/")[2] ?? "").toUpperCase()
    : null;
  const [hubVisited, setHubVisited] = useState(hubOpen);
  useEffect(() => {
    if (hubOpen) setHubVisited(true);
  }, [hubOpen]);
  const accountScope = useResponseScope();
  const researchScope = useResponseScope();
  const recordsScope = useResponseScope();
  const editorScope = useResponseScope();
  const [menuOpen, setMenuOpen] = useState(false);
  useEffect(() => {
    if (hubOpen || knowledgeOpen || notesOpen || stockSymbol) {
      setCanvasOpen(true);
      setMenuOpen(false);
    }
  }, [pathname]);
  useEffect(() => {
    if (!menuOpen) return;
    const previous = document.activeElement as HTMLElement | null;
    const panel = document.querySelector<HTMLElement>(".sidebar.expanded");
    const elements = () =>
      Array.from(
        panel?.querySelectorAll<HTMLElement>(
          "button:not([disabled]),a,input,summary,select",
        ) ?? [],
      ).filter((el) => el.getClientRects().length);
    elements()[0]?.focus();
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuOpen(false);
      if (e.key === "Tab") {
        const items = elements(),
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
    document.addEventListener("keydown", handler);
    return () => {
      document.removeEventListener("keydown", handler);
      previous?.focus();
    };
  }, [menuOpen]);
  const [draft, setDraft] = useState("");
  const [canvasOpen, setCanvasOpen] = useState(false);
  const [chatWidth, setChatWidth] = useState(0);
  const deskRef = useRef<HTMLElement>(null);
  const chatWidthRef = useRef(0);
  const chatRatioRef = useRef(CHAT_DEFAULT_RATIO);
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
    requestAnimationFrame(() => {
      const target = canvasTrigger.current;
      if (target?.isConnected) target.focus();
      else document.querySelector<HTMLButtonElement>(".canvas-toggle")?.focus();
    });
  }, []);
  const ratioBounds = useCallback(() => {
    const width = deskRef.current?.clientWidth || 1280;
    const min = CHAT_MIN_WIDTH / width;
    const max = Math.max(
      min,
      Math.min(CHAT_MAX_RATIO, 1 - (CHAT_CANVAS_MIN_WIDTH + 6) / width),
    );
    return { width, min, max };
  }, []);
  const maxChatWidth = useCallback(() => {
    const { width, max } = ratioBounds();
    return Math.round(max * width);
  }, [ratioBounds]);
  const applyChatRatio = useCallback(
    (ratio: number, persist = true) => {
      const { width, min, max } = ratioBounds();
      const nextRatio = Math.min(Math.max(ratio, min), max);
      const nextWidth = Math.max(CHAT_MIN_WIDTH, Math.round(nextRatio * width));
      chatRatioRef.current = nextRatio;
      chatWidthRef.current = nextWidth;
      setChatWidth(nextWidth);
      if (persist)
        window.localStorage.setItem(CHAT_RATIO_KEY, String(nextRatio));
    },
    [ratioBounds],
  );
  useEffect(() => {
    const saved = Number(window.localStorage.getItem(CHAT_RATIO_KEY));
    applyChatRatio(
      Number.isFinite(saved) && saved > 0 ? saved : CHAT_DEFAULT_RATIO,
      false,
    );
  }, [applyChatRatio]);
  useEffect(() => {
    const desk = deskRef.current;
    if (!desk || typeof ResizeObserver === "undefined") {
      const onResize = () => applyChatRatio(chatRatioRef.current, false);
      window.addEventListener("resize", onResize);
      return () => window.removeEventListener("resize", onResize);
    }
    // Re-derive the pixel width from the stored ratio so the split keeps its
    // proportions when the window, sidebar or canvas changes size.
    const observer = new ResizeObserver(() =>
      applyChatRatio(chatRatioRef.current, false),
    );
    observer.observe(desk);
    return () => observer.disconnect();
  }, [applyChatRatio]);
  const startChatResize = useCallback(
    (event: ReactPointerEvent<HTMLDivElement>) => {
      if (event.button !== 0) return;
      event.preventDefault();
      const move = (moveEvent: PointerEvent) => {
        const desk = deskRef.current;
        const rect = desk?.getBoundingClientRect();
        if (!rect?.width) return;
        applyChatRatio((moveEvent.clientX - rect.left) / rect.width, false);
      };
      const stop = () => {
        document.removeEventListener("pointermove", move);
        document.removeEventListener("pointerup", stop);
        document.body.classList.remove("is-resizing");
        window.localStorage.setItem(
          CHAT_RATIO_KEY,
          String(chatRatioRef.current),
        );
      };
      document.body.classList.add("is-resizing");
      document.addEventListener("pointermove", move);
      document.addEventListener("pointerup", stop);
    },
    [applyChatRatio],
  );
  const resizeChatWithKeyboard = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>) => {
      const step = event.shiftKey ? 80 : 24;
      const { width } = ratioBounds();
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        applyChatRatio((chatWidthRef.current - step) / width);
      } else if (event.key === "ArrowRight") {
        event.preventDefault();
        applyChatRatio((chatWidthRef.current + step) / width);
      } else if (event.key === "Home") {
        event.preventDefault();
        applyChatRatio(0);
      } else if (event.key === "End") {
        event.preventDefault();
        applyChatRatio(1);
      }
    },
    [applyChatRatio, ratioBounds],
  );
  const [user, setUser] = useState<any>(null),
    [loading, setLoading] = useState(true),
    [register, setRegister] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [threads, setThreads] = useState<any[]>([]),
    [selected, setSelected] = useState<any>(null),
    [deleteTarget, setDeleteTarget] = useState<any>(null),
    [deleting, setDeleting] = useState(false),
    [deleteError, setDeleteError] = useState(""),
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
  useEffect(() => {
    if (!deleteTarget) return;
    const previous = document.activeElement as HTMLElement | null;
    const dialog = document.querySelector<HTMLElement>(".confirm-modal");
    const buttons = () =>
      Array.from(
        dialog?.querySelectorAll<HTMLButtonElement>(
          "button:not([disabled])",
        ) ?? [],
      );
    buttons()[0]?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !deleting) setDeleteTarget(null);
      if (event.key === "Tab") {
        const items = buttons(),
          first = items[0],
          last = items.at(-1);
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("keydown", key);
      previous?.focus();
    };
  }, [deleteTarget, deleting]);
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
        router.push("/");
        setCanvasOpen(true);
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
    router.push("/");
    if (starting.current) return;
    researchScope.invalidate();
    setSelected(null);
    setDraft((previous) =>
      symbol ? `分析 ${symbol} 最近三个月走势，并计算 MA20 和 RSI。` : previous,
    );
    startRequest.current = null;
    useCanvas.getState().load(null);
    setCanvasOpen(true);
    setMenuOpen(false);
    setError("");
    requestAnimationFrame(() => draftInput.current?.focus());
  }
  async function deleteThread() {
    if (!deleteTarget || deleting) return;
    const target = deleteTarget;
    setDeleting(true);
    setDeleteError("");
    try {
      await api(`threads/${target.id}`, "DELETE");
      if (!isAccountCurrent()) return;
      drafts.current.delete(target.id);
      setThreads((list) => list.filter((t) => t.id !== target.id));
      if (selected?.id === target.id) newThread();
      setDeleteTarget(null);
    } catch (e) {
      if (isAccountCurrent()) setDeleteError(message(e));
    } finally {
      if (isAccountCurrent()) setDeleting(false);
    }
  }
  const [submission, setSubmission] = useState<{
    input: string;
    stage: string;
  } | null>(null);
  async function startResearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (starting.current || !draft.trim()) return;
    starting.current = true;
    setSubmission({
      input: startRequest.current?.input ?? draft.trim(),
      stage: "正在提交研究",
    });
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
      setSubmission({ input: body.input, stage: "任务已提交，正在打开对话" });
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
        setSubmission(null);
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
      setCanvasOpen(true);
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
      <main className="entry-loading" aria-busy="true">
        <div className="entry-loading-content">
          <div className="entry-loading-emblem" aria-hidden="true">
            <HermesMark />
          </div>
          <p className="entry-loading-brand">
            Hermes<span>RESEARCH WORKSPACE</span>
          </p>
          <div className="entry-loading-divider" aria-hidden="true" />
          <h1>让每一个判断，有据可循。</h1>
          <div className="entry-loading-status" role="status">
            <span className="entry-loading-orbit" aria-hidden="true" />
            正在打开研究工作台
          </div>
        </div>
        <p className="entry-loading-footer">行情 · 洞察 · 研究</p>
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
  const pageTitle =
    stockSymbol ??
    (hubOpen
      ? "研究中心"
      : knowledgeOpen
        ? "知识沉淀"
        : notesOpen
          ? "个人笔记"
          : selected?.title || "新的研究");
  const pageMode = stockSymbol
    ? "个股详情"
    : hubOpen
      ? "研究"
      : knowledgeOpen
        ? "知识库"
        : notesOpen
          ? "风格与心得"
          : "研究对话";
  return (
    <div className={`app${canvasOpen ? " mobile-results" : ""}`}>
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
      {menuOpen && (
        <button
          className="nav-scrim"
          aria-label="关闭导航"
          onClick={() => setMenuOpen(false)}
        />
      )}
      <aside className={menuOpen ? "sidebar expanded" : "sidebar"}>
        <div className="wordmark">
          <HermesMark /> Hermes <span>研究工作台</span>
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
          aria-label="＋ 新建研究"
          disabled={busy}
          onClick={() => void newThread()}
        >
          <svg
            viewBox="0 0 20 20"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            aria-hidden="true"
          >
            <path d="M10 4.5v11M4.5 10h11" />
          </svg>
          新建研究
        </button>
        <Link
          href="/"
          className="workspace-library-link"
          aria-current={!hubOpen && !knowledgeOpen && !notesOpen && !stockSymbol ? "page" : undefined}
        >
          <svg
            viewBox="0 0 20 20"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M17 9.5a6.5 6.5 0 1 1-3.2-5.6M17 3v5h-5" />
          </svg>
          研究对话
        </Link>
        <Link
          href="/lab"
          className="workspace-library-link"
          aria-current={hubOpen ? "page" : undefined}
        >
          <svg
            viewBox="0 0 20 20"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M3 5.5A1.5 1.5 0 0 1 4.5 4h3l1.8 2H15.5A1.5 1.5 0 0 1 17 7.5v7A1.5 1.5 0 0 1 15.5 16h-11A1.5 1.5 0 0 1 3 14.5Z" />
            <path d="M3 8h14" />
          </svg>
          研究中心
        </Link>
        <Link
          href="/knowledge"
          className="workspace-library-link"
          aria-current={knowledgeOpen ? "page" : undefined}
        >
          <svg
            viewBox="0 0 20 20"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M4.5 3.5h7L16 8v8.5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-12a1 1 0 0 1 .5-1Z" />
            <path d="M11 3.5V8h4.5M7 11h6M7 14h4" />
          </svg>
          知识沉淀
        </Link>
        <Link
          href="/notes"
          className="workspace-library-link"
          aria-current={notesOpen ? "page" : undefined}
        >
          <svg
            viewBox="0 0 20 20"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M4.5 3.5h8A2.5 2.5 0 0 1 15 6v10.5H6A1.5 1.5 0 0 1 4.5 15Z" />
            <path d="M6.5 7h6M6.5 10h6M6.5 13h4" />
          </svg>
          个人笔记
        </Link>
        <div className="sidebar-label">
          最近研究 <span>{threads.length}</span>
        </div>
        <nav aria-label="研究记录">
          {threads.map((t) => {
            const active = selected?.id === t.id;
            const source =
              t.source === "group"
                ? "群研究"
                : t.channel === "feishu_private"
                  ? "飞书"
                  : "网页";
            return (
              <div
                key={t.id}
                className={active ? "thread-row active" : "thread-row"}
              >
                <button
                  type="button"
                  className="thread-open"
                  disabled={busy}
                  aria-current={active ? "page" : undefined}
                  onClick={() => open(t.id)}
                >
                  <span className="thread-title">{t.title}</span>
                  <span className="thread-meta">
                    <span className="thread-source">{source}</span>
                    <span aria-hidden="true">·</span>
                    <time dateTime={t.created_at}>
                      {new Date(t.created_at).toLocaleDateString("zh-CN", {
                        month: "2-digit",
                        day: "2-digit",
                      })}
                    </time>
                  </span>
                </button>
                <button
                  type="button"
                  className="thread-delete"
                  aria-label={`删除会话：${t.title}`}
                  disabled={busy || deleting}
                  onClick={() => {
                    setDeleteError("");
                    setDeleteTarget(t);
                  }}
                >
                  <svg
                    viewBox="0 0 20 20"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    aria-hidden="true"
                  >
                    <path d="M4.5 6h11M8 3.5h4M6.5 6l.7 10h5.6l.7-10M8.5 9v4.5M11.5 9v4.5" />
                  </svg>
                </button>
              </div>
            );
          })}
          {!threads.length && <p className="sidebar-empty">尚无研究记录</p>}
        </nav>
        <details className="watch-section">
          <summary>
            自选股 <span className="watch-count">{watchlist.length}</span>
          </summary>
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
                const added = String(new FormData(form).get("symbol"))
                  .trim()
                  .toUpperCase();
                form.reset();
                router.push(`/stocks/${encodeURIComponent(added)}`);
                setMenuOpen(false);
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
              placeholder="股票代码，如 AAPL"
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
                aria-current={stockSymbol === w.symbol ? "page" : undefined}
                disabled={busy}
                onClick={() => {
                  router.push(`/stocks/${encodeURIComponent(w.symbol)}`);
                  setMenuOpen(false);
                }}
                aria-label={`查看 ${w.symbol} 行情与新闻`}
              >
                <strong>{w.symbol}</strong>
                <span>走势 · 新闻 →</span>
              </button>
            ))}
            {watchlistLoad.status === "ready" && !watchlist.length && (
              <p className="watch-empty">添加关注的股票，随时开始研究。</p>
            )}
          </div>
        </details>
        <details className="sidebar-utilities">
          <summary>持仓与运行状态</summary>
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
        </details>
      </aside>
      <div className="main">
        <header className="topbar">
          <button
            className="topbar-menu"
            aria-label="打开导航"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen(!menuOpen)}
          >
            <svg
              viewBox="0 0 20 20"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              aria-hidden="true"
            >
              <path d="M3 6h14M3 10h14M3 14h14" />
            </svg>
          </button>
          <Link className="topbar-brand" href="/" aria-label="Hermes 首页">
            <HermesMark />
            <span>Hermes</span>
          </Link>
          <div className="topbar-heading">
            <h1 className="topbar-title">{pageTitle}</h1>
            <span className="topbar-mode">{pageMode}</span>
          </div>
          <div className="topbar-actions">
            <button
              className="canvas-toggle"
              aria-label={`研究产物，${artifacts.length} 项`}
              aria-expanded={
                !hubOpen && !knowledgeOpen && !notesOpen && !stockSymbol
                  ? canvasOpen
                  : undefined
              }
              aria-controls={
                !hubOpen && !knowledgeOpen && !notesOpen && !stockSymbol
                  ? "research-canvas"
                  : undefined
              }
              onClick={() => {
                router.push("/");
                setCanvasOpen((v) => !v);
              }}
            >
              <svg viewBox="0 0 20 20" aria-hidden="true">
                <rect
                  x="3"
                  y="4"
                  width="14"
                  height="12"
                  rx="1.6"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.5"
                />
                <path d="M10 4v12" stroke="currentColor" strokeWidth="1.5" />
              </svg>
              <span className="canvas-toggle-label">研究产物</span>
              <span className="canvas-toggle-count">{artifacts.length}</span>
            </button>
            <div className="account">
              <details className="account-menu">
                <summary aria-label="账户菜单">
                  <span className="account-avatar" aria-hidden="true">
                    {(user.email || "飞书用户").slice(0, 1).toUpperCase()}
                  </span>
                  <span className="account-email">
                    {user.email || "飞书用户"}
                  </span>
                  <svg
                    className="account-chevron"
                    viewBox="0 0 20 20"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.6"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    aria-hidden="true"
                  >
                    <path d="m5 8 5 5 5-5" />
                  </svg>
                </summary>
                <div className="account-popover">
                  <p className="account-identity">{user.email || "飞书用户"}</p>
                  <div className="account-section-label">飞书</div>
                  <FeishuLogin binding />
                  <button
                    className="account-logout"
                    onClick={() => void logout()}
                    disabled={loggingOut}
                  >
                    {loggingOut ? "正在退出…" : "退出登录"}
                  </button>
                </div>
              </details>
            </div>
          </div>
        </header>
        <main
          ref={deskRef}
          style={
            {
              "--chat-width": chatWidth ? `${chatWidth}px` : undefined,
            } as CSSProperties
          }
          className={`desk${canvasOpen ? " canvas-visible" : ""}${!hubOpen && !knowledgeOpen && !notesOpen && !stockSymbol ? " desk-split" : ""}`}
        >
          {!hubOpen && !knowledgeOpen && !notesOpen && !stockSymbol && (
            <div className="conversation-column">
              {submission ? (
                <section className="submission-view" aria-label="提交研究">
                  <div className="submission-message">{submission.input}</div>
                  <div className="submission-status" role="status">
                    <span className="entry-loading-orbit" aria-hidden="true" />
                    {submission.stage}
                  </div>
                  <p>请求确认后会自动开始研究，请勿重复发送。</p>
                </section>
              ) : selected ? (
                <Chat
                  key={selected.id}
                  thread={selected}
                  drafts={drafts.current}
                  portfolioRef={portfolioRef || undefined}
                  onChange={refresh}
                  onOpenCanvas={() => {
                    router.push("/");
                    showCanvas();
                  }}
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
                      aria-label="提问或研究"
                      placeholder="问点什么，或让我研究一只股票，例如：查一下特斯拉最近的走势"
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
                      <ComposerTools
                        disabled={busy || !!startRequest.current}
                        onSelect={(text) =>
                          setDraft((previous) =>
                            previous ? `${previous}\n${text}` : text,
                          )
                        }
                      />
                      <small className="composer-hint">
                        Enter 发送 · Shift + Enter 换行
                      </small>
                      <button
                        type="submit"
                        aria-label="发送研究"
                        disabled={busy || !draft.trim()}
                      >
                        {busy ? (
                          "…"
                        ) : (
                          <svg
                            viewBox="0 0 20 20"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="1.8"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            aria-hidden="true"
                          >
                            <path d="M10 15V5m0 0-4 4m4-4 4 4" />
                          </svg>
                        )}
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
                        aria-label={s.label}
                        disabled={busy || !!startRequest.current}
                        onClick={() => setDraft(s.prompt)}
                      >
                        <SuggestionIcon icon={s.icon} />
                        <span className="suggestion-text">
                          <strong>{s.label}</strong>
                          <small>{s.hint}</small>
                        </span>
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
          )}
          {!hubOpen && !knowledgeOpen && !notesOpen && !stockSymbol && (
            <>
              <div
                className="desk-divider"
                role="separator"
                aria-orientation="vertical"
                aria-label="调整对话与画布宽度"
                aria-valuemin={CHAT_MIN_WIDTH}
                aria-valuemax={Math.round(maxChatWidth())}
                aria-valuenow={chatWidth || undefined}
                aria-valuetext={
                  chatWidth ? `对话宽度 ${chatWidth} 像素` : "对话宽度自动"
                }
                tabIndex={0}
                onPointerDown={startChatResize}
                onKeyDown={resizeChatWithKeyboard}
                onDoubleClick={() => applyChatRatio(CHAT_DEFAULT_RATIO)}
              />
              <aside id="research-canvas" className="canvas-drawer">
                <Canvas onClose={() => setCanvasOpen(false)} />
              </aside>
            </>
          )}
          {user && stockSymbol && (
            <section className="workspace-hub" aria-label="股票详情">
              <StockDetail
                key={user.id + stockSymbol}
                symbol={stockSymbol}
                onAnalyze={(symbol) => {
                  newThread();
                  setDraft(
                    `请分析 ${symbol} 近期走势与相关新闻，展示价格趋势图，说明新闻对公司的潜在影响，标明数据日期与来源。`,
                  );
                }}
              />
            </section>
          )}
          {user && (hubVisited || hubOpen) && (
            <section
              className="workspace-hub"
              hidden={!hubOpen}
              aria-label="研究中心"
            >
              <ResearchHub key={user.id} embedded />
            </section>
          )}
          {user && knowledgeOpen && (
            <section
              className="workspace-hub workspace-hub-doc"
              aria-label="知识沉淀"
            >
              <KnowledgeBase key={user.id} />
            </section>
          )}
          {user && notesOpen && (
            <section
              className="workspace-hub workspace-hub-doc"
              aria-label="个人笔记"
            >
              <PersonalNotes key={user.id} />
            </section>
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
      {deleteTarget && (
        <div
          className="modal-backdrop"
          onClick={(e) => {
            if (e.target === e.currentTarget && !deleting) setDeleteTarget(null);
          }}
        >
          <section
            className="modal confirm-modal"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="delete-thread-title"
            aria-describedby="delete-thread-note"
          >
            <h2 id="delete-thread-title">删除这个会话？</h2>
            <p className="confirm-target">{deleteTarget.title}</p>
            <small id="delete-thread-note">
              会话会从「最近研究」移除；已保存的画布、研究结果与预测不会被删除。
            </small>
            {deleteError && (
              <p className="notice" role="alert">
                {deleteError}
              </p>
            )}
            <div className="confirm-actions">
              <button
                type="button"
                onClick={() => setDeleteTarget(null)}
                disabled={deleting}
              >
                取消
              </button>
              <button
                type="button"
                className="danger"
                onClick={() => void deleteThread()}
                disabled={deleting}
              >
                {deleting ? "正在删除…" : "删除会话"}
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
