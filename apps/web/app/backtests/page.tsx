"use client";
import {Suspense} from "react";
import {useSearchParams} from "next/navigation";
import {Shell} from "../../components/shell";
import {Backtests} from "../../components/backtests";
function Content(){const query=useSearchParams(),model=query.get("model_id")??"";return <Shell>{me=><Backtests key={`${me.user.id}:${me.data_mode}:${model}`} me={me} model={model}/>}</Shell>;}
export default function Page(){return <Suspense fallback={<p role="status">正在打开回测目录…</p>}><Content/></Suspense>;}
