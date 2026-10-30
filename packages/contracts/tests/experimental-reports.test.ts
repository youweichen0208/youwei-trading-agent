import { readFileSync } from "node:fs";
import { expect, it } from "vitest";
import { experimentalPriceReportSchema } from "../src/index.js";

const report = JSON.parse(readFileSync(new URL("../fixtures/experimental-price-report.json", import.meta.url), "utf8"));
it("accepts registered Python report transport with exact manifest membership", () => {
  expect(experimentalPriceReportSchema.parse(report)).toEqual(report);
});
it("rejects inconsistent cohort, counts, baseline availability and qualification", () => {
  for (const value of [
    {...report, qualification: "VERIFIED"},
    {...report, cohort: {...report.cohort, label_ids: [...report.cohort.label_ids].reverse()}},
    {...report, metrics: {...report.metrics, model: {...report.metrics.model, sample_count: 999}}},
    {...report, metrics: {...report.metrics, model: {...report.metrics.model, mae: 0.1}}},
    {...report, metrics: {...report.metrics, model: {...report.metrics.model, direction_accuracy: "1.0001"}}},
    {...report, metrics: {...report.metrics, "momentum20-v1": null}},
    {...report, manifest: [...report.manifest, report.manifest[0]]},
  ]) expect(experimentalPriceReportSchema.safeParse(value).success).toBe(false);
});

it("accepts report pages and rejects duplicate identities or a cursor outside the page", async () => {
  const {experimentalEvaluationPageSchema} = await import("../src/index.js");
  const page = JSON.parse(readFileSync(new URL("../fixtures/experimental-evaluation-page.json", import.meta.url), "utf8"));
  expect(experimentalEvaluationPageSchema.parse(page)).toEqual(page);
  expect(experimentalEvaluationPageSchema.safeParse({items: [page.items[0], page.items[0]], next_cursor: null}).success).toBe(false);
  expect(experimentalEvaluationPageSchema.safeParse({items: [], next_cursor: page.items[0].id}).success).toBe(false);
});


it("accepts registered comparisons and rejects duplicate or mismatched report selections", async () => {
  const {experimentalReportComparisonSchema, experimentalComparisonRequestSchema} = await import("../src/index.js");
  const comparison = JSON.parse(readFileSync(new URL("../fixtures/experimental-report-comparison.json", import.meta.url), "utf8"));
  expect(experimentalReportComparisonSchema.parse(comparison)).toEqual(comparison);
  expect(experimentalComparisonRequestSchema.parse({evaluation_ids: comparison.evaluation_ids})).toEqual({evaluation_ids: comparison.evaluation_ids});
  for (const ids of [[], comparison.evaluation_ids.slice(0, 1), Array(11).fill(comparison.evaluation_ids[0]), [comparison.evaluation_ids[0], comparison.evaluation_ids[0]]]) {
    expect(experimentalComparisonRequestSchema.safeParse({evaluation_ids: ids}).success).toBe(false);
  }
  expect(experimentalComparisonRequestSchema.safeParse({evaluation_ids: comparison.evaluation_ids, data_mode: "REPLAY"}).success).toBe(false);
  expect(experimentalReportComparisonSchema.safeParse({...comparison, reports: comparison.reports.slice(0, 1)}).success).toBe(false);
});

it("accepts immutable proposal transport and constrains caller input", async () => {
  const {modelProposalRequestSchema,modelProposalResponseSchema,modelProposalKeySchema} = await import("../src/index.js");
  const saved=JSON.parse(readFileSync(new URL("../fixtures/model-proposal.json",import.meta.url),"utf8"));
  expect(modelProposalResponseSchema.parse(saved)).toEqual(saved);
  const input={candidate_evaluation_id:saved.proposal.candidate_evaluation_id,rationale:"复核 🧪"};
  expect(modelProposalRequestSchema.parse(input).reference_evaluation_id).toBeNull();
  expect(modelProposalRequestSchema.safeParse({...input,rationale:"🧪".repeat(2000)}).success).toBe(true);
  for(const rationale of ["", "   ", "🧪".repeat(2001)]) expect(modelProposalRequestSchema.safeParse({...input,rationale}).success).toBe(false);
  expect(modelProposalRequestSchema.safeParse({...input,owner_id:saved.proposal.owner_id}).success).toBe(false);
  expect(modelProposalKeySchema.parse(saved.proposal.request_id.toUpperCase())).toBe(saved.proposal.request_id);
  expect(modelProposalKeySchema.safeParse("arbitrary-key").success).toBe(false);
});

it("constrains proposal history queries and page identities", async () => {
  const {modelProposalQuerySchema,modelProposalPageSchema}=await import("../src/index.js");
  const saved=JSON.parse(readFileSync(new URL("../fixtures/model-proposal.json",import.meta.url),"utf8"));
  expect(modelProposalQuerySchema.parse({cursor:saved.id.toUpperCase(),limit:"50"})).toEqual({cursor:saved.id,limit:50});
  for(const query of [{limit:"51"},{limit:"0"},{limit:1},{limit:["1"]},{owner_id:saved.proposal.owner_id},{data_mode:"REPLAY"},{cursor:"bad"}]) {
    expect(modelProposalQuerySchema.safeParse(query).success).toBe(false);
  }
  expect(modelProposalPageSchema.parse({items:[saved],next_cursor:saved.id}).items).toEqual([saved]);
  for(const page of [{items:[saved,saved],next_cursor:null},{items:[],next_cursor:saved.id},{items:[saved],next_cursor:saved.proposal.owner_id}]) {
    expect(modelProposalPageSchema.safeParse(page).success).toBe(false);
  }
});
