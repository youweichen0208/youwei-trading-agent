import { researchReportSchema, type DataMode, type Forecast, type ResearchReport } from "@yta/contracts";

export type ReportDraft = { draft_id: string; forecast: Forecast };
export const reportError = "研究报告与当前草稿或数据模式不一致，请重新打开会话核对。";

// Display boundary only. BFF verifies provenance against actual tool responses.
export function reportForDisplay(value: unknown, mode: DataMode, draft?: ReportDraft): ResearchReport | undefined {
  if (value === undefined || value === null) return undefined; // Older runs have no report.
  const parsed = researchReportSchema.safeParse(value);
  if (!parsed.success || parsed.data.data_mode !== mode) throw new Error(reportError);
  const report = parsed.data;
  if (report.status === "DRAFT_READY") {
    if (!draft || report.draft_id !== draft.draft_id || report.as_of !== draft.forecast.as_of
      || report.data_mode !== draft.forecast.data_mode
      || report.evidence.some(item => item.data_snapshot_hash !== draft.forecast.data_snapshot_hash)) throw new Error(reportError);
  } else if (draft) throw new Error(reportError);
  return report;
}
