"use client";
import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { agentEventSchema, forecastSchema, resourceIdSchema, type ResearchReport, type Forecast } from "@yta/contracts";
import { api, HttpError, dateTime, percent, type Me } from "../lib/api";
import { StockChart } from "./chart";
import { ForecastFacts } from "./forecast";
import { ResearchReportDetails } from "./research-report";
import { reportStreamSummary } from "../lib/report-stream";
import { reportForDisplay, reportError } from "../lib/research-report";

type Session = { session_id: string; created_at: string };
type SessionPage = { items: Session[]; next_cursor: string | null };
type Message = { id: string; run_id: string; role: string; content: string; partial: boolean };
type Draft = { draft_id: string; forecast: Forecast };
const eventNames = ["run.started", "message.delta", "message.completed", "tool.started", "tool.completed", "canvas.command", "prediction.draft", "run.completed", "run.canceled", "run.failed", "run.execution_unknown"];
const terminal = ["COMPLETED", "CANCELED", "FAILED", "EXECUTION_UNKNOWN"];

export function ResearchWorkspace({ me, correctionId }: { me: Me; correctionId?: string }) {
  const [original, setOriginal] = useState<{prediction_id: string; forecast: Forecast}>();
  const [correctionError, setCorrectionError] = useState("");
  useEffect(() => {
    if (!correctionId) return;
    const abort = new AbortController();
    void api<{prediction_id: string; forecast: Forecast}>("/predictions/" + encodeURIComponent(correctionId), {signal: abort.signal})
      .then(setOriginal).catch(e => { if (!abort.signal.aborted) setCorrectionError(e.message); });
    return () => abort.abort();
  }, [correctionId]);
  const [sessions, setSessions] = useState<Session[]>([]), [selected, setSelected] = useState("");
  const [sessionCursor, setSessionCursor] = useState<string | null>(null);
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [messages, setMessages] = useState<Message[]>([]), [run, setRun] = useState("");
  const [status, setStatus] = useState(""), [text, setText] = useState("");
  const [draft, setDraft] = useState<Draft>(), [frozen, setFrozen] = useState("");
  const [report, setReport] = useState<ResearchReport>();
  const [error, setError] = useState(""), [pending, setPending] = useState(false);
  const [command, setCommand] = useState<string>(), [tool, setTool] = useState("");
  const [tab, setTab] = useState("chat"), [connected, setConnected] = useState(true);
  const dialog = useRef<HTMLDialogElement>(null);
  const admission = useRef<{ session: string; text: string; key: string } | null>(null);
  const freezeKey = useRef<{ draft: string; target?: string; key: string } | null>(null);
  const selection = useRef(0);
  const busy = !!run && !terminal.includes(status);
  const correctionMatches = !correctionId || !!(original && draft
    && original.forecast.base_session === draft.forecast.base_session
    && original.forecast.target_session === draft.forecast.target_session
    && original.forecast.data_mode === draft.forecast.data_mode);

  useEffect(() => {
    const abort = new AbortController();
    void api<SessionPage>("/sessions", {signal: abort.signal}).then(page => {
      setSessions(old => [...old, ...page.items.filter(item => !old.some(s => s.session_id === item.session_id))]);
      setSessionCursor(page.next_cursor);
    }).catch(e => { if (!abort.signal.aborted) setError(e.message); })
      .finally(() => { if (!abort.signal.aborted) setLoadingSessions(false); });
    return () => abort.abort();
  }, []);
  async function olderSessions() {
    if (!sessionCursor || loadingSessions) return;
    setLoadingSessions(true); setError("");
    try {
      const page = await api<SessionPage>("/sessions?cursor=" + encodeURIComponent(sessionCursor));
      setSessions(old => [...old, ...page.items.filter(item => !old.some(s => s.session_id === item.session_id))]);
      setSessionCursor(page.next_cursor);
    } catch (e) { setError((e as Error).message); }
    finally { setLoadingSessions(false); }
  }
  async function openSession(id: string) {
    const generation = ++selection.current;
    setSelected(id); setRun(""); setMessages([]); setDraft(undefined); setReport(undefined); setFrozen(""); setCommand(undefined); setError(""); setStatus(""); setTool("");
    try {
      let cursor: string | null = null; const history: Message[] = [];
      do {
        const page: {items: Message[]; next_cursor: string | null} = await api(`/sessions/${id}/messages${cursor ? `?cursor=${cursor}` : ""}`);
        history.push(...page.items); cursor = page.next_cursor;
      } while (cursor && generation === selection.current);
      if (generation !== selection.current) return;
      setMessages(history);
      const latest = history.at(-1)?.run_id;
      if (latest) {
        const summary = await api<{run_id: string; session_id: string; data_mode: string; status: string; draft: Draft | null; report?: unknown}>(`/runs/${latest}`);
        if (generation !== selection.current) return;
        if (summary.run_id !== latest || summary.session_id !== id || summary.data_mode !== me.data_mode) throw new Error(reportError);
        let checkedDraft: Draft | undefined;
        if (summary.draft) {
          const id = resourceIdSchema.safeParse(summary.draft.draft_id), value = forecastSchema.safeParse(summary.draft.forecast);
          if (!id.success || !value.success || value.data.data_mode !== me.data_mode) throw new Error(reportError);
          checkedDraft = { draft_id: id.data, forecast: value.data };
        }
        const checkedReport = reportForDisplay(summary.report, me.data_mode, checkedDraft);
        setStatus(summary.status); setDraft(checkedDraft); setReport(checkedReport); setRun(latest);
      }
    } catch (e) { if (generation === selection.current) setError((e as Error).message); }
  }
  useEffect(() => {
    if (!run || !selected) return;
    // A terminal run already has its final history and report. Replaying its
    // stream would overwrite that text and temporarily restore RUNNING state.
    if (["COMPLETED", "CANCELED", "FAILED", "EXECUTION_UNKNOWN"].includes(status)) {
      setConnected(true);
      return;
    }
    const source = new EventSource(`/api/yta/v1/runs/${run}/events`);
    const seen = new Set<string>(); let streamed = ""; let streamDraft = draft;
    const rejectReport = () => { setDraft(undefined); setReport(undefined); dialog.current?.close(); setError(reportError); source.close(); };
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    const receive = (message: MessageEvent) => {
      let json; try { json = JSON.parse(message.data); } catch { rejectReport(); return; }
      const parsed = agentEventSchema.safeParse(json);
      if (!parsed.success) { rejectReport(); return; }
      const e = parsed.data;
      if (e.run_id !== run || e.session_id !== selected || seen.has(e.event_id)) return;
      seen.add(e.event_id);
      if (e.type === "message.completed" && e.payload.report) {
        try { setReport(reportForDisplay(e.payload.report, me.data_mode, streamDraft)); }
        catch { rejectReport(); return; }
      }
      if (e.type === "message.delta" || e.type === "message.completed") {
        streamed = e.type === "message.delta" ? streamed + e.payload.text : e.payload.text;
        const content = e.type === "message.delta" && me.runtime === "hermes"
          ? reportStreamSummary(streamed) : streamed;
        setMessages(old => [...old.filter(m => !(m.run_id === run && m.role === "assistant")), { id: run, run_id: run, role: "assistant", content, partial: e.type === "message.delta" }]);
      }
      if (e.type.startsWith("run.")) setStatus(e.type === "run.started" ? "RUNNING" : e.type.slice(4).toUpperCase());
      if (e.type === "tool.started") setTool(`${e.payload.tool} · 正在读取`);
      if (e.type === "tool.completed") setTool(`${e.payload.tool} · ${e.payload.success ? "完成" : "失败"}`);
      if (e.type === "canvas.command" && e.payload.session_id === selected && e.payload.run_id === run) setCommand(e.payload.type === "chart.show" ? e.payload.command_id : undefined);
      if (e.type === "prediction.draft") {
        if (e.payload.forecast.data_mode !== me.data_mode) { rejectReport(); return; }
        streamDraft = e.payload; setDraft(e.payload);
      }
      if (e.type === "run.failed" || e.type === "run.execution_unknown") setError(`${e.payload.code}：${e.payload.message}`);
      if (["run.completed", "run.canceled", "run.failed", "run.execution_unknown"].includes(e.type)) { source.close(); setConnected(true); }
    };
    for (const name of eventNames) source.addEventListener(name, receive as EventListener);
    return () => source.close();
  }, [run, selected]);

  async function newSession() {
    const created = await api<Session>("/sessions", { body: { profile_id: "alpha-orchestrator" }, csrf: me.csrf_token, key: crypto.randomUUID() });
    setSessions(old => [created, ...old]); await openSession(created.session_id); return created.session_id;
  }
  async function send(event: FormEvent) {
    event.preventDefault(); if (!text.trim() || pending || busy || status === "EXECUTION_UNKNOWN") return;
    setPending(true); setError("");
    try {
      const session = selected || await newSession();
      const request = admission.current ?? { session, text: text.trim(), key: crypto.randomUUID() };
      admission.current = request;
      const result = await api<{run_id: string}>(`/sessions/${request.session}/messages`, { body: { text: request.text, request_id: request.key }, csrf: me.csrf_token, key: request.key });
      setMessages(old => [...old.filter(m => !(m.run_id === result.run_id && m.role === "user")), { id: request.key, run_id: result.run_id, role: "user", content: request.text, partial: false }]);
      admission.current = null; setText(""); setDraft(undefined); setReport(undefined); setFrozen(""); setStatus("QUEUED"); setRun(result.run_id);
    } catch (e) {
      if (e instanceof HttpError && e.status === 429 && e.code === "RESEARCH_QUOTA_EXCEEDED") {
        // This response proves that no admission was created. Network failures do not.
        admission.current = null;
        setError(e.message);
      } else setError(`${(e as Error).message} 再次发送会使用原请求标识核对。`);
    }
    finally { setPending(false); }
  }
  async function freeze() {
    if (!draft || pending || !correctionMatches) return;
    setPending(true); setError("");
    if (freezeKey.current?.draft !== draft.draft_id || freezeKey.current?.target !== correctionId) freezeKey.current = { draft: draft.draft_id, target: correctionId, key: crypto.randomUUID() };
    try { const value = await api<{prediction_id: string}>(correctionId ? "/predictions/" + encodeURIComponent(correctionId) + "/corrections" : "/predictions", { body: { draft_id: draft.draft_id, confirmed: true }, csrf: me.csrf_token, key: freezeKey.current.key }); setFrozen(value.prediction_id); dialog.current?.close(); }
    catch (e) { setError((e as Error).message); }
    finally { setPending(false); }
  }
  return <main className="workspace">
    <aside className="session-sidebar"><div className="sidebar-heading"><span className="eyebrow">研究会话</span><button disabled={pending || !!admission.current} onClick={() => { setPending(true); void newSession().catch(e => setError(e.message)).finally(() => setPending(false)); }}>＋ 新建</button></div><p className="profile-label">● alpha-orchestrator</p><div className="session-list">{sessions.map((s) => <button disabled={pending || !!admission.current} key={s.session_id} className={selected === s.session_id ? "selected" : ""} onClick={() => void openSession(s.session_id)}><strong>NVDA 研究</strong><span>{dateTime(s.created_at)}</span></button>)}</div>{sessionCursor && <button disabled={loadingSessions} onClick={() => void olderSessions()}>{loadingSessions ? "正在加载会话…" : "加载更早会话"}</button>}<p className="sidebar-note">每个判断保留来源，<br/>每条预测等待验证。</p></aside>
    <div className="mobile-tabs"><button className={tab === "chat" ? "selected" : ""} onClick={() => setTab("chat")}>研究对话</button><button className={tab === "canvas" ? "selected" : ""} onClick={() => setTab("canvas")}>图表与预测{draft ? " · 1" : ""}</button></div>
    <section className={`chat-panel mobile-${tab === "chat" ? "visible" : "hidden"}`}><div className="panel-heading"><div><span className="eyebrow">研究工作台</span><h1>NVDA · 价格研究</h1></div><span className="chip">5 交易日</span></div>
      <div className="messages" aria-live="polite">{correctionId && <div className="tool-status"><strong>正在研究预测更正</strong>{original ? <p>原预测：{original.forecast.base_session} → {original.forecast.target_session}，{percent(original.forecast.expected_return)}。<Link href={"/predictions/" + original.prediction_id}>查看原记录</Link></p> : <p>{correctionError || "正在核对原预测…"}</p>}<p>需生成新草稿并再次确认；原记录保留参与统计。</p></div>}{!messages.length && <div className="chat-welcome"><span className="eyebrow">从一个可验证的问题开始</span><h2>先看证据，<br/>再留下判断。</h2><p>读取日线与技术指标，生成 5 交易日规则基线。检查草稿后，由你决定是否冻结。</p><button onClick={() => setText("查看 NVDA 日线与指标，生成未来 5 个交易日的基线预测。")}>研究 NVDA 的 5 日基线 ↗</button></div>}{messages.map(m => <article className={`message ${m.role}`} key={`${m.run_id}-${m.role}`}><span className="eyebrow">{m.role === "user" ? "你" : "研究协调员"}</span><p>{m.content}</p>{m.partial && terminal.includes(status) && <small>已保存的部分回答</small>}</article>)}{report && <ResearchReportDetails key={run} report={report}/>} {tool && <p className="tool-status">{tool}</p>}{status && <p className="muted small" role="status">{({COMPLETED: "研究已完成", CANCELED: "研究已停止", EXECUTION_UNKNOWN: "执行结果未知，请核对记录；本会话不能自动重发。", FAILED: "研究失败", RUNNING: "正在研究…", QUEUED: "等待研究…"} as Record<string,string>)[status]}</p>}{!connected && <p role="status">连接中断，正在恢复事件流。不会重新执行研究。</p>}</div>
      {error && <p className="alert" role="alert">{error}</p>}<form className="composer" onSubmit={send}><label className="sr-only" htmlFor="prompt">研究问题</label><textarea id="prompt" maxLength={8000} value={text} onChange={e => setText(e.target.value)} placeholder="研究 NVDA 的价格与指标…" disabled={pending || !!admission.current}/><div><span className="muted small">{me.data_mode === "REPLAY" ? (me.runtime === "hermes" ? "模型研究 · 合成回放数据" : "固定回放流程 · 合成数据") : "研究结果需自行核验"}</span>{busy ? <button type="button" onClick={() => void api(`/runs/${run}/cancel`, { body: {}, csrf: me.csrf_token, key: crypto.randomUUID() }).catch(e => setError(e.message))}>停止研究</button> : <button className="primary" disabled={pending || !text.trim() || status === "EXECUTION_UNKNOWN"}>{pending ? "正在发送…" : admission.current ? "核对原请求" : "发送 →"}</button>}</div></form>
    </section>
    <section className={`canvas-panel mobile-${tab === "canvas" ? "visible" : "hidden"}`}><StockChart commandId={command}/><section className="forecast-card"><div className="section-heading"><h2>预测草稿</h2><span className="chip">{frozen ? "已冻结" : "待确认"}</span></div>{draft ? <><ForecastFacts forecast={draft.forecast}/>{correctionId && !correctionMatches && <p className="alert" role="alert">{correctionError || (original ? "新草稿与原预测的交易窗口或数据模式不一致，不能用于更正。" : "正在核对原预测，暂不能提交更正。")}</p>}{frozen ? <Link className="primary button-link" href={`/predictions/${frozen}`}>查看冻结记录 →</Link> : <button className="primary" disabled={!correctionMatches} onClick={() => dialog.current?.showModal()}>{correctionId ? "检查并更正预测" : "检查并冻结预测"}</button>}</> : <p className="muted">开始研究后，规则基线会出现在这里。草稿需经你确认才会成为冻结记录。</p>}</section></section>
    <dialog ref={dialog} aria-labelledby="confirm-title"><h2 id="confirm-title">{correctionId ? "确认新增更正预测？" : "确认冻结这条预测？"}</h2>{original && <p>原预测 {original.forecast.base_session} → {original.forecast.target_session}：{percent(original.forecast.expected_return)}。更正将新增记录，原预测继续参与统计。</p>}{draft && <ForecastFacts forecast={draft.forecast}/>}<p>冻结后将永久保留此判断、数据快照和模型版本。{me.data_mode === "REPLAY" && "这是合成回放记录，不计入真实前瞻成绩。"}</p>{error && <p role="alert" className="alert">{error}</p>}<div className="dialog-actions"><button disabled={pending} onClick={() => dialog.current?.close()}>返回检查</button><button disabled={pending || !correctionMatches} className="primary" onClick={() => void freeze()}>{pending ? "正在保存…" : correctionId ? "确认更正" : "确认冻结"}</button></div></dialog>
  </main>;
}
