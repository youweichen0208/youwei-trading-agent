"use client";
import { Shell } from "../../components/shell";
import { ModelProposalHistory } from "../../components/model-proposal-history";
export default function Page() { return <Shell>{me => <ModelProposalHistory key={`${me.user.id}:${me.data_mode}`} me={me}/>}</Shell>; }
