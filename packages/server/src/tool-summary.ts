// Derives a short, whitelisted summary from a bounded upstream tool preview.
// Raw previews, arguments, reasoning and provider payloads are never stored.

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const MAX_DETAIL = 160;

const TOOL_STATUS: Record<string, string> = {
  completed: "完成",
  partial: "部分覆盖",
  failed: "失败",
};

function record(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function text(value: unknown, max = 32): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed && trimmed.length <= 512 ? trimmed.slice(0, max) : undefined;
}

function date(value: unknown): string | undefined {
  return typeof value === "string" && DATE.test(value) ? value : undefined;
}

function span(start?: string, end?: string): string | undefined {
  if (start && end) return start === end ? start : `${start}→${end}`;
  return start ?? end;
}

function count(value: unknown): number | undefined {
  return Array.isArray(value) ? value.length : undefined;
}

function numberCount(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? Math.round(value)
    : undefined;
}

function previewRecord(preview?: string): Record<string, unknown> | undefined {
  if (!preview) return undefined;
  try {
    return record(JSON.parse(preview));
  } catch {
    return undefined;
  }
}

const QUANT_KINDS: Record<string, string> = {
  indicators: "技术指标",
  prediction: "预测计算",
  portfolio: "组合计算",
};

const ADJUSTMENTS: Record<string, string> = {
  raw: "原始价格",
  split: "复权价格",
};

/** Tool identity is already the row title; this adds what was asked / returned. */
export function toolDetail(
  stage: string,
  preview?: string,
  durationSeconds?: number,
): string | undefined {
  const parts: string[] = [];
  const data = previewRecord(preview);
  if (data) {
    const symbol = text(data.symbol, 16);
    if (symbol) parts.push(symbol);

    const range =
      record(data.sessionRange) ?? record(data.displayRange) ?? undefined;
    const label = span(
      date(data.start) ?? date(range?.start) ?? date(data.asOf),
      date(data.end) ?? date(range?.end),
    );
    if (label) parts.push(label);

    if (stage === "tool.completed") {
      const status = text(data.status, 16);
      if (status && TOOL_STATUS[status]) parts.push(TOOL_STATUS[status]);
      const refs = count(data.evidenceRefs);
      if (refs !== undefined) parts.push(`证据 ${refs} 条`);
      const counts = record(data.counts);
      if (counts) {
        const evidence = numberCount(counts.evidence);
        const forecasts = numberCount(counts.forecasts);
        const lessons = numberCount(counts.lessons);
        if (evidence !== undefined) parts.push(`证据 ${evidence} 条`);
        if (forecasts !== undefined) parts.push(`预测 ${forecasts} 条`);
        if (lessons !== undefined) parts.push(`经验 ${lessons} 条`);
      }
      const failures = count(data.failures);
      if (failures) parts.push(`${failures} 个来源失败`);
      const sources = count(data.sources);
      if (sources !== undefined) parts.push(`${sources} 个来源`);
    } else {
      const sources = Array.isArray(data.sources)
        ? data.sources.filter((value) => typeof value === "string").slice(0, 3)
        : [];
      if (sources.length) parts.push(`来源 ${sources.join("/")}`);
      const kind = text(data.kind, 16);
      if (kind && QUANT_KINDS[kind]) parts.push(QUANT_KINDS[kind]);
      const adjustment = text(data.adjustment, 16);
      if (adjustment && ADJUSTMENTS[adjustment])
        parts.push(ADJUSTMENTS[adjustment]);
      if (typeof data.evidenceOffset === "number" && data.evidenceOffset > 0)
        parts.push(`第 ${Math.round(data.evidenceOffset)} 页`);
    }
  }

  if (
    stage === "tool.completed" &&
    typeof durationSeconds === "number" &&
    durationSeconds >= 0.1
  ) {
    const value =
      durationSeconds >= 10
        ? `${Math.round(durationSeconds)}`
        : durationSeconds.toFixed(1);
    parts.push(`耗时 ${value} 秒`);
  }

  const detail = parts.filter(Boolean).join(" · ");
  return detail ? detail.slice(0, MAX_DETAIL) : undefined;
}
