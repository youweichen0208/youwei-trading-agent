"use client";
import { use } from "react";
import { Shell } from "../../../components/shell";
import { Predictions } from "../../../components/predictions";
export default function Page({ params }: { params: Promise<{id: string}> }) { const { id } = use(params); return <Shell>{me => <Predictions me={me} id={id}/>}</Shell>; }
