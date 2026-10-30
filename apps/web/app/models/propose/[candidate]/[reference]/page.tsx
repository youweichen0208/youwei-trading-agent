"use client";
import { use } from "react";
import { Shell } from "../../../../../components/shell";
import { ModelProposal } from "../../../../../components/model-proposal";
export default function Page({params}: {params: Promise<{candidate: string; reference: string}>}) {
  const {candidate,reference}=use(params);
  return <Shell>{me=><ModelProposal key={candidate+reference} me={me} candidateId={candidate} referenceId={reference}/>}</Shell>;
}
