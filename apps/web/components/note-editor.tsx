"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { EditorContent, useEditor, type Editor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import TaskList from "@tiptap/extension-task-list";
import TaskItem from "@tiptap/extension-task-item";
import Placeholder from "@tiptap/extension-placeholder";
import Image from "@tiptap/extension-image";
import TextAlign from "@tiptap/extension-text-align";
import {
  BackgroundColor,
  Color,
  FontSize,
  TextStyle,
} from "@tiptap/extension-text-style";
import {
  Table,
  TableCell,
  TableHeader,
  TableRow,
} from "@tiptap/extension-table";

type Props = {
  initialHtml: string;
  placeholder: string;
  onChange: (html: string) => void;
  onReady: (editor: Editor | null) => void;
};

type Block = {
  id: string;
  label: string;
  hint: string;
  run: (editor: Editor, range: { from: number; to: number }) => void;
};

const askUrl = (message: string, initial = "https://") =>
  window.prompt(message, initial);

const blocks: Block[] = [
  {
    id: "h1",
    label: "一级标题",
    hint: "H1",
    run: (e, r) => e.chain().focus().deleteRange(r).setHeading({ level: 1 }).run(),
  },
  {
    id: "h2",
    label: "二级标题",
    hint: "H2",
    run: (e, r) => e.chain().focus().deleteRange(r).setHeading({ level: 2 }).run(),
  },
  {
    id: "h3",
    label: "三级标题",
    hint: "H3",
    run: (e, r) => e.chain().focus().deleteRange(r).setHeading({ level: 3 }).run(),
  },
  {
    id: "bullet",
    label: "无序列表",
    hint: "•",
    run: (e, r) => e.chain().focus().deleteRange(r).toggleBulletList().run(),
  },
  {
    id: "ordered",
    label: "有序列表",
    hint: "1.",
    run: (e, r) => e.chain().focus().deleteRange(r).toggleOrderedList().run(),
  },
  {
    id: "task",
    label: "任务列表",
    hint: "[ ]",
    run: (e, r) => e.chain().focus().deleteRange(r).toggleTaskList().run(),
  },
  {
    id: "quote",
    label: "引用",
    hint: "\u201c",
    run: (e, r) => e.chain().focus().deleteRange(r).toggleBlockquote().run(),
  },
  {
    id: "code",
    label: "代码块",
    hint: "</>",
    run: (e, r) => e.chain().focus().deleteRange(r).toggleCodeBlock().run(),
  },
  {
    id: "divider",
    label: "分割线",
    hint: "—",
    run: (e, r) => e.chain().focus().deleteRange(r).setHorizontalRule().run(),
  },
  {
    id: "table",
    label: "表格",
    hint: "3×3",
    run: (e, r) =>
      e
        .chain()
        .focus()
        .deleteRange(r)
        .insertTable({ rows: 3, cols: 3, withHeaderRow: true })
        .run(),
  },
  {
    id: "image",
    label: "图片链接",
    hint: "img",
    run: (e, r) => {
      const src = askUrl("图片地址");
      const chain = e.chain().focus().deleteRange(r);
      if (src) chain.setImage({ src }).run();
      else chain.run();
    },
  },
  {
    id: "link",
    label: "链接",
    hint: "url",
    run: (editor, range) => {
      const href = askUrl("链接地址");
      if (!href) return editor.chain().focus().deleteRange(range).run();
      return editor
        .chain()
        .focus()
        .deleteRange(range)
        .insertContent({
          type: "text",
          text: href,
          marks: [{ type: "link", attrs: { href } }],
        })
        .run();
    },
  },
];

const textColors = [
  { name: "默认", value: null },
  { name: "深灰", value: "var(--text-secondary)" },
  { name: "品牌绿", value: "var(--brand-text)" },
  { name: "警告橙", value: "var(--warning)" },
  { name: "失败红", value: "var(--danger)" },
  { name: "成功绿", value: "var(--success)" },
];
const highlightColors = [
  { name: "无", value: null },
  { name: "浅灰", value: "var(--n100)" },
  { name: "品牌浅绿", value: "var(--brand-soft)" },
  { name: "浅橙", value: "var(--warning-soft)" },
  { name: "浅红", value: "var(--danger-soft)" },
  { name: "浅绿", value: "var(--success-soft)" },
];

/** Document body: one ProseMirror page, toolbar above it, slash menu at the caret. */
export function NoteEditor({
  initialHtml,
  placeholder,
  onChange,
  onReady,
}: Props) {
  const [menu, setMenu] = useState<{
    query: string;
    from: number;
    to: number;
    top: number;
    left: number;
    index: number;
  } | null>(null);
  const menuRef = useRef(menu);
  menuRef.current = menu;
  const options = useMemo(() => {
    const query = menu?.query.trim();
    if (!query) return blocks;
    return blocks.filter((block) =>
      `${block.label}${block.hint}`.toLowerCase().includes(query.toLowerCase()),
    );
  }, [menu?.query]);
  const optionsRef = useRef(options);
  optionsRef.current = options;
  const editorRef = useRef<Editor | null>(null);

  const insert = (block: Block) => {
    const instance = editorRef.current;
    const current = menuRef.current;
    if (!instance || !current) return;
    setMenu(null);
    block.run(instance, { from: current.from, to: current.to });
  };

  const editor = useEditor({
    immediatelyRender: false,
    extensions: [
      StarterKit.configure({
        heading: { levels: [1, 2, 3] },
        link: { openOnClick: false, autolink: true },
      }),
      TextStyle,
      Color,
      BackgroundColor,
      FontSize,
      TextAlign.configure({ types: ["heading", "paragraph"] }),
      Image.configure({ inline: false, allowBase64: false }),
      TaskList,
      TaskItem.configure({ nested: true }),
      Placeholder.configure({ placeholder }),
      Table.configure({ resizable: false }),
      TableRow,
      TableHeader,
      TableCell,
    ],
    content: initialHtml || "<p></p>",
    editorProps: {
      attributes: {
        class: "note-page",
        "aria-label": "笔记正文",
        role: "textbox",
        "aria-multiline": "true",
      },
      handleKeyDown: (_view, event) => {
        const current = menuRef.current;
        if (!current) return false;
        const list = optionsRef.current;
        if (event.key === "Escape") {
          setMenu(null);
          return true;
        }
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          const step = event.key === "ArrowDown" ? 1 : -1;
          setMenu({
            ...current,
            index: (current.index + step + list.length) % Math.max(list.length, 1),
          });
          return true;
        }
        if (event.key === "Enter" && list.length) {
          event.preventDefault();
          insert(list[Math.min(current.index, list.length - 1)]!);
          return true;
        }
        return false;
      },
    },
    onUpdate: ({ editor: instance }) => {
      onChange(instance.getHTML());
      const { from } = instance.state.selection;
      const $from = instance.state.doc.resolve(from);
      // Slash menu only opens on an otherwise empty line starting with "/".
      const before = $from.parent.textBetween(
        0,
        $from.parentOffset,
        undefined,
        "\ufffc",
      );
      const match = /^\/([^\s/]*)$/.exec(before);
      if (!match) return setMenu(null);
      const coords = instance.view.coordsAtPos(from);
      const height = Math.min(272, blocks.length * 34 + 32);
      // Flip above the caret when the menu would fall off the viewport.
      const flip = coords.bottom + height + 12 > window.innerHeight;
      setMenu({
        query: match[1] ?? "",
        from: from - match[0].length,
        to: from,
        top: flip ? Math.max(8, coords.top - height - 6) : coords.bottom + 6,
        left: Math.min(
          Math.max(8, coords.left),
          Math.max(8, window.innerWidth - 216),
        ),
        index: 0,
      });
    },
    onCreate: ({ editor: instance }) => {
      editorRef.current = instance;
      onReady(instance);
    },
    onDestroy: () => {
      editorRef.current = null;
      onReady(null);
    },
  });

  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [menu]);

  if (!editor) return <div className="note-page note-page-loading" />;
  return (
    <div className="note-editor">
      <Toolbar editor={editor} />
      <EditorContent editor={editor} />
      {menu && options.length > 0 && (
        <div
          className="note-slash"
          role="listbox"
          aria-label="插入内容"
          style={{ top: menu.top, left: menu.left }}
        >
          <p className="note-slash-hint">插入内容</p>
          {options.map((block, index) => (
            <button
              key={block.id}
              type="button"
              role="option"
              aria-selected={index === menu.index}
              className="note-slash-item"
              // Keep the caret in the document; the menu only mirrors it.
              onMouseDown={(event) => {
                event.preventDefault();
                insert(block);
              }}
            >
              <span className="note-slash-hint">{block.hint}</span>
              {block.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

const Icon = ({ path }: { path: string }) => (
  <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
    <path
      d={path}
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);

function Palette({
  label,
  glyph,
  colors,
  onPick,
}: {
  label: string;
  glyph: string;
  colors: { name: string; value: string | null }[];
  onPick: (value: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [open]);
  return (
    <span className="note-palette">
      <button
        type="button"
        className="note-tool note-glyph"
        aria-label={label}
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((value) => !value)}
      >
        {glyph}
      </button>
      {open && (
        <div
          className="note-palette-menu"
          role="menu"
          aria-label={label}
          onMouseDown={(event) => event.stopPropagation()}
        >
          {colors.map((color) => (
            <button
              key={color.name}
              type="button"
              role="menuitem"
              className="note-swatch"
              aria-label={color.name}
              title={color.name}
              style={{ background: color.value ?? "transparent" }}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => {
                onPick(color.value);
                setOpen(false);
              }}
            />
          ))}
        </div>
      )}
    </span>
  );
}

function Toolbar({ editor }: { editor: Editor }) {
  const setLink = () => {
    const previous = editor.getAttributes("link").href as string | undefined;
    const href = askUrl("链接地址", previous ?? "https://");
    if (href === null) return;
    if (!href) return void editor.chain().focus().unsetLink().run();
    editor.chain().focus().extendMarkRange("link").setLink({ href }).run();
  };
  const state = (name: string) => (editor.isActive(name) ? "true" : undefined);
  const heading = editor.isActive("heading", { level: 1 })
    ? "h1"
    : editor.isActive("heading", { level: 2 })
      ? "h2"
      : editor.isActive("heading", { level: 3 })
        ? "h3"
        : "p";
  const size = String(editor.getAttributes("textStyle").fontSize ?? "16px");
  const inTable = editor.isActive("table");
  return (
    <div
      className="note-toolbar"
      role="toolbar"
      aria-label="正文格式"
      // Toolbar buttons must not steal focus: the caret and its selection stay
      // in the document while the command runs.
      onMouseDown={(event) => {
        if ((event.target as HTMLElement).closest("button"))
          event.preventDefault();
      }}
    >
      <span className="note-block">
        <select
          aria-label="段落样式"
          value={heading}
          onChange={(event) => {
            const value = event.target.value;
            if (value === "p") editor.chain().focus().setParagraph().run();
            else
              editor
                .chain()
                .focus()
                .setHeading({ level: Number(value.slice(1)) as 1 | 2 | 3 })
                .run();
          }}
        >
          <option value="p">正文</option>
          <option value="h1">一级标题</option>
          <option value="h2">二级标题</option>
          <option value="h3">三级标题</option>
        </select>
        <select
          aria-label="字号"
          value={size}
          onChange={(event) =>
            editor
              .chain()
              .focus()
              .setFontSize(event.target.value)
              .run()
          }
        >
          {["14px", "16px", "18px", "20px", "24px"].map((value) => (
            <option key={value} value={value}>
              {value.replace("px", "")}
            </option>
          ))}
        </select>
      </span>
      <span className="note-tool-group">
        <button
          type="button"
          className="note-tool note-glyph"
          aria-label="粗体"
          aria-pressed={state("bold")}
          onClick={() => editor.chain().focus().toggleBold().run()}
        >
          B
        </button>
        <button
          type="button"
          className="note-tool note-glyph italic"
          aria-label="斜体"
          aria-pressed={state("italic")}
          onClick={() => editor.chain().focus().toggleItalic().run()}
        >
          I
        </button>
        <button
          type="button"
          className="note-tool note-glyph underline"
          aria-label="下划线"
          aria-pressed={state("underline")}
          onClick={() => editor.chain().focus().toggleUnderline().run()}
        >
          U
        </button>
        <button
          type="button"
          className="note-tool note-glyph strike"
          aria-label="删除线"
          aria-pressed={state("strike")}
          onClick={() => editor.chain().focus().toggleStrike().run()}
        >
          S
        </button>
        <button
          type="button"
          className="note-tool"
          aria-label="行内代码"
          aria-pressed={state("code")}
          onClick={() => editor.chain().focus().toggleCode().run()}
        >
          <Icon path="m7.5 6.5-3.5 3.5 3.5 3.5M12.5 6.5 16 10l-3.5 3.5" />
        </button>
      </span>
      <span className="note-tool-group">
        <Palette
          label="文字颜色"
          glyph="A"
          colors={textColors}
          onPick={(value) => {
            const chain = editor.chain().focus();
            if (value) chain.setColor(value).run();
            else chain.unsetColor().run();
          }}
        />
        <Palette
          label="高亮颜色"
          glyph="▨"
          colors={highlightColors}
          onPick={(value) => {
            const chain = editor.chain().focus();
            if (value) chain.setBackgroundColor(value).run();
            else chain.unsetBackgroundColor().run();
          }}
        />
        <button
          type="button"
          className="note-tool"
          aria-label="左对齐"
          aria-pressed={
            editor.isActive({ textAlign: "left" }) ? "true" : undefined
          }
          onClick={() => editor.chain().focus().setTextAlign("left").run()}
        >
          <Icon path="M3.5 5.5h13M3.5 10h8M3.5 14.5h13" />
        </button>
        <button
          type="button"
          className="note-tool"
          aria-label="居中对齐"
          aria-pressed={
            editor.isActive({ textAlign: "center" }) ? "true" : undefined
          }
          onClick={() => editor.chain().focus().setTextAlign("center").run()}
        >
          <Icon path="M3.5 5.5h13M6 10h8M3.5 14.5h13" />
        </button>
        <button
          type="button"
          className="note-tool"
          aria-label="右对齐"
          aria-pressed={
            editor.isActive({ textAlign: "right" }) ? "true" : undefined
          }
          onClick={() => editor.chain().focus().setTextAlign("right").run()}
        >
          <Icon path="M3.5 5.5h13M8.5 10h8M3.5 14.5h13" />
        </button>
      </span>
      <span className="note-tool-group">
        <button
          type="button"
          className="note-tool"
          aria-label="引用"
          aria-pressed={state("blockquote")}
          onClick={() => editor.chain().focus().toggleBlockquote().run()}
        >
          <Icon path="M6.5 15V9.5A3.5 3.5 0 0 1 10 6M13.5 15V9.5A3.5 3.5 0 0 1 17 6" />
        </button>
        <button
          type="button"
          className="note-tool"
          aria-label="无序列表"
          aria-pressed={state("bulletList")}
          onClick={() => editor.chain().focus().toggleBulletList().run()}
        >
          <Icon path="M6.5 5.5h10M6.5 10h10M6.5 14.5h10M3.5 5.5h.01M3.5 10h.01M3.5 14.5h.01" />
        </button>
        <button
          type="button"
          className="note-tool"
          aria-label="有序列表"
          aria-pressed={state("orderedList")}
          onClick={() => editor.chain().focus().toggleOrderedList().run()}
        >
          <Icon path="M7 5.5h9.5M7 10h9.5M7 14.5h9.5M3.2 4.6l.9-.6v3M3 14.2c0-.7 1.6-.8 1.6 0 0 .6-1.6 1-1.6 1.7h1.8" />
        </button>
        <button
          type="button"
          className="note-tool"
          aria-label="任务列表"
          aria-pressed={state("taskList")}
          onClick={() => editor.chain().focus().toggleTaskList().run()}
        >
          <Icon path="M3.5 5.5h2v2h-2zM3.5 12.5h2v2h-2zM8 6.5h8.5M8 13.5h8.5" />
        </button>
      </span>
      <span className="note-tool-group">
        <button
          type="button"
          className="note-tool"
          aria-label="插入链接"
          aria-pressed={state("link")}
          onClick={setLink}
        >
          <Icon path="M8.5 11.5 11.5 8.5M7 13l-1 1a2.8 2.8 0 0 1-4-4l2.5-2.5a2.8 2.8 0 0 1 4 0M13 7l1-1a2.8 2.8 0 0 1 4 4L15.5 12.5a2.8 2.8 0 0 1-4 0" />
        </button>
        <button
          type="button"
          className="note-tool"
          aria-label="插入图片"
          onClick={() => {
            const src = askUrl("图片地址");
            if (src) editor.chain().focus().setImage({ src }).run();
          }}
        >
          <Icon path="M3.5 4.5h13v11h-13zM3.5 12l4-4 3.5 3.5 2.5-2.5 3 3M7 7.5h.01" />
        </button>
        <button
          type="button"
          className="note-tool"
          aria-label="插入表格"
          aria-pressed={state("table")}
          onClick={() =>
            editor
              .chain()
              .focus()
              .insertTable({ rows: 3, cols: 3, withHeaderRow: true })
              .run()
          }
        >
          <Icon path="M3 4.5h14v11H3zM3 8h14M3 11.5h14M8 4.5v11M13 4.5v11" />
        </button>
        <button
          type="button"
          className="note-tool"
          aria-label="分隔线"
          onClick={() => editor.chain().focus().setHorizontalRule().run()}
        >
          <Icon path="M3 10h14" />
        </button>
        <button
          type="button"
          className="note-tool"
          aria-label="代码块"
          aria-pressed={state("codeBlock")}
          onClick={() => editor.chain().focus().toggleCodeBlock().run()}
        >
          <Icon path="M3.5 4.5h13v11h-13zM8 8l-2 2 2 2M12 8l2 2-2 2" />
        </button>
      </span>
      {inTable && (
        <span className="note-tool-group" aria-label="表格操作">
          <button
            type="button"
            className="note-tool note-text"
            aria-label="在下方插入行"
            onClick={() => editor.chain().focus().addRowAfter().run()}
          >
            行+
          </button>
          <button
            type="button"
            className="note-tool note-text"
            aria-label="在右侧插入列"
            onClick={() => editor.chain().focus().addColumnAfter().run()}
          >
            列+
          </button>
          <button
            type="button"
            className="note-tool note-text"
            aria-label="删除当前行"
            onClick={() => editor.chain().focus().deleteRow().run()}
          >
            行−
          </button>
          <button
            type="button"
            className="note-tool note-text"
            aria-label="删除当前列"
            onClick={() => editor.chain().focus().deleteColumn().run()}
          >
            列−
          </button>
          <button
            type="button"
            className="note-tool note-text"
            aria-label="删除表格"
            onClick={() => editor.chain().focus().deleteTable().run()}
          >
            删表
          </button>
        </span>
      )}
      <span className="note-tool-group note-tool-history">
        <button
          type="button"
          className="note-tool"
          aria-label="撤销"
          disabled={!editor.can().undo()}
          onClick={() => editor.chain().focus().undo().run()}
        >
          <Icon path="M7 6.5 3.5 10 7 13.5M4 10h7.5a4 4 0 0 1 0 8H10" />
        </button>
        <button
          type="button"
          className="note-tool"
          aria-label="重做"
          disabled={!editor.can().redo()}
          onClick={() => editor.chain().focus().redo().run()}
        >
          <Icon path="M13 6.5 16.5 10 13 13.5M16 10H8.5a4 4 0 0 0 0 8H10" />
        </button>
      </span>
    </div>
  );
}
