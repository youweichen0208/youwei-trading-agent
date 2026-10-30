"use client";
import Link from "next/link";
import {useEffect,useState} from "react";
import {useRouter} from "next/navigation";
import {experimentalModelIdSchema,trainingEvidenceIdSchema,trainingEvidencePageSchema,trainingEvidenceResponseSchema,type TrainingEvidencePage,type TrainingEvidenceResponse} from "@yta/contracts";
import {api,dateTime,HttpError,type Me} from "../lib/api";

export function TrainingEvidence({me,modelId,id}:{me:Me;modelId?:string;id?:string}) {
  const router=useRouter(),[trail,setTrail]=useState<string[]>([]),[page,setPage]=useState<TrainingEvidencePage>(),[detail,setDetail]=useState<TrainingEvidenceResponse>();
  const [pending,setPending]=useState(false),[error,setError]=useState(""),[reload,setReload]=useState(0);
  const cursor=trail.at(-1);
  useEffect(()=>{
    if(!me.experimental_models_enabled)return;
    const abort=new AbortController();setPage(undefined);setDetail(undefined);setPending(true);setError("");
    void (async()=>{
      if(id){
        const identity=trainingEvidenceIdSchema.parse(id);
        const value=trainingEvidenceResponseSchema.parse(await api<unknown>("/training-evidence/"+identity,{signal:abort.signal}));
        if(value.id!==identity || value.data_mode!==me.data_mode)throw new Error("Evidence scope differs");
        if(!abort.signal.aborted)setDetail(value);
      }else{
        const model=experimentalModelIdSchema.parse(modelId);
        const params=new URLSearchParams({limit:"10"});if(cursor)params.set("cursor",cursor);
        const value=trainingEvidencePageSchema.parse(await api<unknown>(`/experimental-models/${model}/training-evidence?${params}`,{signal:abort.signal}));
        if(value.items.length>10 || value.items.some(r=>r.model_id!==model || r.data_mode!==me.data_mode || r.id===cursor) || (cursor&&value.next_cursor===cursor))throw new Error("Evidence catalog scope differs");
        if(!abort.signal.aborted)setPage(value);
      }
    })().catch(e=>{
      if(abort.signal.aborted)return;
      if(e instanceof HttpError && e.status===401){router.replace("/login");return;}
      setError(e instanceof HttpError&&e.status===404?"当前模式中找不到这份训练证据或模型。":id?"暂时无法重新训练核对，请重新加载。不会显示未经本次核对的旧结果。":"训练证据目录暂时无法读取，请重新加载。");
    }).finally(()=>{if(!abort.signal.aborted)setPending(false);});
    return()=>abort.abort();
  },[id,modelId,cursor,reload,me.data_mode,me.experimental_models_enabled,router]);
  const report=detail?.report;
  return <main className="records-page experimental-reports-page">
    <div className="records-heading"><div><p className="eyebrow">固定训练输入 / 复现核对</p><h1>{id?"训练核对详情":"模型训练证据"}</h1></div><Link href={detail?`/models/${detail.model_id}/training-evidence`:"/models"}>{detail?"← 训练证据目录":"← 实验模型"}</Link></div>
    {!me.experimental_models_enabled?<section className="empty-state"><h2>训练证据尚未启用</h2></section>:<>
      <aside className="model-scope"><span className="badge">UNVERIFIED</span><div><h2>训练可复现，仍需核验来源资格</h2><p>{id?"打开详情会用固定原始输入重新训练，并核对登记结果。":"目录展示保存时的核对结论，只检查已存记录；打开详情才重新训练。"}一致性不代表数据授权、历史可用时间资格或模型晋升批准。</p></div></aside>
      {modelId&&<p className="hash">模型：{modelId}</p>}
      {pending&&<p role="status">{id?"正在从固定输入重新训练核对…":"正在读取训练证据…"}</p>}
      {error&&<div role="alert" className="alert">{error}<button onClick={()=>setReload(n=>n+1)}>重新加载</button></div>}
      {page&&<>
        {!page.items.length?<section className="empty-state"><h2>暂无训练证据</h2><p>当前模式下，此模型尚未登记原始训练核对记录。</p></section>:page.items.map(r=><article className="evidence-card" key={r.id}>
          <div className="section-heading"><h2>核对 {r.content_hash.slice(0,12)}</h2><span className="badge">{r.status==="MATCHED"?"保存时全部一致":"保存时存在差异"}</span></div>
          <p className="muted">登记于 {dateTime(r.registered_at)} 美东 · {r.data_mode}</p><p><Link className="button-link" href={"/training-evidence/"+r.id}>重新训练并查看详情 →</Link></p>
          <details><summary>核对固定身份</summary><dl className="facts"><dt>证据 ID</dt><dd className="hash">{r.id}</dd><dt>训练输入哈希</dt><dd className="hash">{r.input_hash}</dd></dl></details>
        </article>)}
        <div className="model-pagination"><button disabled={pending||!trail.length} onClick={()=>setTrail(t=>t.slice(0,-1))}>上一页</button><span>第 {trail.length+1} 页</span><button disabled={pending||!page.next_cursor} onClick={()=>{if(page.next_cursor)setTrail(t=>[...t,page.next_cursor!]);}}>下一页</button></div>
      </>}
      {detail&&report&&<>
        <section className="evidence-card"><h2>{report.status==="MATCHED"?"本次重训与原模型全部一致":"本次重训复现了已登记的差异"}</h2><p>{report.matched_fields.length} 个字段一致 · {report.mismatched_fields.length} 个字段存在差异 · {detail.data_mode}</p>
          <h3>差异字段</h3>{report.mismatched_fields.length?<ul>{report.mismatched_fields.map(field=><li key={field}><code>{field}</code></li>)}</ul>:<p>无差异</p>}
          <details><summary>查看全部一致字段</summary><ul>{report.matched_fields.map(field=><li key={field}><code>{field}</code></li>)}</ul></details>
        </section>
        <section className="evidence-card"><h2>固定输入与模型绑定</h2><dl className="facts"><dt>证据 ID</dt><dd className="hash">{detail.id}</dd><dt>模型 ID</dt><dd className="hash"><Link href={`/models/${detail.model_id}/training-evidence`}>{detail.model_id}</Link></dd><dt>训练输入哈希</dt><dd className="hash">{detail.input_hash}</dd><dt>原制品文件 SHA256</dt><dd className="hash">{detail.artifact_file_sha256}</dd><dt>原模型 SHA256</dt><dd className="hash">{report.expected_model_sha256}</dd><dt>重训模型 SHA256</dt><dd className="hash">{report.replayed_model_sha256}</dd><dt>登记内容哈希</dt><dd className="hash">{detail.content_hash}</dd><dt>核对报告哈希</dt><dd className="hash">{report.content_hash}</dd></dl><details><summary>查看原模型与重训源码哈希</summary><pre className="report-manifest">{JSON.stringify({expected:report.expected_source_hashes,replayed:report.replayed_source_hashes},null,2)}</pre></details><p className="muted small">原始行情、标签、特征与模型文本保留在私有服务中。输入哈希不能单独重建完整登记身份。审核流程尚未引用本记录。</p></section>
      </>}
    </>}
  </main>;
}
