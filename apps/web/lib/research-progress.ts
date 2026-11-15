export type ProgressItem = {
  sequence: number;
  data: {
    stage?: string;
    tool?: string;
    status?: string;
    role?: string;
    detail?: string;
    reason?: string;
    /** Client-side excerpt of the visible assistant text before this tool call. */
    narration?: string;
  };
};

export const toolNames: Record<string, string> = {
  market_history: "查询历史行情",
  collect_evidence: "采集研究证据",
  read_research_context: "读取研究上下文",
  read_forecasts: "读取历史预测",
  publish_forecast: "发布预测",
  record_review: "记录复盘",
  get_trial_context: "读取试验上下文",
  publish_forecast_trial_arm: "发布试验结果",
  quant_calculate: "计算指标与量化结果",
  read_dataset: "读取行情数据",
  read_result: "读取分析结果",
  read_holdings: "读取模拟持仓",
  get_operation: "检查数据计算进度",
  submit_specialist: "安排预测或组合分析",
  get_specialist_result: "读取专项分析结果",
  get_workspace: "读取已有图表",
  save_research_summary: "保存研究分析",
  canvas_upsert: "生成可视化产物",
  canvas_patch: "更新可视化产物",
  canvas_remove: "调整研究产物",
};

const stateNames: Record<string, string> = {
  queued: "任务已进入队列",
  running: "研究进行中",
  completed: "研究完成",
  failed: "研究未完成",
  cancelled: "任务已取消",
  stopping: "正在停止",
  unknown: "正在核对执行状态",
  interrupted: "研究已中断",
};

export const roleNames: Record<string, string> = {
  research: "研究",
  prediction: "预测",
  portfolio: "组合",
};

/** Human-readable cause for a stop/failure transition, when the Worker knows one. */
const reasonNames: Record<string, string> = {
  required_child_failed: "专项分析任务失败",
  required_tool_failed: "必需工具调用失败",
  deadline_exceeded: "超过执行时限",
  final_output_missing: "未返回最终结论",
  unsupported_upstream_control: "运行环境不支持停止",
  user_requested: "用户请求停止",
};

export function progressLabel(item: ProgressItem) {
  const { stage, tool, status, role } = item.data;
  const prefix = roleNames[role ?? ""] ? `${roleNames[role!]} · ` : "";
  if (stage === "response.streaming") return "正在接收回复";
  if (stage === "event_history_gap") return "部分活动记录缺失，正在同步任务状态";
  if (stage === "tool.started" || stage === "tool.completed")
    return (
      prefix +
      (toolNames[tool ?? ""] ?? "执行研究工具") +
      (stage === "tool.completed" ? " · 已结束" : " · 开始")
    );
  if (stage === "reasoning.available" || stage === "reasoning.delta")
    return `${prefix}思考`;
  if (stage === "message.interim") return `${prefix}说明`;
  return prefix + (stateNames[status ?? ""] ?? "任务状态更新");
}

export type StepState =
  | "done"
  | "running"
  | "queued"
  | "failed"
  | "warn"
  | "thinking";

export type ProgressMeta = {
  role?: string;
  roleLabel: string;
  label: string;
  detail?: string;
  narration?: string;
  tool?: string;
  state: StepState;
};

function stateForStatus(status?: string): StepState {
  if (status === "completed") return "done";
  if (status === "running") return "running";
  if (status === "failed" || status === "cancelled" || status === "interrupted")
    return "failed";
  if (status === "stopping" || status === "unknown") return "warn";
  return "queued";
}

/** Structured view of a progress event, for the execution trace timeline. */
export function progressMeta(item: ProgressItem): ProgressMeta {
  const { stage, tool, status, role, detail, reason, narration } = item.data;
  const roleLabel = roleNames[role ?? ""] ?? "";
  if (stage === "response.streaming")
    return { role, roleLabel, label: "模型思考", state: "thinking" };
  if (stage === "event_history_gap")
    return { role, roleLabel, label: "记录缺口，同步任务状态", state: "warn" };
  if (stage === "reasoning.available" || stage === "reasoning.delta")
    return {
      role,
      roleLabel,
      label: "思考",
      detail: detail ?? reasonNames[reason ?? ""],
      state: "thinking",
    };
  if (stage === "message.interim")
    return {
      role,
      roleLabel,
      label: "说明",
      detail: detail ?? reasonNames[reason ?? ""],
      state: "thinking",
    };
  if (stage === "tool.started")
    return {
      role,
      roleLabel,
      label: toolNames[tool ?? ""] ?? "执行研究工具",
      detail,
      narration,
      tool,
      state: "running",
    };
  if (stage === "tool.completed")
    return {
      role,
      roleLabel,
      label: toolNames[tool ?? ""] ?? "执行研究工具",
      detail,
      tool,
      state: "done",
    };
  return {
    role,
    roleLabel,
    label: stateNames[status ?? ""] ?? "任务状态更新",
    detail: detail ?? reasonNames[reason ?? ""],
    state: stateForStatus(status),
  };
}

export function compactProgress(items: ProgressItem[]) {
  return items.filter((item, index) => {
    if (index === 0) return true;
    // Tool and commentary events carry real work; never collapse them even
    // when consecutive entries share a label.
    if (
      item.data.stage === "tool.started" ||
      item.data.stage === "tool.completed" ||
      isThoughtStage(item.data.stage)
    )
      return true;
    return progressLabel(item) !== progressLabel(items[index - 1]!);
  });
}

const isThoughtStage = (stage?: string) =>
  stage === "reasoning.available" ||
  stage === "reasoning.delta" ||
  stage === "message.interim";

/**
 * Bound the live timeline without discarding the model's thinking. Tool/status
 * events keep the newest `limit` rows; reasoning rows keep the newest
 * `thoughtLimit` so a long run does not hide its earlier thinking.
 */
export function trimProgress(
  items: ProgressItem[],
  limit = 100,
  thoughtLimit = 200,
) {
  const thoughts: ProgressItem[] = [];
  const others: ProgressItem[] = [];
  for (const item of items)
    (isThoughtStage(item.data.stage) ? thoughts : others).push(item);
  if (thoughts.length <= thoughtLimit && others.length <= limit) return items;
  const keep = new Set([
    ...thoughts.slice(-thoughtLimit),
    ...others.slice(-limit),
  ]);
  return items.filter((item) => keep.has(item));
}

export const isTerminalStatus = (status: string) =>
  ["completed", "failed", "cancelled", "interrupted"].includes(status);

export type TraceRow = {
  key: string;
  meta: ProgressMeta;
  kind: "step" | "thinking";
  number?: number;
  /** Consecutive identical completed tool calls collapsed into this row. */
  count?: number;
  /** Every distinct summary of the collapsed calls, for the row tooltip. */
  details?: string[];
};

/** Component and tool identifiers that read better as Chinese in reasoning text. */
const thoughtTerms: Record<string, string> = {
  ...toolNames,
  stock_chart: "股票走势图",
  indicator_panel: "指标面板",
  forecast_result: "预测结果卡片",
  prediction_result: "预测结果",
  portfolio_summary: "组合摘要",
  research_summary: "研究摘要",
  forecast_evaluation: "预测评估",
};

function cleanThought(text: string) {
  let value = text.trim();
  // The runtime keeps the most recent slice of long reasoning; drop the
  // truncated first word so the block does not start mid-word.
  if (value.startsWith("…")) {
    value = value.replace(/^…+/, "").trimStart();
    const boundary = value.search(/\s/);
    if (boundary > 0 && boundary <= 24) value = value.slice(boundary).trimStart();
  }
  return value.replace(/\b[a-z][a-z0-9_]{2,}\b/g, (word) => thoughtTerms[word] ?? word);
}

/**
 * Timeline rows: numbered steps for tools and task state, with reasoning and
 * commentary rendered as unnumbered thinking blocks. `tool.completed` is folded
 * into the matching `tool.started`; streaming placeholders are not timeline rows.
 */
export function traceRows(
  items: ProgressItem[],
  status: string,
): TraceRow[] {
  const metas = items.map(progressMeta);
  const folded = new Set<number>();
  const rows: TraceRow[] = [];
  let stepNumber = 0;
  items.forEach((item, index) => {
    if (folded.has(index)) return;
    const stage = item.data.stage ?? "";
    if (stage === "response.streaming") return;
    // Once the task is terminal, intermediate queue/running rows would keep a
    // green "running" icon in a finished trace. Drop them; keep the terminal row.
    if (!stage && isTerminalStatus(status) && !isTerminalStatus(item.data.status ?? ""))
      return;
    let meta = metas[index]!;
    const thinking =
      stage === "reasoning.available" ||
      stage === "reasoning.delta" ||
      stage === "message.interim";
    if (thinking) {
      const text = cleanThought(meta.detail ?? "");
      if (!text) return;
      const previous = rows.at(-1);
      if (
        previous?.kind === "thinking" &&
        previous.meta.label === meta.label &&
        previous.meta.role === meta.role
      ) {
        const combined = `${previous.meta.detail ?? ""}\n\n${text}`;
        previous.meta = {
          ...previous.meta,
          detail:
            combined.length > 4000 ? `${combined.slice(0, 4000)}…` : combined,
        };
        return;
      }
      rows.push({
        key: String(item.sequence),
        meta: { ...meta, detail: text.slice(0, 4000) },
        kind: "thinking",
      });
      return;
    }
    if (item.data.stage === "tool.started") {
      const completedAt = metas.findIndex(
        (later, laterIndex) =>
          laterIndex > index &&
          !folded.has(laterIndex) &&
          items[laterIndex]!.data.stage === "tool.completed" &&
          later.tool === meta.tool &&
          later.role === meta.role,
      );
      if (completedAt >= 0) {
        folded.add(completedAt);
        const result = metas[completedAt]!.detail;
        const detail =
          meta.detail && result && result !== meta.detail
            ? `${meta.detail} · ${result}`.slice(0, 180)
            : meta.detail ?? result;
        meta = { ...meta, state: "done", ...(detail ? { detail } : {}) };
      } else if (isTerminalStatus(status)) {
        meta = { ...meta, state: status === "completed" ? "done" : "failed" };
      }
    }
    const previous = rows.at(-1);
    if (
      meta.tool &&
      meta.state === "done" &&
      previous?.kind === "step" &&
      previous.meta.state === "done" &&
      previous.meta.tool === meta.tool &&
      previous.meta.role === meta.role &&
      previous.meta.label === meta.label
    ) {
      previous.count = (previous.count ?? 1) + 1;
      const seen = new Set(
        previous.details ??
          (previous.meta.detail ? [previous.meta.detail] : []),
      );
      if (meta.detail) seen.add(meta.detail);
      previous.details = [...seen];
      previous.meta = {
        ...previous.meta,
        detail: meta.detail ?? previous.meta.detail,
      };
      return;
    }
    stepNumber++;
    rows.push({
      key: String(item.sequence),
      meta,
      kind: "step",
      number: stepNumber,
    });
  });
  return rows;
}

export function currentProgress(items: ProgressItem[], status: string) {
  if (status !== "running") return progressLabel({ sequence: 0, data: { status } });
  const latest = items.at(-1);
  if (latest?.data.stage === "event_history_gap") return progressLabel(latest);
  if (latest?.data.stage === "response.streaming") return "正在生成研究思路";
  const tool = [...items]
    .reverse()
    .find(
      (item) =>
        item.data.stage === "tool.started" ||
        item.data.stage === "tool.completed",
    );
  if (!tool) return "模型正在思考";
  return progressLabel(tool);
}
