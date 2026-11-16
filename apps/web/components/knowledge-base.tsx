"use client";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
const lessonSections = ["经验结论", "适用条件", "失效条件", "验证进度"];
const day = (value: string | null | undefined) =>
  !value
    ? "—"
    : new Date(value).toLocaleDateString("zh-CN", {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        timeZone: "Asia/Shanghai",
      });

const shortDay = (value: string | null | undefined) =>
  !value
    ? "—"
    : new Date(value).toLocaleDateString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        timeZone: "Asia/Shanghai",
      });

type Entry = { key: string; fresh: boolean; title: string; meta: string };
const toEntry = (doc: Doc): Entry => ({
  key: doc.key,
  fresh: isRecent(doc.at),
  title:
    doc.kind === "lesson" ? doc.lesson.title : `${doc.summary.symbol} 研究摘要`,
  meta:
    doc.kind === "lesson"
      ? `${scopeName[doc.lesson.scope] ?? doc.lesson.scope} · ${
          lessonStatus[doc.lesson.status] ?? doc.lesson.status
        } · ${doc.lesson.horizon} 日 · ${shortDay(doc.at)}`
      : `${doc.summary.symbol} · ${shortDay(doc.summary.as_of)}`,
});

function FolderIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M3 5.6A1.6 1.6 0 0 1 4.6 4h2.9c.5 0 .9.2 1.2.6l.9 1.1h5.8A1.6 1.6 0 0 1 17 7.3v7.1A1.6 1.6 0 0 1 15.4 16H4.6A1.6 1.6 0 0 1 3 14.4Z" />
    </svg>
  );
}

function BookIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true" className="kb-book-icon">
      <path d="M3.2 4.4A1.4 1.4 0 0 1 4.6 3h3.2c.8 0 1.5.4 1.9 1.1l.3.6.3-.6c.4-.7 1.1-1.1 1.9-1.1h3.2a1.4 1.4 0 0 1 1.4 1.4v9.2a1.4 1.4 0 0 1-1.4 1.4h-3.6c-.6 0-1.1.2-1.5.6l-.3.3-.3-.3c-.4-.4-.9-.6-1.5-.6H4.6a1.4 1.4 0 0 1-1.4-1.4Z" />
      <path d="M10 5v10.5" />
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M10 4.6v10.8M4.6 10h10.8" />
    </svg>
  );
}

function OutlineIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true" className="kb-outline-icon">
      <path d="M4 6h9M4 10h9M4 14h6" />
    </svg>
  );
}

function TagIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M3.4 9.1V4.6c0-.7.5-1.2 1.2-1.2h4.5c.3 0 .6.1.8.3l5.5 5.5c.3.3.3.8 0 1.1l-4.4 4.4c-.3.3-.8.3-1.1 0L4.4 9.9a1.2 1.2 0 0 1-.3-.8Z" />
      <circle cx="6.8" cy="6.8" r="1.1" />
    </svg>
  );
}


function SearchIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <circle cx="9" cy="9" r="4.6" />
      <path d="m12.6 12.6 3.3 3.3" />
    </svg>
  );
}

function ChevronIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="m6 8 4 4 4-4" />
    </svg>
  );
}

function DocIcon({ kind }: { kind: "lesson" | "summary" }) {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true" className="kb-doc-icon">
      {kind === "lesson" ? (
        <>
          <path d="M6 3.5h6l3.5 3.5v9a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4.5a1 1 0 0 1 1-1Z" />
          <path d="M12 3.5V7h3.5M7.5 10.5h5M7.5 13h3.5" />
        </>
      ) : (
        <>
          <circle cx="10" cy="10" r="6.5" />
          <path d="M10 6.5v3.8l2.4 1.5" />
        </>
      )}
    </svg>
  );
}

function DocButton({
  active,
  kind,
  title,
  meta,
  fresh,
  nested,
  onSelect,
}: {
  active: boolean;
  kind: "lesson" | "summary";
  title: string;
  meta: string;
  fresh: boolean;
  nested?: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      className={nested ? "kb-doc kb-doc-sub" : "kb-doc"}
      aria-current={active ? "page" : undefined}
      onClick={onSelect}
    >
      <DocIcon kind={kind} />
      <span className="kb-doc-text">
        <span className="kb-doc-title">
          <span className="kb-doc-name">{title}</span>
          {fresh && <span className="kb-new">新</span>}
        </span>
        <span className="kb-doc-meta">{meta}</span>
      </span>
    </button>
  );
}

/**
 * Research summaries are plain text written in a document shape: `一、` section
 * headings, `1.` list items, blank-line paragraphs. Parse once into blocks so
 * the reader can render real headings instead of one wall of prose.
 */
type Block =
  | { kind: "heading"; text: string }
  | { kind: "list"; items: string[] }
  | { kind: "paragraph"; text: string };

const sectionHeading = /^(?:[一二三四五六七八九十]+、|[（(][一二三四五六七八九十]+[)）]|#+\s*)\s*\S/;
const listItem = /^(?:\d{1,2}[.、)）]|[（(]\d{1,2}[)）]|[-*·]\s)\s*\S/;

function summaryBlocks(text: string | null): Block[] {
  const lines = (text ?? "").split(/\r?\n/);
  const blocks: Block[] = [];
  let paragraph: string[] = [];
  let list: string[] = [];
  const flush = () => {
    if (list.length) {
      blocks.push({ kind: "list", items: list });
      list = [];
    }
    if (paragraph.length) {
      blocks.push({ kind: "paragraph", text: paragraph.join("") });
      paragraph = [];
    }
  };
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) {
      flush();
      continue;
    }
    if (sectionHeading.test(line)) {
      flush();
      blocks.push({
        kind: "heading",
        text: line.replace(/^#+\s*/, "").trim(),
      });
      continue;
    }
    if (listItem.test(line)) {
      if (paragraph.length) flush();
      list.push(line);
      continue;
    }
    if (list.length) flush();
    paragraph.push(line);
  }
  flush();
  return blocks;
}

function SummaryBody({ text }: { text: string | null }) {
  const blocks = summaryBlocks(text);
  if (!blocks.length) return <p className="kb-muted">暂无正文</p>;
  return (
    <>
      {blocks.map((block, index) =>
        block.kind === "heading" ? (
          <h3 key={index} id={`kb-b-${index}`} className="kb-h3">
            {block.text}
          </h3>
        ) : block.kind === "list" ? (
          <ol key={index} className="kb-list">
            {block.items.map((item, i) => (
              <li key={i}>{item.replace(/^(?:\d{1,2}[.、)）]|[（(]\d{1,2}[)）]|[-*·]\s)\s*/, "")}</li>
            ))}
          </ol>
        ) : (
          <p key={index}>{block.text}</p>
        ),
      )}
    </>
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
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [contentsOpen, setContentsOpen] = useState(false);
  const [treeOpen, setTreeOpen] = useState(false);

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
  // Opening a search collapses nothing: expand every folder so matches are visible.
  const wasSearching = useRef(false);
  useEffect(() => {
    if (needle && !wasSearching.current) setCollapsed({});
    wasSearching.current = Boolean(needle);
  }, [needle]);

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

  const lessonEntries = visible
    .filter(
      (doc): doc is Extract<Doc, { kind: "lesson" }> => doc.kind === "lesson",
    )
    .map(toEntry);
  // One folder per symbol; `docs` is newest-first so folders follow recency.
  const stockFolders: { symbol: string; entries: Entry[] }[] = [];
  let summaryCount = 0;
  for (const doc of visible) {
    if (doc.kind !== "summary") continue;
    summaryCount += 1;
    const folder = stockFolders.find((f) => f.symbol === doc.summary.symbol);
    if (folder) folder.entries.push(toEntry(doc));
    else
      stockFolders.push({
        symbol: doc.summary.symbol,
        entries: [toEntry(doc)],
      });
  }

  const selected = docs.find((doc) => doc.key === selectedKey) ?? null;
  const readerRef = useRef<HTMLElement | null>(null);
  const paperRef = useRef<HTMLDivElement | null>(null);
  const treeListRef = useRef<HTMLElement | null>(null);
  const [activeSection, setActiveSection] = useState("");

  const outline = useMemo(() => {
    if (!selected) return [];
    if (selected.kind === "lesson")
      return lessonSections.map((label, index) => ({
        id: `kb-l-${index}`,
        label,
      }));
    return summaryBlocks(selected.summary.text).flatMap((block, index) =>
      block.kind === "heading"
        ? [
            {
              id: `kb-b-${index}`,
              label: block.text.replace(/^[一二三四五六七八九十]+、\s*/, ""),
            },
          ]
        : [],
    );
  }, [selected]);

  // A new document starts at its first line, like any document reader.
  useEffect(() => {
    readerRef.current?.scrollTo({ top: 0 });
    // On phones the tree is a drawer; picking a document returns to the text.
    if (window.matchMedia("(max-width: 900px)").matches) setTreeOpen(false);
  }, [selectedKey]);

  useEffect(() => {
    treeListRef.current
      ?.querySelector('[aria-current="page"]')
      ?.scrollIntoView({ block: "nearest" });
  }, [selectedKey, collapsed, visible.length]);

  // Follow the reader's scroll so the outline marks where the reader is.
  useEffect(() => {
    const scroller = readerRef.current;
    const paper = paperRef.current;
    setActiveSection(outline[0]?.id ?? "");
    if (!scroller || !paper || !outline.length) return;
    const sync = () => {
      const line = scroller.getBoundingClientRect().top + 96;
      let current = outline[0]!.id;
      for (const item of outline) {
        const heading = paper.querySelector<HTMLElement>(`#${item.id}`);
        if (heading && heading.getBoundingClientRect().top <= line)
          current = item.id;
      }
      setActiveSection(current);
    };
    sync();
    scroller.addEventListener("scroll", sync, { passive: true });
    return () => scroller.removeEventListener("scroll", sync);
  }, [outline]);

  const readerHead = selected ? (
    selected.kind === "lesson" ? (
      <>
        <nav className="kb-crumb" aria-label="文档位置">
          <span>知识库</span>
          <span aria-hidden="true">/</span>
          <span>策略经验</span>
        </nav>
        <h1 className="kb-title">
          {selected.lesson.title}
          {isRecent(selected.at) && <span className="kb-new">新</span>}
        </h1>
        <p className="kb-byline">
          <span
            className="kb-status"
            data-status={statusTone[selected.lesson.status] ?? "candidate"}
          >
            {lessonStatus[selected.lesson.status] ?? selected.lesson.status}
          </span>
          <span>
            {scopeName[selected.lesson.scope] ?? selected.lesson.scope} ·{" "}
            {selected.lesson.horizon} 个交易日
          </span>
          {selected.lesson.symbol && <span>{selected.lesson.symbol}</span>}
          <span>更新于 {day(selected.lesson.created_at)}</span>
        </p>
      </>
    ) : (
      <>
        <nav className="kb-crumb" aria-label="文档位置">
          <span>知识库</span>
          <span aria-hidden="true">/</span>
          <span>个股研究</span>
          <span aria-hidden="true">/</span>
          <span>{selected.summary.symbol}</span>
        </nav>
        <h1 className="kb-title">
          {selected.summary.symbol} 研究摘要
          {isRecent(selected.at) && <span className="kb-new">新</span>}
        </h1>
        <p className="kb-byline">
          <span>研究日期 {day(selected.summary.as_of)}</span>
          <span>保存于 {day(selected.summary.created_at)}</span>
        </p>
      </>
    )
  ) : null;

  return (
    <div className="kb">
      <header className="kb-bar">
        <div className="kb-bar-name">
          <FolderIcon />
          <span>研究知识库</span>
          <span className="kb-bar-sep" aria-hidden="true">
            /
          </span>
          <span className="kb-bar-doc">
            {selected
              ? selected.kind === "lesson"
                ? selected.lesson.title
                : `${selected.summary.symbol} 研究摘要`
              : "尚未选择文档"}
          </span>
        </div>
        <div className="kb-bar-actions">
          <button
            type="button"
            className="kb-bar-tree"
            aria-expanded={treeOpen}
            aria-controls="kb-tree"
            onClick={() => setTreeOpen((open) => !open)}
          >
            <OutlineIcon />
            目录
          </button>
          <span className="kb-bar-count">
            {stats.lessons} 经验 · {stats.summaries} 摘要
          </span>
          <button
            type="button"
            className="kb-btn"
            onClick={() => void load()}
            disabled={loading}
          >
            {loading ? "加载中…" : "刷新"}
          </button>
        </div>
      </header>

      {error && data && !unauthorized && (
        <p className="kb-alert" role="alert">
          {error}
          <button type="button" className="kb-btn" onClick={() => void load()}>
            重试
          </button>
        </p>
      )}

      {unauthorized ? (
        <div className="kb-state" role="alert">
          <h2>登录已失效</h2>
          <p>{error}</p>
          <Link href="/">返回登录 ↗</Link>
        </div>
      ) : error && !data ? (
        <div className="kb-state" role="alert">
          <h2>知识沉淀加载失败</h2>
          <p>{error}</p>
          <button type="button" className="kb-btn" onClick={() => void load()}>
            重试
          </button>
        </div>
      ) : !data ? (
        <p className="kb-loading" role="status">
          正在加载知识沉淀…
        </p>
      ) : (
        <div className="kb-body">
          <a className="kb-skip" href="#kb-paper">
            跳到正文
          </a>
          <aside
            id="kb-tree"
            className={treeOpen ? "kb-tree kb-tree-open" : "kb-tree"}
            aria-label="知识库目录"
          >
            <div className="kb-tree-head">
              <span className="kb-tree-brand">
                <BookIcon />
                <span>研究知识库</span>
              </span>
              <Link
                className="kb-tree-new"
                href="/"
                aria-label="新建研究"
                title="新建研究"
              >
                <PlusIcon />
              </Link>
            </div>
            <div className="kb-tree-top">
              <label className="kb-search">
                <SearchIcon />
                <input
                  aria-label="搜索知识"
                  placeholder="搜索标题、股票或结论"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
              </label>
              <div className="kb-seg" role="group" aria-label="时间范围">
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
                  近 7 天
                </button>
              </div>
            </div>

            <nav className="kb-tree-list" aria-label="知识文档" ref={treeListRef}>
              <section>
                <button
                  type="button"
                  className="kb-folder"
                  aria-expanded={!collapsed.lesson}
                  onClick={() =>
                    setCollapsed((prev) => ({
                      ...prev,
                      lesson: !prev.lesson,
                    }))
                  }
                >
                  <ChevronIcon />
                  <span className="kb-folder-icon">
                    <FolderIcon />
                  </span>
                  <span className="kb-folder-name">策略经验</span>
                  <span className="kb-folder-count">
                    {lessonEntries.length}
                  </span>
                </button>
                {!collapsed.lesson &&
                  lessonEntries.map((entry) => (
                    <DocButton
                      key={entry.key}
                      kind="lesson"
                      active={entry.key === selectedKey}
                      title={entry.title}
                      meta={entry.meta}
                      fresh={entry.fresh}
                      onSelect={() => setSelectedKey(entry.key)}
                    />
                  ))}
              </section>

              <section>
                <button
                  type="button"
                  className="kb-folder"
                  aria-expanded={!collapsed.summary}
                  onClick={() =>
                    setCollapsed((prev) => ({
                      ...prev,
                      summary: !prev.summary,
                    }))
                  }
                >
                  <ChevronIcon />
                  <span className="kb-folder-icon">
                    <FolderIcon />
                  </span>
                  <span className="kb-folder-name">个股研究</span>
                  <span className="kb-folder-count">{summaryCount}</span>
                </button>
                {!collapsed.summary &&
                  stockFolders.map((folder) => {
                    const branchKey = `summary:${folder.symbol}`;
                    const open = !collapsed[branchKey];
                    return (
                      <div className="kb-branch" key={folder.symbol}>
                        <button
                          type="button"
                          className="kb-folder kb-folder-sub"
                          aria-expanded={open}
                          onClick={() =>
                            setCollapsed((prev) => ({
                              ...prev,
                              [branchKey]: open,
                            }))
                          }
                        >
                          <ChevronIcon />
                          <span className="kb-folder-icon">
                            <TagIcon />
                          </span>
                          <span className="kb-folder-name">
                            {folder.symbol}
                          </span>
                          <span className="kb-folder-count">
                            {folder.entries.length}
                          </span>
                        </button>
                        {open &&
                          folder.entries.map((entry) => (
                            <DocButton
                              key={entry.key}
                              kind="summary"
                              nested
                              active={entry.key === selectedKey}
                              title={entry.title}
                              meta={entry.meta}
                              fresh={entry.fresh}
                              onSelect={() => setSelectedKey(entry.key)}
                            />
                          ))}
                      </div>
                    );
                  })}
              </section>

              {visible.length === 0 && (
                <p className="kb-tree-empty">
                  {docs.length === 0
                    ? "知识库还是空的"
                    : onlyNew
                      ? "近 7 天暂无新增"
                      : "没有匹配的知识"}
                </p>
              )}
            </nav>

            <dl className="kb-facet">
              <div>
                <dt>策略经验</dt>
                <dd>{stats.lessons}</dd>
              </div>
              <div>
                <dt>研究摘要</dt>
                <dd>{stats.summaries}</dd>
              </div>
              <div>
                <dt>覆盖股票</dt>
                <dd>{stats.stocks}</dd>
              </div>
              <div>
                <dt>近 7 天新增</dt>
                <dd>{stats.fresh}</dd>
              </div>
            </dl>
            <p className="kb-tree-foot">只读 · 自动归档</p>
          </aside>

          <article className="kb-reader" ref={readerRef}>
            {docs.length === 0 ? (
              <div className="kb-state kb-state-inline">
                <h2>还没有沉淀知识</h2>
                <p>
                  完成一次股票研究后，研究摘要与策略经验会自动归档到这里。
                </p>
                <div className="kb-state-links">
                  <Link href="/">开始研究 ↗</Link>
                  <Link href="/lab">进入研究中心 ↗</Link>
                </div>
              </div>
            ) : (
              <>
                <div className="kb-reader-head">{readerHead}</div>
                <div className="kb-paper" id="kb-paper" ref={paperRef}>
                  {selected?.kind === "lesson" ? (
                    <>
                      <h2 id="kb-l-0">经验结论</h2>
                      <p>{selected.lesson.statement}</p>
                      <h2 id="kb-l-1">适用条件</h2>
                      <p>{selected.lesson.applicability}</p>
                      <h2 id="kb-l-2">失效条件</h2>
                      <p>{selected.lesson.invalidation}</p>
                      <h2 id="kb-l-3">验证进度</h2>
                      <p className="kb-progress">
                        <strong>{selected.lesson.trial_count}</strong>
                        <span>/ 30 组独立事件</span>
                      </p>
                      <div
                        className="kb-meter"
                        role="img"
                        aria-label={`已完成 ${selected.lesson.trial_count} / 30 组独立事件`}
                      >
                        <span
                          style={{
                            width: `${Math.min(100, (selected.lesson.trial_count / 30) * 100)}%`,
                          }}
                        />
                      </div>
                      <p className="kb-meta">
                        事件类型：{selected.lesson.event_type}
                        <span className="kb-meta-dot" aria-hidden="true">
                          ·
                        </span>
                        {selected.lesson.last_batch
                          ? `最近批次：${
                              selected.lesson.last_batch.passes
                                ? "达到采用门槛"
                                : "继续观察"
                            }`
                          : "尚无完整验证批次。"}
                      </p>
                    </>
                  ) : selected?.kind === "summary" ? (
                    <SummaryBody text={selected.summary.text} />
                  ) : null}
                </div>
                <footer className="kb-foot">
                  <span>只读视图 · 来源：保存的研究摘要与经验版本</span>
                  {selected && (
                    <Link
                      href={
                        selected.kind === "lesson"
                          ? `/stocks/${selected.lesson.symbol ?? ""}`
                          : `/stocks/${selected.summary.symbol}`
                      }
                    >
                      打开{" "}
                      {selected.kind === "lesson"
                        ? selected.lesson.symbol
                        : selected.summary.symbol}{" "}
                      个股研究 ↗
                    </Link>
                  )}
                </footer>
              </>
            )}
          </article>

          <aside className="kb-outline" aria-label="文档大纲">
            <div className="kb-outline-head">
              <span className="kb-outline-title">
                大纲
                <OutlineIcon />
              </span>
            </div>
            <nav aria-label="跳到章节">
              {outline.length === 0 && (
                <p className="kb-outline-empty">本文暂无分节</p>
              )}
              <ol>
                {outline.map((item) => (
                  <li key={item.id}>
                    <a
                      href={`#${item.id}`}
                      title={item.label}
                      aria-current={
                        activeSection === item.id ? "location" : undefined
                      }
                      onClick={(event) => {
                        event.preventDefault();
                        paperRef.current
                          ?.querySelector<HTMLElement>(`#${item.id}`)
                          ?.scrollIntoView({
                            block: "start",
                            behavior: window.matchMedia(
                              "(prefers-reduced-motion: reduce)",
                            ).matches
                              ? "auto"
                              : "smooth",
                          });
                        setActiveSection(item.id);
                      }}
                    >
                      {item.label}
                    </a>
                  </li>
                ))}
              </ol>
            </nav>
            <div className="kb-outline-sep" />
            <button
              type="button"
              className="kb-btn kb-btn-block"
              aria-expanded={contentsOpen}
              onClick={() => setContentsOpen((v) => !v)}
            >
              文档信息
            </button>
            {contentsOpen && selected && (
              <dl className="kb-info">
                <div>
                  <dt>类型</dt>
                  <dd>
                    {selected.kind === "lesson" ? "策略经验" : "个股研究"}
                  </dd>
                </div>
                <div>
                  <dt>保存于</dt>
                  <dd>{day(selected.at)}</dd>
                </div>
                {selected.kind === "lesson" && (
                  <div>
                    <dt>验证样本</dt>
                    <dd>{selected.lesson.trial_count} / 30</dd>
                  </div>
                )}
              </dl>
            )}
          </aside>
        </div>
      )}
    </div>
  );
}
