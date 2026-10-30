"use client";
import { use } from "react";
import { Shell } from "../../../../components/shell";
import { ModelEvaluations } from "../../../../components/model-evaluations";
export default function Page({params}: {params: Promise<{id: string}>}) {
  const {id} = use(params);
  return <Shell>{me => <ModelEvaluations key={id} me={me} modelId={id}/>}</Shell>;
}
