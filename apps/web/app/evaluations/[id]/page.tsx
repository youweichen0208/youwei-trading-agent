"use client";
import { use } from "react";
import { Shell } from "../../../components/shell";
import { Evaluations } from "../../../components/evaluations";
export default function Page({params}: {params: Promise<{id: string}>}) { const {id} = use(params); return <Shell>{me => <Evaluations key={id} me={me} id={id}/>}</Shell>; }
