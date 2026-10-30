"use client";
import { Shell } from "../../components/shell";
import { Predictions } from "../../components/predictions";
export default function Page() { return <Shell>{me => <Predictions me={me}/>}</Shell>; }
