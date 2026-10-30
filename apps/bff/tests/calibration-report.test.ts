import {readFileSync} from "node:fs";
import {it,expect} from "vitest";
import {readCalibrationReport} from "../src/calibration-report.js";
import {hashContent} from "../src/experimental-report.js";
const saved=JSON.parse(readFileSync(new URL("../../../packages/contracts/fixtures/calibration-report.json",import.meta.url),"utf8"));
it("preserves the exact registered probability report and its identity",()=>{
  expect(readCalibrationReport(saved,"REPLAY",saved.id)).toEqual(saved);
});
it("rejects changed hashes, mode, identity, authority and malformed probability evidence",()=>{
  for(const change of [{qualification:"APPROVED"},{data_mode:"LIVE_RESEARCH"},
    {probabilities:["1.0000000000000000000000000001",...saved.report.probabilities.slice(1)]},
    {calibration_ids:saved.report.evaluation_ids},{evaluation_targets:[]}]) {
    const report={...saved.report,...change};
    expect(()=>readCalibrationReport({...saved,report,report_hash:hashContent(report)},"REPLAY",saved.id)).toThrow();
  }
  expect(()=>readCalibrationReport({...saved,report_hash:"0".repeat(64)},"REPLAY",saved.id)).toThrow();
  expect(()=>readCalibrationReport({...saved,private:"secret"},"REPLAY",saved.id)).toThrow();
  expect(()=>readCalibrationReport(saved,"REPLAY","calibration-experimental-"+"0".repeat(64))).toThrow();
});
