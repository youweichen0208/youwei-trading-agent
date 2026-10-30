"use client";
import { Shell } from "../components/shell";
import { Overview } from "../components/overview";
export default function Home() { return <Shell>{me => <Overview me={me}/>}</Shell>; }
