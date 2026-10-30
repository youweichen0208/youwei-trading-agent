import {calibrationReportResponseSchema} from "@yta/contracts";
import type {Config} from "./config.js";
import {ApiError} from "./errors.js";
import {hashContent} from "./experimental-report.js";
export function readCalibrationReport(value:unknown,mode:Config["YTA_DATA_MODE"],id:string) {
  const parsed=calibrationReportResponseSchema.safeParse(value);
  if(parsed.success && parsed.data.id===id && parsed.data.report.data_mode===mode
    && hashContent(parsed.data.report)===parsed.data.report_hash)return parsed.data;
  throw new ApiError("UPSTREAM_INVALID","Calibration report response is invalid",502);
}
