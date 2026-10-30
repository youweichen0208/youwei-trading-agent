"use client";
import { Shell } from "../../../components/shell";
import { ModelComparison } from "../../../components/model-comparison";
export default function Page() {return <Shell>{me => <ModelComparison me={me}/>}</Shell>;}
