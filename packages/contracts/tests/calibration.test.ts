import {readFileSync} from "node:fs";
import {it,expect} from "vitest";
import {calibrationReportResponseSchema} from "../src/calibration.js";
const saved=JSON.parse(readFileSync(new URL("../fixtures/calibration-report.json",import.meta.url),"utf8"));
it("keeps Decimal spellings, exact ranges, empty bins and window alignment",()=>{
  expect(calibrationReportResponseSchema.parse(saved)).toEqual(saved);
  for(const value of ["0E-12","-0E+4","1E-20","0.9999999999999999999999999999","1.000"]){
    expect(calibrationReportResponseSchema.safeParse({...saved,report:{...saved.report,probabilities:[value,...saved.report.probabilities.slice(1)]}}).success).toBe(true);
  }
  for(const value of ["1.0000000000000000000000001","-1E-20","2e0","1e1","NaN","1eBAD",0.5]){
    expect(calibrationReportResponseSchema.safeParse({...saved,report:{...saved.report,probabilities:[value,...saved.report.probabilities.slice(1)]}}).success).toBe(false);
  }
  const report=structuredClone(saved.report);report.reliability.bins[0].count=1000;
  expect(calibrationReportResponseSchema.safeParse({...saved,report}).success).toBe(false);
});
