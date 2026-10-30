"use client";
import { Shell } from "../../components/shell";
import { Models } from "../../components/models";
export default function Page() { return <Shell>{me => <Models me={me}/>}</Shell>; }
