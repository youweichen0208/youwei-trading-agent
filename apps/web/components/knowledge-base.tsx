"use client";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, message } from "../lib/client";

type Lesson = {
  id: string;
  title: string;
  scope: "company" | "industry" | "general";
  status: string;
  created_at: string;
  horizon: number;
  symbol: string | null;
  event_type: string;
  statement: string;
  applicability: string;
  invalidation: string;
  trial_count: number;
  last_batch: { passes?: boolean } | null;
};
type Summary = {
  id: string;
  symbol: string;
  as_of: string;
  created_at: string;
  text: string | null;
};
type Knowledge = { lessons: Lesson[]; summaries: Summary[] };
type Doc =
  | { key: `lesson:${string}`; kind: "lesson"; at: string; lesson: Lesson }
  | { key: `summary:${string}`; kind: "summary"; at: string; summary: Summary };

const NEW_WINDOW_MS = 7 * 24 * 60 * 60 * 1000;
const isRecent = (at: string) =>
  Date.now() - new Date(at).getTime() <= NEW_WINDOW_MS;
const lessonStatus: Record<string, string> = {
  candidate: "候选经验",
  shadow: "影子试用",
  trial: "影子试用",
  adopted: "采用中",
  active: "采用中",
  paused: "已暂停",
  retired: "已停用",
  disabled: "已停用",
};
const statusTone: Record<string, string> = {
  candidate: "candidate",
  shadow: "shadow",
  trial: "shadow",
  adopted: "active",
  active: "active",
  paused: "shadow",
  retired: "retired",
  disabled: "retired",
};
const scopeName: Record<string, string> = {
  company: "公司",
  industry: "行业",
  general: "通用",
};
const day = (value: string | null | undefined) =>
  !value
    ? "—"
    : new Date(value).toLocaleDateString("zh-CN", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        timeZone: "Asia/Shanghai",
      });

function FolderIcon() {
  return (
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
  );
}

function SearchIcon() {
  return (
    <svg
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <circle cx="9" cy="9" r="4.5" />
      <path d="M12.5 12.5 16 16" />
    </svg>
  );
}

function DocButton({
  active,
  title,
  meta,
  fresh,
  onSelect,
}: {
  active: boolean;
  title: string;
  meta: string;
  fresh: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      className="knowledge-doc"
      aria-current={active ? "page" : undefined}
      onClick={onSelect}
    >
      <span className="knowledge-doc-title">
        {title}
        {fresh && <span className="knowledge-new">新</span>}
      </span>
      <span className="knowledge-doc-meta">{meta}</span>
    </button>
  );
}

export function KnowledgeBase() {
  const [data, setData] = useState<Knowledge | null>(null);
  const [error, setError] = useState("");
  const [unauthorized, setUnauthorized] = useState(false);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [onlyNew, setOnlyNew] = useState(false);
  const [selectedKey, setSelectedKey] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await api<Knowledge>("knowledge"));
      setError("");
      setUnauthorized(false);
    } catch (e) {
      const text = e instanceof Error ? e.message : "";
      setUnauthorized(text === "unauthorized");
      setError(message(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const next = await api<Knowledge>("knowledge");
        if (!active) return;
        setData(next);
        setError("");
        setUnauthorized(false);
      } catch (e) {
        if (!active) return;
        const text = e instanceof Error ? e.message : "";
        setUnauthorized(text === "unauthorized");
        setError(message(e));
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  const docs = useMemo<Doc[]>(() => {
    const lessons: Doc[] = (data?.lessons ?? []).map((lesson) => ({
      key: `lesson:${lesson.id}`,
      kind: "lesson",
      at: lesson.created_at,
      lesson,
    }));
    const summaries: Doc[] = (data?.summaries ?? []).map((summary) => ({
      key: `summary:${summary.id}`,
      kind: "summary",
      at: summary.created_at,
      summary,
    }));
    return [...lessons, ...summaries].sort(
      (a, b) => new Date(b.at).getTime() - new Date(a.at).getTime(),
    );
  }, [data]);

  useEffect(() => {
    if (docs.length && !docs.some((d) => d.key === selectedKey))
      setSelectedKey(docs[0]!.key);
  }, [docs, selectedKey]);

  const stats = useMemo(() => {
    const summaries = data?.summaries ?? [];
    return {
      lessons: data?.lessons.length ?? 0,
      fresh: docs.filter((d) => isRecent(d.at)).length,
      summaries: summaries.length,
      stocks: new Set(summaries.map((s) => s.symbol)).size,
    };
  }, [data, docs]);

  const needle = query.trim().toLowerCase();
  const visible = docs.filter((doc) => {
    if (onlyNew && !isRecent(doc.at)) return false;
    if (!needle) return true;
    const haystack =
      doc.kind === "lesson"
        ? [
            doc.lesson.title,
            doc.lesson.symbol,
            doc.lesson.statement,
            doc.lesson.applicability,
            doc.lesson.invalidation,
          ]
        : [doc.summary.symbol, doc.summary.text];
    return haystack
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(needle));
  });
  const groups = (["lesson", "summary"] as const).map((kind) => ({
    kind,
    label: kind === "lesson" ? "策略经验" : "个股研究",
    entries: visible
      .filter((doc) => doc.kind === kind)
      .map((doc) => ({
        key: doc.key,
        fresh: isRecent(doc.at),
        title:
          doc.kind === "lesson"
            ? doc.lesson.title
            : `${doc.summary.symbol} 研究摘要`,
        meta:
          doc.kind === "lesson"
            ? `${scopeName[doc.lesson.scope] ?? doc.lesson.scope} · ${
                lessonStatus[doc.lesson.status] ?? doc.lesson.status
              } · ${doc.lesson.horizon} 个交易日 · ${day(doc.at)}`
            : `${doc.summary.symbol} · ${day(doc.summary.as_of)}`,
      })),
  }));
  const selected = docs.find((doc) => doc.key === selectedKey) ?? null;

  return (
    <div className="knowledge-surface">
      <header className="knowledge-head">
        <div>
          <p className="knowledge-kicker">研究与预测 · 知识沉淀</p>
          <h1>让每一次判断，沉淀为下一次的依据。</h1>
          <p>策略经验与个股研究自动归档；近 7 天新增的内容标记为「新」。</p>
        </div>
        <button
          type="button"
          className="knowledge-refresh"
          onClick={() => void load()}
          disabled={loading}
        >
          {loading ? "正在加载…" : "刷新"}
        </button>
      </header>

      <div className="knowledge-summary">
        <div>
          <small>策略经验</small>
          <strong>{stats.lessons}</strong>
        </div>
        <div>
          <small>近 7 天新增</small>
          <strong>{stats.fresh}</strong>
        </div>
        <div>
          <small>研究摘要</small>
          <strong>{stats.summaries}</strong>
        </div>
        <div>
          <small>覆盖股票</small>
          <strong>{stats.stocks}</strong>
        </div>
      </div>

      {unauthorized ? (
        <div className="knowledge-empty" role="alert">
          <h2>登录已失效</h2>
          <p>{error}</p>
          <Link href="/">返回登录 ↗</Link>
        </div>
      ) : error && !data ? (
        <div className="knowledge-empty" role="alert">
          <h2>知识沉淀加载失败</h2>
          <p>{error}</p>
          <button
            type="button"
            className="knowledge-refresh"
            onClick={() => void load()}
          >
            重试
          </button>
        </div>
      ) : !data ? (
        <p className="knowledge-loading" role="status">
          正在加载知识沉淀…
        </p>
      ) : docs.length === 0 ? (
        <div className="knowledge-empty">
          <h2>还没有沉淀知识</h2>
          <p>完成一次股票研究后，研究摘要与策略经验会自动归档到这里。</p>
          <div className="knowledge-empty-links">
            <Link href="/">开始研究 ↗</Link>
            <Link href="/lab">进入研究中心 ↗</Link>
          </div>
        </div>
      ) : (
        <div className="knowledge-layout">
          <aside className="knowledge-tree">
            <div className="knowledge-tree-head">
              <span className="knowledge-tree-icon">
                <FolderIcon />
              </span>
              <span>
                <strong>研究知识库</strong>
                <small>策略与个股</small>
              </span>
            </div>
            <label className="knowledge-search">
              <SearchIcon />
              <input
                aria-label="搜索知识"
                placeholder="搜索标题、股票或结论"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
            <div className="knowledge-filter">
              <button
                type="button"
                aria-pressed={!onlyNew}
                onClick={() => setOnlyNew(false)}
              >
                全部
              </button>
              <button
                type="button"
                aria-pressed={onlyNew}
                onClick={() => setOnlyNew(true)}
              >
                新增
              </button>
            </div>
            <nav aria-label="知识文档">
              {groups.map(
                (group) =>
                  group.entries.length > 0 && (
                    <section key={group.kind}>
                      <div className="knowledge-group">
                        {group.label} <span>{group.entries.length}</span>
                      </div>
                      {group.entries.map((entry) => (
                        <DocButton
                          key={entry.key}
                          active={entry.key === selectedKey}
                          title={entry.title}
                          meta={entry.meta}
                          fresh={entry.fresh}
                          onSelect={() => setSelectedKey(entry.key)}
                        />
                      ))}
                    </section>
                  ),
              )}
              {visible.length === 0 && (
                <p className="knowledge-tree-empty">
                  {onlyNew ? "近 7 天暂无新增" : "没有匹配的知识"}
                </p>
              )}
            </nav>
            <p className="knowledge-tree-foot">
              只读视图 · 来源：保存的研究摘要与经验版本
            </p>
          </aside>

          <article className="knowledge-reader">
            {selected?.kind === "lesson"
              ? (() => {
                  const lesson = selected.lesson;
                  const fresh = isRecent(selected.at);
                  const progress = [
                    `${scopeName[lesson.scope] ?? lesson.scope}`,
                    `${lesson.horizon} 个交易日`,
                    lesson.symbol,
                    `更新于 ${day(lesson.created_at)}`,
                  ].filter(Boolean);
                  return (
                    <>
                      <nav className="knowledge-breadcrumb">
                        知识库 <span>/</span> 策略经验
                      </nav>
                      <h1>
                        {lesson.title}
                        {fresh && <span className="knowledge-new">新</span>}
                      </h1>
                      <p className="knowledge-meta">
                        <span
                          className="knowledge-status"
                          data-status={statusTone[lesson.status] ?? "candidate"}
                        >
                          {lessonStatus[lesson.status] ?? lesson.status}
                        </span>
                        {progress.map((entry) => (
                          <span key={entry}>{entry}</span>
                        ))}
                      </p>
                      <h2>经验结论</h2>
                      <p>{lesson.statement}</p>
                      <h2>适用条件</h2>
                      <p>{lesson.applicability}</p>
                      <h2>失效条件</h2>
                      <p>{lesson.invalidation}</p>
                      <h2>验证进度</h2>
                      <p className="knowledge-progress">
                        <strong>{lesson.trial_count}</strong> / 30 组独立事件
                      </p>
                      <p className="knowledge-meta">
                        <span>事件类型：{lesson.event_type}</span>
                        <span>
                          {lesson.last_batch
                            ? `最近批次：${
                                lesson.last_batch.passes
                                  ? "达到采用门槛"
                                  : "继续观察"
                              }`
                            : "尚无完整验证批次。"}
                        </span>
                      </p>
                      <footer className="knowledge-footer">
                        <span>只读视图 · 来源：保存的研究摘要与经验版本</span>
                        {lesson.scope === "company" && lesson.symbol && (
                          <Link href={`/stocks/${lesson.symbol}`}>
                            打开 {lesson.symbol} 个股研究 ↗
                          </Link>
                        )}
                      </footer>
                    </>
                  );
                })()
              : selected?.kind === "summary"
                ? (() => {
                    const summary = selected.summary;
                    const blocks = (summary.text ?? "")
                      .split(/\n{2,}/)
                      .map((block) => block.trim())
                      .filter(Boolean);
                    return (
                      <>
                        <nav className="knowledge-breadcrumb">
                          知识库 <span>/</span> 个股研究 <span>/</span>{" "}
                          {summary.symbol}
                        </nav>
                        <h1>
                          {summary.symbol} 研究摘要
                          {isRecent(selected.at) && <span className="knowledge-new">新</span>}
                        </h1>
                        <p className="knowledge-meta">
                          <span>
                            研究日期 {day(summary.as_of)} · 保存于{" "}
                            {day(summary.created_at)}
                          </span>
                        </p>
                        <div className="knowledge-body">
                          {blocks.length ? (
                            blocks.map((block, index) => (
                              <p key={index}>{block}</p>
                            ))
                          ) : (
                            <p className="knowledge-muted">暂无正文</p>
                          )}
                        </div>
                        <footer className="knowledge-footer">
                          <span>只读视图 · 来源：保存的研究摘要与经验版本</span>
                          <Link href={`/stocks/${summary.symbol}`}>
                            打开 {summary.symbol} 个股研究 ↗
                          </Link>
                        </footer>
                      </>
                    );
                  })()
                : null}
          </article>
        </div>
      )}
    </div>
  );
}
