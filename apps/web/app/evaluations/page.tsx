"use client";
import { Shell } from "../../components/shell";
import { Evaluations } from "../../components/evaluations";
export default function Page() { return <Shell>{me => <Evaluations me={me}/>}</Shell>; }
