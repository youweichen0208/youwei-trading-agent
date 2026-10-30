"use client";
import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { Shell } from "../../components/shell";
import { ResearchWorkspace } from "../../components/research-workspace";
function Workspace() {
  const correctionId = useSearchParams().get("correct") || undefined;
  return <Shell>{me => <ResearchWorkspace key={correctionId ?? "new"} me={me} correctionId={correctionId}/>}</Shell>;
}
export default function Research() { return <Suspense fallback={<p role="status">正在打开研究工作台…</p>}><Workspace/></Suspense>; }
