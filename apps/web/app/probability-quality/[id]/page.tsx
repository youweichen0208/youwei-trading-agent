"use client";
import { use } from "react";
import { Shell } from "../../../components/shell";
import { ProbabilityQuality } from "../../../components/probability-quality";
export default function Page({params}: {params: Promise<{id: string}>}) {
  const {id}=use(params);
  return <Shell>{me=><ProbabilityQuality key={me.user.id+me.data_mode+id} me={me} assessmentId={id}/>}</Shell>;
}
