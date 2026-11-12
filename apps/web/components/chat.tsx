"use client";
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useLocalRuntime,
  useAuiState,
  type ChatModelAdapter,
  type ThreadMessageLike,
} from "@assistant-ui/react";
import {
  api,
  isTerminal,
  message,
  statusName,
  taskEvents,
} from "../lib/client";
import { useCanvas } from "../lib/canvas";
import { WelcomeHeading } from "./research-welcome";
const ResultContext = createContext({
  onOpen: () => {},
  open: false,
  busy: false,
});

function savedMessages(
  messages: any[],
  task: any,
  workspace: any,
): ThreadMessageLike[] {
  return messages.map((m) => ({
    id: m.id,
    role: m.role,
    content: m.content,
    metadata: {
      custom: {
        taskId: m.task_id,
        resultRevision:
          task?.status === "completed" && m.task_id === task.id
            ? workspace?.revision
            : undefined,
      },
    },
  }));
}
function ResultCard() {
  const { onOpen, open, busy } = useContext(ResultContext);
  const artifacts = useCanvas((s) => s.artifacts);
  const resultRevision = useAuiState(
    (s) => s.message.metadata.custom.resultRevision,
  );
  const taskId = useAuiState((s) => s.message.metadata.custom.taskId);
  const revision = useCanvas((s) => s.revision);
  const historyTaskId = useCanvas((s) => s.historyTaskId);
  if (
    busy ||
    !artifacts.length ||
    !taskId ||
    resultRevision !== revision ||
    historyTaskId
  )
    return null;
  const title =
    artifacts.find((a) => a.component === "stock_chart")?.title ??
    "已保存的研究结果";
  return (
    <button
      className="research-result-card"
      onClick={onOpen}
      aria-expanded={open}
    >
      <span className="result-card-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24">
          <path
            d="M4 4v16h16M7 15l4-5 4 3 5-7"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.7"
          />
        </svg>
      </span>
      <span>
        <strong>{title}</strong>
        <small>研究画布，{artifacts.length} 项结果</small>
      </span>
      <span className="result-card-arrow" aria-hidden="true">
        ›
      </span>
    </button>
  );
}
function Message() {
  const role = useAuiState((state) => state.message.role);
  return (
    <MessagePrimitive.Root className="chat-message" data-role={role}>
      <div className="message-body">
        <MessagePrimitive.Parts />
      </div>
      {role === "assistant" && <ResultCard />}
    </MessagePrimitive.Root>
  );
}
export function Chat({
  thread,
  portfolioRef,
  onChange,
  initialPrompt,
  onOpenCanvas,
  canvasOpen,
  profileState,
  drafts,
}: {
  thread: any;
  portfolioRef?: string;
  onChange: () => void;
  initialPrompt?: string;
  onOpenCanvas: () => void;
  canvasOpen: boolean;
  profileState?: string;
  drafts: Map<string, string>;
}) {
  const [task, setTask] = useState<any>(thread.tasks?.[0] ?? null),
    [error, setError] = useState("");
  const active = useRef<any>(task),
    load = useCanvas((s) => s.load),
    sync = useCanvas((s) => s.sync);
  const [requestPending, setRequestPending] = useState(false);
  const [retryParent, setRetryParent] = useState<string | null>(null);
  const admission = useRef<{
    key: string;
    input: string;
    portfolioRef?: string;
  } | null>(null);
  const initial = useMemo<ThreadMessageLike[]>(
    () => savedMessages(thread.messages, thread.tasks?.[0], thread.workspace),
    [thread.id],
  );
  const adapter = useMemo<ChatModelAdapter>(
    () => ({
      async *run({ messages, abortSignal }) {
        setError("");
        setRetryParent(null);
        setRequestPending(true);
        const last = messages.at(-1);
        const input =
          last?.content
            .filter((p) => p.type === "text")
            .map((p) => p.text)
            .join("\n") ?? "";
        if (
          !admission.current ||
          admission.current.input !== input ||
          admission.current.portfolioRef !== portfolioRef
        )
          admission.current = { key: crypto.randomUUID(), input, portfolioRef };
        const key = admission.current.key;
        drafts.delete(thread.id);
        let submitted;
        const submit = () =>
          api(
            "research",
            "POST",
            {
              threadId: thread.id,
              input,
              ...(portfolioRef ? { portfolioSnapshotRef: portfolioRef } : {}),
            },
            { "Idempotency-Key": key },
          );
        try {
          try {
            submitted = await submit();
          } catch (first) {
            if (first instanceof TypeError) submitted = await submit();
            else throw first;
          }
          admission.current = null;
        } catch (e) {
          setError(message(e));
          setRetryParent(last?.id ?? null);
          setRequestPending(false);
          throw e;
        }
        active.current = { id: submitted.taskId, status: submitted.status };
        setTask(active.current);
        onChange();
        let text = "";
        try {
          for await (const event of taskEvents(submitted.taskId, abortSignal)) {
            if (event.event === "connection.reconnecting") {
              setError("连接正在恢复，任务继续在后台运行。");
            } else if (event.event === "connection.restored") {
              setError("");
            } else if (event.event === "state.snapshot") {
              const snap = event.data;
              active.current = snap.task;
              setTask(snap.task);
              sync(snap.workspace);
              const final = snap.messages.find(
                (m: any) =>
                  m.task_id === submitted.taskId && m.role === "assistant",
              );
              text = final?.content ?? snap.task.stream_text ?? "";
              if (isTerminal(snap.task.status) && !text)
                text =
                  statusName[snap.task.status] +
                  (snap.task.output?.reason
                    ? " · " +
                      (snap.task.output.reason === "provider_unavailable"
                        ? "研究服务尚未连接"
                        : "请查看任务状态")
                    : "");
            } else if (event.event.startsWith("canvas.")) {
              if (!useCanvas.getState().apply(event))
                sync(await api(`workspaces/${event.data.workspaceId}`));
            } else if (event.event === "assistant.delta")
              text += event.data.text ?? "";
            else if (event.data?.status && !event.data?.childTaskId) {
              active.current = { ...active.current, status: event.data.status };
              setTask(active.current);
              if (isTerminal(event.data.status))
                text = event.data.output?.text ?? statusName[event.data.status];
            }
            if (text)
              yield {
                content: [{ type: "text", text }],
                metadata: {
                  custom: {
                    taskId: submitted.taskId,
                    resultRevision:
                      active.current?.status === "completed"
                        ? useCanvas.getState().revision
                        : undefined,
                  },
                },
              };
          }
        } catch (e) {
          if (!abortSignal.aborted) {
            setError(message(e));
            throw e;
          }
        } finally {
          setRequestPending(false);
          onChange();
        }
      },
    }),
    [thread.id, portfolioRef, sync, onChange, drafts],
  );
  const runtime = useLocalRuntime(adapter, { initialMessages: initial });
  useEffect(() => {
    const composer = runtime.thread.composer;
    composer.setText(drafts.get(thread.id) ?? initialPrompt ?? "");
  }, [runtime, drafts, thread.id]);
  useEffect(() => {
    if (!thread.messages.length)
      document
        .querySelector<HTMLTextAreaElement>(".composer textarea")
        ?.focus();
    load(thread.workspace);
  }, [thread.id, load]);
  useEffect(() => {
    const pending = thread.tasks?.find((t: any) => !isTerminal(t.status));
    if (!pending) return;
    const controller = new AbortController();
    (async () => {
      let restored: ThreadMessageLike[] = initial;
      let partial = "";
      try {
        for await (const ev of taskEvents(pending.id, controller.signal)) {
          if (ev.event === "connection.reconnecting") {
            setError("连接正在恢复，任务继续在后台运行。");
          } else if (ev.event === "connection.restored") {
            setError("");
          } else if (ev.event === "state.snapshot") {
            setTask(ev.data.task);
            active.current = ev.data.task;
            sync(ev.data.workspace);
            restored = savedMessages(
              ev.data.messages,
              ev.data.task,
              ev.data.workspace,
            );
            partial = ev.data.task.stream_text ?? "";
            runtime.thread.reset(
              !isTerminal(ev.data.task.status) && partial
                ? [
                    ...restored,
                    {
                      id: ev.data.task.stream_message_id,
                      role: "assistant",
                      content: partial,
                    },
                  ]
                : restored,
            );
          } else if (ev.event === "assistant.delta") {
            partial += ev.data.text ?? "";
            runtime.thread.reset([
              ...restored,
              {
                id: ev.data.messageId,
                role: "assistant",
                content: partial,
              },
            ]);
          } else if (ev.event.startsWith("canvas.")) {
            if (!useCanvas.getState().apply(ev))
              sync(await api(`workspaces/${ev.data.workspaceId}`));
          } else if (ev.data?.status && !ev.data.childTaskId) {
            setTask((old: any) => ({ ...old, status: ev.data.status }));
            if (isTerminal(ev.data.status)) {
              const latest = await api(`threads/${thread.id}`);
              runtime.thread.reset(
                savedMessages(
                  latest.messages,
                  latest.tasks?.[0],
                  latest.workspace,
                ),
              );
              onChange();
            }
          }
        }
      } catch (e) {
        if (!controller.signal.aborted) setError(message(e));
      }
    })();
    return () => controller.abort();
  }, [thread.id]);
  async function stop() {
    try {
      const result = await api(`tasks/${task.id}/stop`, "POST");
      setTask({ ...task, status: result.status });
    } catch (e) {
      setError(message(e));
    }
  }
  const busy = task && !isTerminal(task.status);
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ResultContext.Provider
        value={{
          onOpen: onOpenCanvas,
          open: canvasOpen,
          busy: !!busy || requestPending,
        }}
      >
        <div className="chat-shell">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">HERMES AGENT</span>
              <h2>研究对话</h2>
            </div>
            <span className="tag">
              {task ? statusName[task.status] : "准备就绪"}
            </span>
          </div>
          <ThreadPrimitive.Root className="thread">
            <ThreadPrimitive.Viewport className="messages">
              <ThreadPrimitive.Empty>
                <WelcomeHeading />
              </ThreadPrimitive.Empty>
              <ThreadPrimitive.Messages components={{ Message }} />
            </ThreadPrimitive.Viewport>
            {error && (
              <p className="notice" role="alert">
                {error}
                {retryParent && thread.source !== "group" && (
                  <button
                    type="button"
                    disabled={requestPending || !!busy}
                    onClick={() =>
                      runtime.thread.startRun({ parentId: retryParent })
                    }
                  >
                    重试这条问题
                  </button>
                )}
              </p>
            )}
            {busy && thread.source !== "group" && (
              <div className="run-line" aria-live="polite">
                <span className="pulse" />
                {statusName[task.status]}
                <button onClick={stop} disabled={task.status === "stopping"}>
                  停止任务
                </button>
              </div>
            )}
            {thread.source === "group" ? (
              <p role="status">
                群研究为只读记录。请回到原飞书群 @ 助手续聊，或发送
                /status、/stop。
              </p>
            ) : (
              <ComposerPrimitive.Root className="composer">
                <ComposerPrimitive.Input
                  placeholder="向 Hermes 提问…"
                  aria-label="研究问题"
                  onChange={(event) => {
                    const text = event.currentTarget.value;
                    if (text) drafts.set(thread.id, text);
                    else drafts.delete(thread.id);
                  }}
                  disabled={!!busy || requestPending}
                />
                <div className="composer-bottom">
                  <small className="composer-hint">
                    Enter 发送 · Shift + Enter 换行
                  </small>
                  <span className="agent-label" data-state={profileState}>
                    <i />
                    Hermes Agent
                  </span>
                  <ComposerPrimitive.Send
                    disabled={!!busy || requestPending}
                    aria-label="发送研究"
                  >
                    ↑
                  </ComposerPrimitive.Send>
                </div>
              </ComposerPrimitive.Root>
            )}
            <p className="caption">
              用于研究与模拟 · 所有数值应能追溯到数据来源
            </p>
          </ThreadPrimitive.Root>
        </div>
      </ResultContext.Provider>
    </AssistantRuntimeProvider>
  );
}
