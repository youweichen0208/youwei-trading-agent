"use client";
import { use } from "react";
import { Shell } from "../../../components/shell";
import { ModelQuality } from "../../../components/model-quality";
export default function Page({params}: {params: Promise<{id: string}>}) {
  const {id}=use(params);
  return <Shell>{me=><ModelQuality key={me.user.id+me.data_mode+id} me={me} assessmentId={id}/>}</Shell>;
}
