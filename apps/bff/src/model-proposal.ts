import { modelProposalPageSchema, type modelProposalQuerySchema, modelProposalResponseSchema, type modelProposalRequestSchema } from "@yta/contracts";
import type { z } from "zod";
import type { Config } from "./config.js";
import { ApiError } from "./errors.js";
import { hashContent } from "./experimental-report.js";

export function readModelProposal(value: unknown, owner: string, mode: Config["YTA_DATA_MODE"],
  expected: {id?: string; key?: string; request?: z.output<typeof modelProposalRequestSchema>}) {
  const parsed = modelProposalResponseSchema.safeParse(value);
  if (parsed.success) {
    const result = parsed.data, proposal = result.proposal, input = expected.request;
    if (proposal.owner_id === owner && proposal.data_mode === mode
      && (!expected.id || result.id === expected.id) && (!expected.key || proposal.request_id === expected.key)
      && (!input || (proposal.candidate_evaluation_id === input.candidate_evaluation_id
        && proposal.reference_evaluation_id === input.reference_evaluation_id && proposal.rationale === input.rationale))
      && hashContent(proposal) === result.content_hash) return result;
  }
  throw new ApiError("UPSTREAM_INVALID", "Model proposal response is invalid", 502);
}

export function readModelProposalPage(value: unknown, owner: string, mode: Config["YTA_DATA_MODE"],
  input: z.output<typeof modelProposalQuerySchema>) {
  const parsed = modelProposalPageSchema.safeParse(value);
  if (!parsed.success || parsed.data.items.length > (input.limit ?? 20)
    || parsed.data.items.some(row => row.id === input.cursor)
    || (input.cursor && parsed.data.next_cursor === input.cursor)) {
    throw new ApiError("UPSTREAM_INVALID", "Model proposal list is invalid", 502);
  }
  for (const item of parsed.data.items) readModelProposal(item, owner, mode, {});
  return parsed.data;
}

// Separate privileged readers: ordinary readers above always bind the session owner.
export function readReviewProposal(value:unknown,mode:Config["YTA_DATA_MODE"],id?:string) {
  const parsed=modelProposalResponseSchema.safeParse(value);
  if(!parsed.success)throw new ApiError("UPSTREAM_INVALID","Review proposal is invalid",502);
  return readModelProposal(parsed.data,parsed.data.proposal.owner_id,mode,{id});
}
export function readReviewProposalPage(value:unknown,mode:Config["YTA_DATA_MODE"],input:z.output<typeof modelProposalQuerySchema>) {
  const parsed=modelProposalPageSchema.safeParse(value);
  if(!parsed.success || parsed.data.items.length>(input.limit ?? 20)
    || parsed.data.items.some(item=>item.id===input.cursor)
    || (input.cursor && parsed.data.next_cursor===input.cursor))
    throw new ApiError("UPSTREAM_INVALID","Review proposal list is invalid",502);
  parsed.data.items.forEach(item=>readReviewProposal(item,mode));
  return parsed.data;
}
