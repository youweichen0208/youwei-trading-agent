"use client";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { api, message } from "../lib/client";

type Note = {
  id: string;
  title: string;
  body: string;
  tags: string[];
  symbol: string | null;
  pinned: boolean;
  useInAgent: boolean;
  createdAt: string;
  updatedAt: string;
};
type Draft = {
  id: string | null;
  title: string;
  body: string;
  tags: string;
  symbol: string;
  pinned: boolean;
  useInAgent: boolean;
};
const emptyDraft: Draft = {
  id: null,
  title: "",
  body: "",
  tags: "",
  symbol: "",
  pinned: false,
  useInAgent: true,
};
const parseTags = (value: string) => [
  ...new Set(
    value
      .split(/[,，]/)
      .map((tag) => tag.trim())
      .filter(Boolean),
  ),
];
const toDraft = (note: Note): Draft => ({
  id: note.id,
  title: note.title,
  body: note.body,
  tags: note.tags.join("，"),
  symbol: note.symbol ?? "",
  pinned: note.pinned,
  useInAgent: note.useInAgent,
});
/** Server order first; local mutation keeps it without a second round trip. */
const sortNotes = (rows: Note[]) =>
  [...rows].sort(
    (a, b) =>
      Number(b.pinned) - Number(a.pinned) ||
      b.updatedAt.localeCompare(a.updatedAt) ||
      a.id.localeCompare(b.id),
  );
const day = (value: string) =>
  new Date(value).toLocaleDateString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone: "Asia/Shanghai",
  });
const Plus = () => (
  <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
    <path d="M10 4.5v11M4.5 10h11" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
  </svg>
);
const Search = () => (
  <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
    <circle cx="9" cy="9" r="5.2" stroke="currentColor" strokeWidth="1.6" />
    <path d="m13 13 3.4 3.4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
  </svg>
);
const Pin = () => (
  <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
    <path
      d="M12.6 3.4 16.6 7.4l-2 .7-2.6 2.6.3 2.7-4.7-4.7 2.7.3 2.6-2.6Z"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinejoin="round"
    />
    <path d="m8.4 11.6-4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
  </svg>
);
const Spark = () => (
  <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
    <path
      d="M10 3.2l1.5 4.1 4.1 1.5-4.1 1.5L10 14.4 8.5 10.3 4.4 8.8l4.1-1.5Z"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinejoin="round"
    />
    <path d="M15.4 13.6l.6 1.6 1.6.6-1.6.6-.6 1.6-.6-1.6-1.6-.6 1.6-.6Z" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round" />
  </svg>
);

export function PersonalNotes() {
  const [notes, setNotes] = useState<Note[] | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const bodyRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    let active = true;
    api<Note[]>("personal-notes")
      .then((rows) => {
        if (active) setNotes(rows);
      })
      .catch((e) => {
        if (!active) return;
        setNotes([]);
        setError(message(e));
      });
    return () => {
      active = false;
    };
  }, []);
  const dirty = useMemo(() => {
    if (!draft) return false;
    if (!draft.id)
      return Boolean(
        draft.title || draft.body || draft.tags || draft.symbol,
      );
    const note = notes?.find((row) => row.id === draft.id);
    // The note is gone; keep the text so nothing typed is lost.
    if (!note) return true;
    return (
      draft.title !== note.title ||
      draft.body !== note.body ||
      parseTags(draft.tags).join("\n") !== note.tags.join("\n") ||
      draft.symbol !== (note.symbol ?? "") ||
      draft.pinned !== note.pinned ||
      draft.useInAgent !== note.useInAgent
    );
  }, [draft, notes]);
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return notes ?? [];
    return (notes ?? []).filter((note) =>
      [note.title, note.body, note.tags.join(" "), note.symbol ?? ""]
        .join("\n")
        .toLowerCase()
        .includes(needle),
    );
  }, [notes, query]);
  const set = (patch: Partial<Draft>) =>
    setDraft((current) => (current ? { ...current, ...patch } : current));
  // Guarded navigation: unsaved text is never dropped without a confirmation.
  const canLeave = () => !dirty || window.confirm("放弃未保存的修改？");
  const select = (note: Note) => {
    if (!canLeave()) return;
    setDraft(toDraft(note));
    setError("");
  };
  const start = () => {
    if (!canLeave()) return;
    setDraft({ ...emptyDraft });
    setError("");
  };
  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!draft) return;
    const title = draft.title.trim(),
      body = draft.body.trim();
    if (!title || !body) {
      setError("标题和正文不能为空");
      return;
    }
    const symbol = draft.symbol.trim().toUpperCase();
    const base = {
      title,
      body,
      tags: parseTags(draft.tags).slice(0, 10),
      pinned: draft.pinned,
      useInAgent: draft.useInAgent,
    };
    // Create omits an empty symbol; update sends null to clear it.
    const payload = draft.id
      ? { ...base, symbol: symbol || null }
      : { ...base, ...(symbol ? { symbol } : {}) };
    setSaving(true);
    try {
      const saved = draft.id
        ? await api<Note>(`personal-notes/${draft.id}`, "PUT", payload)
        : await api<Note>("personal-notes", "POST", payload);
      setNotes((current) =>
        sortNotes([
          ...(current ?? []).filter((row) => row.id !== saved.id),
          saved,
        ]),
      );
      setDraft(toDraft(saved));
      setError("");
    } catch (e) {
      setError(message(e));
    } finally {
      setSaving(false);
    }
  };
  const remove = async () => {
    if (!draft?.id) return;
    if (!window.confirm("删除这条笔记？此操作不可撤销。")) return;
    setSaving(true);
    try {
      await api(`personal-notes/${draft.id}`, "DELETE");
      const next = (notes ?? []).filter((row) => row.id !== draft.id);
      setNotes(next);
      setDraft(next[0] ? toDraft(next[0]) : null);
      setError("");
    } catch (e) {
      setError(message(e));
    } finally {
      setSaving(false);
    }
  };
  return (
    <section className="notes">
      {error && (
        <p className="notes-error" role="alert">
          {error}
        </p>
      )}
      <div className="notes-body">
        <aside className="notes-rail">
          <div className="notes-rail-head">
            <h2 className="notes-rail-title">
              笔记
              <span className="notes-count">{notes?.length ?? 0}</span>
            </h2>
            <button
              type="button"
              className="notes-new"
              aria-label="＋ 新建笔记"
              title="新建笔记"
              onClick={start}
            >
              <Plus />
            </button>
          </div>
          <div className="notes-rail-search">
            <Search />
            <input
              className="notes-search"
              aria-label="搜索笔记"
              placeholder="搜索笔记"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          <nav className="notes-list" aria-label="笔记列表">
            {notes === null ? (
              <p className="notes-loading" role="status">
                正在加载笔记…
              </p>
            ) : (
              visible.map((note) => (
                <button
                  key={note.id}
                  type="button"
                  className="notes-item"
                  aria-current={draft?.id === note.id ? "true" : undefined}
                  onClick={() => select(note)}
                >
                  <span className="notes-item-title">
                    {note.pinned && (
                      <span className="notes-item-pin" aria-hidden="true">
                        <Pin />
                      </span>
                    )}
                    {note.pinned ? "置顶 · " : ""}
                    {note.title}
                  </span>
                  <span className="notes-item-meta">
                    {day(note.updatedAt)} ·{" "}
                    {note.useInAgent ? "参考中" : "不参考"}
                    {note.symbol ? ` · ${note.symbol}` : ""}
                  </span>
                </button>
              ))
            )}
            {notes && !visible.length && (
              <p className="notes-loading">
                {query.trim() ? "没有匹配的笔记" : "还没有笔记"}
              </p>
            )}
          </nav>
          <p className="notes-hint">
            Hermes 会参考勾选的笔记（置顶优先，单条前 2000 字）；仅当前账号个人研究使用，不进入群研究
          </p>
        </aside>
        {draft ? (
          <form className="notes-doc" onSubmit={save}>
            <div className="notes-toolbar">
              <label className="notes-toggle">
                <input
                  type="checkbox"
                  checked={draft.pinned}
                  onChange={(event) => set({ pinned: event.target.checked })}
                />
                <Pin />
                置顶
              </label>
              <label className="notes-toggle">
                <input
                  type="checkbox"
                  checked={draft.useInAgent}
                  onChange={(event) =>
                    set({ useInAgent: event.target.checked })
                  }
                />
                <Spark />
                让 Hermes 在分析时参考
              </label>
              <div className="notes-tools">
                <button
                  type="button"
                  className="notes-delete"
                  disabled={!draft.id || saving}
                  onClick={() => void remove()}
                >
                  删除笔记
                </button>
                <button
                  type="submit"
                  className="notes-save"
                  disabled={!dirty || saving}
                >
                  {saving ? "正在保存…" : "保存笔记"}
                </button>
              </div>
            </div>
            <div
              className="notes-editor"
              // Clicking the blank page below the last line starts writing there.
              onClick={(event) => {
                if (event.target === event.currentTarget) bodyRef.current?.focus();
              }}
            >
              <input
                className="notes-title-input"
                type="text"
                aria-label="笔记标题"
                placeholder="无标题"
                maxLength={200}
                value={draft.title}
                onChange={(event) => set({ title: event.target.value })}
              />
              <textarea
                ref={bodyRef}
                className="notes-editor-body"
                aria-label="笔记正文"
                placeholder="写点什么：你的投资风格、偏好，或一次复盘的心得……"
                rows={6}
                maxLength={20000}
                value={draft.body}
                onChange={(event) => set({ body: event.target.value })}
              />
              <div className="notes-meta">
                <label className="notes-meta-field">
                  <span>股票</span>
                  <input
                    type="text"
                    aria-label="关注股票"
                    maxLength={15}
                    placeholder="NVDA"
                    value={draft.symbol}
                    onChange={(event) => set({ symbol: event.target.value })}
                  />
                </label>
                <label className="notes-meta-field">
                  <span>标签</span>
                  <input
                    type="text"
                    aria-label="笔记标签"
                    maxLength={310}
                    placeholder="估值，风险，行业"
                    value={draft.tags}
                    onChange={(event) => set({ tags: event.target.value })}
                  />
                </label>
              </div>
            </div>
          </form>
        ) : (
          notes && (
            <div className="notes-doc notes-doc-empty">
              <p className="notes-blank">
                {notes.length
                  ? "从左侧选一条笔记继续写，或新建一条。"
                  : "还没有笔记。写下你的投资风格、偏好或复盘心得，Hermes 会在分析时参考。"}
              </p>
              <button type="button" className="notes-new" onClick={start}>
                ＋ 新建笔记
              </button>
            </div>
          )
        )}
      </div>
    </section>
  );
}
