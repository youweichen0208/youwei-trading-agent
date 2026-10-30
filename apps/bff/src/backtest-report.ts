import {z} from "zod";
import {backtestPageSchema,backtestQuerySchema,backtestResponseSchema} from "@yta/contracts";
import type {Config} from "./config.js";
import {ApiError} from "./errors.js";
import {hashContent} from "./experimental-report.js";
function validHash(value:{content_hash:string}) {
  const {content_hash,...body}=value;
  return hashContent(body)===content_hash;
}
export function readBacktestReport(value:unknown,mode:Config["YTA_DATA_MODE"],id:string) {
  const parsed=backtestResponseSchema.safeParse(value);
  if(parsed.success && parsed.data.id===id && parsed.data.report.data_mode===mode) {
    const report=parsed.data.report;
    if(validHash(report) && report.signal_sources.every(validHash)
      && report.rows.every(row=>row.episode===null || validHash(row.episode)))return parsed.data;
  }
  // Alpha owns arithmetic and full private-input replay; this boundary checks the wire evidence.
  throw new ApiError("UPSTREAM_INVALID","Backtest report response is invalid",502);
}

export function readBacktestPage(value:unknown,mode:Config["YTA_DATA_MODE"],query:z.output<typeof backtestQuerySchema>) {
  const parsed=backtestPageSchema.safeParse(value);
  if(parsed.success && parsed.data.items.length<=(query.limit??20) && parsed.data.items.every(row=>row.data_mode===mode
    && row.id!==query.cursor && (!query.model_id || row.model_ids.includes(query.model_id)) && (!query.status || row.status===query.status))
    && (!query.cursor || parsed.data.next_cursor!==query.cursor))return parsed.data;
  throw new ApiError("UPSTREAM_INVALID","Backtest catalog response is invalid",502);
}
