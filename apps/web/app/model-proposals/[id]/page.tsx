"use client";
import { use } from "react";
import { Shell } from "../../../components/shell";
import { ModelProposal } from "../../../components/model-proposal";
export default function Page({params}: {params: Promise<{id: string}>}) {
  const {id}=use(params);
  return <Shell>{me=><ModelProposal key={id} me={me} proposalId={id}/>}</Shell>;
}
