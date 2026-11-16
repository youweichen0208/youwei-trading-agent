"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  isTerminalStatus,
  traceRows,
  type ProgressItem,
  type StepState,
} from "../lib/research-progress";

const isTerminal = isTerminalStatus;

function formatDuration(seconds: number) {
  if (seconds < 60) return `${seconds} 秒`;
  return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

function StateIcon({ state }: { state: StepState }) {
  if (state === "running")
    return <span className="trace-pulse" aria-hidden="true" />;
  if (state === "thinking")
    return (
      <span className="trace-thinking" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
    );
  if (state === "queued")
    return <span className="trace-queued" aria-hidden="true" />;
  const path =
    state === "done" ? "m4 10.5 4 4 8-9" : state === "warn" ? "M10 4v7m0 4v.5" : "m6 6 8 8M14 6l-8 8";
  return (
    <svg
      className="trace-state-icon"
      data-state={state}
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={path} />
    </svg>
  );
}

function ThoughtBlock({
  text,
  roleLabel,
  defaultOpen,
}: {
  text: string;
  roleLabel?: string;
  defaultOpen: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const preview = text.replace(/\s+/g, " ").trim();
  return (
    <details
      className="trace-thought"
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        <span className="trace-thought-icon" aria-hidden="true">
          <svg
            viewBox="0 0 20 20"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M8 15.5h4M9 18h2M10 2.8a5.2 5.2 0 0 0-3.1 9.4c.6.5 1 1.2 1.1 2h4c.1-.8.5-1.5 1.1-2A5.2 5.2 0 0 0 10 2.8Z" />
          </svg>
        </span>
        <span className="trace-thought-title">思考过程</span>
        {roleLabel && (
          <span className="trace-thought-role">{roleLabel}</span>
        )}
        {!open && preview && (
          <span className="trace-thought-preview">{preview}</span>
        )}
        <span className="trace-thought-hint">{open ? "收起" : "展开"}</span>
        <svg
          className="trace-thought-chevron"
          viewBox="0 0 20 20"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="m5 8 5 5 5-5" />
        </svg>
      </summary>
      <div className="trace-thought-body">
        <p className="trace-thought-note">模型原始推理，工具名已转为中文</p>
        {text.split(/\n{2,}/).map((paragraph, index) => (
          <p key={index} className="trace-thought-text">
            {paragraph}
          </p>
        ))}
      </div>
    </details>
  );
}

export function ResearchTrace({
  taskId,
  status,
  role,
  connectionError = false,
  currentAction,
  items,
  onStop,
}: {
  taskId: string;
  status: string;
  role?: string;
  connectionError?: boolean;
  currentAction?: string;
  items: ProgressItem[];
  onStop?: () => void;
}) {
  const busy = !isTerminal(status);
  const [seconds, setSeconds] = useState(0);
  const [expanded, setExpanded] = useState(busy);
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const started = Date.now();
    setSeconds(0);
    if (isTerminal(status)) return;
    const timer = setInterval(
      () => setSeconds(Math.floor((Date.now() - started) / 1000)),
      1000,
    );
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);

  useEffect(() => {
    if (!expanded || !busy) return;
    const node = bodyRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [items.length, expanded, busy]);

  const steps = useMemo(() => traceRows(items, status), [items, status]);
  const stepCount = steps.filter((row) => row.kind === "step").length;
  const streaming = items.at(-1)?.data.stage === "response.streaming";
  const latest = steps.at(-1)?.meta;
  const headState: StepState = connectionError
    ? "warn"
    : status === "failed" || status === "interrupted"
      ? "failed"
      : status === "stopping" || status === "cancelled"
        ? "warn"
        : !busy
          ? "done"
          : streaming
            ? "thinking"
            : steps.some((row) => row.meta.tool)
              ? (latest?.state ?? "running")
              : "thinking";
  // A consult answers a question; calling it a finished "研究" would imply
  // evidence was frozen and forecasts published when nothing happened.
  const consult = role === "consult";
  const headLabel = connectionError
    ? "连接异常，正在恢复"
    : busy
      ? currentAction ?? "模型正在思考"
      : status === "failed" || status === "interrupted"
        ? consult
          ? "回答未完成"
          : "研究未完成"
        : status === "cancelled"
          ? "任务已取消"
          : consult
            ? "已回答"
            : "研究已完成";

  return (
    <section
      className="research-trace"
      data-busy={busy || undefined}
      aria-label={consult ? "回答过程" : "研究执行轨迹"}
    >
      <div className="trace-head">
        <button
          type="button"
          className="trace-toggle"
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
        >
          <span className="trace-head-state" data-state={headState} aria-hidden="true">
            <StateIcon state={headState} />
          </span>
          <span className="trace-current" role="status" title={headLabel}>
            {busy ? headLabel : "研究过程"}
          </span>
          <span className="trace-steps">{stepCount} 个步骤</span>
          <svg
            className="trace-chevron"
            data-expanded={expanded || undefined}
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
        </button>
        {busy && <span className="trace-time">已用时 {formatDuration(seconds)}</span>}
        {busy && onStop && (
          <button
            type="button"
            className="trace-stop"
            onClick={onStop}
            disabled={status === "stopping"}
            aria-label="停止任务"
          >
            {status === "stopping" ? "正在停止…" : "停止"}
          </button>
        )}
      </div>
      {expanded && (
        <div className="trace-body" ref={bodyRef}>
          {steps.length ? (
            <ol className="trace-list">
              {steps.map((row, index) => {
                const step = row.meta;
                const previous = steps[index - 1]?.meta;
                const showGroup =
                  step.roleLabel && step.roleLabel !== previous?.roleLabel;
                const nextStep = steps
                  .slice(index + 1)
                  .find((later) => later.kind === "step");
                const hasChinese = !!nextStep?.meta.narration;
                return (
                  <li key={row.key} className="trace-item">
                    {showGroup && row.kind !== "thinking" && (
                      <p className="trace-group">{step.roleLabel}</p>
                    )}
                    {row.kind === "thinking" ? (
                      <ThoughtBlock
                        key={`${row.key}:${hasChinese}`}
                        text={step.detail ?? ""}
                        roleLabel={step.roleLabel}
                        defaultOpen={busy || !hasChinese}
                      />
                    ) : (
                      <>
                        {step.narration && (
                          <p
                            className="trace-narration"
                            title={step.narration}
                          >
                            <span className="trace-narration-label">
                              思考
                            </span>
                            {step.narration}
                          </p>
                        )}
                        <div className="trace-step" data-state={step.state}>
                          <span className="trace-index" aria-hidden="true">
                            {String(row.number).padStart(2, "0")}
                          </span>
                          <span className="trace-icon" aria-hidden="true">
                            <StateIcon state={step.state} />
                          </span>
                          <span className="trace-label">
                            <span className="trace-label-text">
                              {step.label}
                              {row.count && row.count > 1 && (
                                <span className="trace-count">
                                  ×{row.count}
                                </span>
                              )}
                            </span>
                            {step.detail && (
                              <span
                                className="trace-detail"
                                title={
                                  row.details?.join(" · ") ?? step.detail
                                }
                              >
                                {step.detail}
                              </span>
                            )}
                          </span>
                        </div>
                      </>
                    )}
                  </li>
                );
              })}
            </ol>
          ) : (
            <p className="trace-empty">任务已提交，等待执行事件。</p>
          )}
        </div>
      )}
    </section>
  );
}
