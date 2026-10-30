"use client";
import { use } from "react";
import { Shell } from "../../../../../components/shell";
import { ModelQuality } from "../../../../../components/model-quality";
export default function Page({params}: {params: Promise<{candidate: string; reference: string}>}) {
  const {candidate,reference}=use(params);
  return <Shell>{me=><ModelQuality key={me.user.id+me.data_mode+candidate+reference} me={me} candidateId={candidate} referenceId={reference}/>}</Shell>;
}
