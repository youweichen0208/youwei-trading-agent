"use client";
import {useEffect,useState} from 'react';
export function ResearchStatus({taskId,status,connectionError=false,currentAction}:{taskId:string;status:string;connectionError?:boolean;currentAction?:string}) {
 const [seconds,setSeconds]=useState(0);
 useEffect(()=>{const started=Date.now();setSeconds(0);const timer=setInterval(()=>setSeconds(Math.floor((Date.now()-started)/1000)),1000);return()=>clearInterval(timer);},[taskId]);
 const running=status==='running'&&!connectionError;
 const label=connectionError?'连接异常，正在恢复':status==='queued'?'排队中':status==='stopping'?'正在停止':status==='unknown'?'正在核对执行状态':(currentAction ?? '等待 Agent 返回进度');
 return <span className="research-status"><span className="research-status-dot" data-running={running} aria-hidden="true"/><span role="status">{label}</span><small aria-live="off">已用时 {seconds<60?`${seconds} 秒`:`${Math.floor(seconds/60)} 分 ${seconds%60} 秒`}</small></span>;
}
