import {test} from 'node:test';
import assert from 'node:assert/strict';
import {progressLabel} from '../../apps/web/lib/research-progress.js';
test('progress shows real stages without exposing unknown tool names or claiming success',()=>{
 assert.match(progressLabel({sequence:1,data:{stage:'tool.started',tool:'market_history'}}),/查询历史行情.*开始/);
 assert.match(progressLabel({sequence:2,data:{stage:'tool.completed',tool:'market_history'}}),/已结束/);
 assert.equal(progressLabel({sequence:3,data:{stage:'tool.started',tool:'private-internal-tool'}}),'执行研究工具 · 开始');
 assert.match(progressLabel({sequence:4,data:{status:'failed'}}),/未完成/);
 assert.match(progressLabel({sequence:5,data:{stage:'event_history_gap'}}),/缺失/);
});

import {compactProgress,currentProgress,traceRows,trimProgress} from '../../apps/web/lib/research-progress.js';
test('long runs keep thinking rows while bounding status noise',()=>{
 const items=[
  {sequence:1,data:{stage:'reasoning.delta',detail:'first thought'}},
  ...Array.from({length:150},(_,i)=>({sequence:i+2,data:{status:'running'}})),
  {sequence:999,data:{stage:'reasoning.delta',detail:'last thought'}},
 ];
 const trimmed=trimProgress(items);
 assert.equal(trimmed.filter(i=>i.data.stage==='reasoning.delta').length,2);
 assert.equal(trimmed.filter(i=>i.data.status==='running').length,100);
 assert.equal(trimmed[0]!.data.detail,'first thought');
 assert.equal(trimmed.at(-1)!.data.detail,'last thought');
 assert.equal(trimmed.length,102);
 assert.equal(trimProgress(items.slice(0,50)).length,50);
});
test('current action distinguishes unknown work from concrete tools and collapses duplicate status',()=>{
 const items=[{sequence:1,data:{status:'queued'}},{sequence:2,data:{status:'running'}},{sequence:3,data:{status:'running'}}];
 assert.equal(compactProgress(items).length,2);
 assert.equal(currentProgress(items,'running'),'模型正在思考');
 assert.match(currentProgress([...items,{sequence:4,data:{stage:'tool.started',tool:'quant_calculate'}}],'running'),/计算指标与量化结果/);
 assert.match(currentProgress([{sequence:5,data:{stage:'tool.completed',tool:'market_history'}}],'running'),/查询历史行情/);
 assert.equal(currentProgress(items,'failed'),'研究未完成');
});

test('reply streaming replaces the tool waiting label',()=>{
 assert.equal(currentProgress([{sequence:7,data:{stage:'tool.completed',tool:'market_history'}},{sequence:8,data:{stage:'response.streaming'}}],'running'),'正在生成研究思路');
});

test('trace rows fold each tool completion into a single call row',()=>{
 const items=[
  {sequence:1,data:{status:'running'}},
  {sequence:2,data:{stage:'tool.started',tool:'collect_evidence',role:'research',detail:'NVDA · sec/web'}},
  {sequence:3,data:{stage:'tool.completed',tool:'collect_evidence',role:'research',detail:'证据 8 条'}},
  {sequence:4,data:{stage:'tool.started',tool:'collect_evidence',role:'research',detail:'NVDA · x'}},
 ];
 const rows=traceRows(items,'running');
 assert.equal(rows.length,3);
 assert.equal(rows[1]!.meta.label,'采集研究证据');
 assert.equal(rows[1]!.meta.state,'done');
 assert.equal(rows[1]!.meta.detail,'NVDA · sec/web · 证据 8 条');
 assert.equal(rows[2]!.meta.state,'running');
 assert.equal(rows[2]!.meta.detail,'NVDA · x');
});

test('stop and failure rows carry the worker reason',()=>{
 const rows=traceRows([
  {sequence:1,data:{status:'running'}},
  {sequence:2,data:{status:'stopping',reason:'required_child_failed'}},
  {sequence:3,data:{status:'stopping',reason:'waiting_for_children'}},
  {sequence:4,data:{status:'stopping',reason:'required_tool_failed',detail:'market_history 调用失败（missing_data）'}},
 ],'running');
 assert.equal(rows[1]!.meta.label,'正在停止');
 assert.equal(rows[1]!.meta.detail,'专项分析任务失败');
 assert.equal(rows[1]!.meta.state,'warn');
 assert.equal(rows[2]!.meta.detail,undefined);
 assert.equal(rows[3]!.meta.detail,'market_history 调用失败（missing_data）');
});

test('reasoning and interim rows render as thinking steps',()=>{
 const items=[
  {sequence:1,data:{stage:'reasoning.available',detail:'核心情况：NVDA 下跌 19%'}},
  {sequence:2,data:{stage:'message.interim',detail:'检查备案文件'}},
  {sequence:3,data:{stage:'reasoning.delta',detail:'原生思考摘要'}},
 ];
 const rows=traceRows(items,'running');
 assert.equal(rows.length,3);
 assert.equal(rows[0]!.meta.label,'思考');
 assert.equal(rows[0]!.meta.state,'thinking');
 assert.equal(rows[0]!.meta.detail,'核心情况：NVDA 下跌 19%');
 assert.equal(rows[1]!.meta.label,'说明');
 assert.equal(rows[2]!.meta.label,'思考');
 assert.equal(compactProgress(items).length,3);
});

test('thinking blocks are unnumbered, merged, localized and streaming placeholders are not rows',()=>{
 const items=[
  {sequence:1,data:{status:'running'}},
  {sequence:2,data:{stage:'response.streaming'}},
  {sequence:3,data:{stage:'reasoning.delta',detail:'…asts, save a research summary with stock_chart and forecast_result.'}},
  {sequence:4,data:{stage:'reasoning.delta',detail:'Then read_research_context(symbol="NVDA").'}},
  {sequence:5,data:{stage:'tool.started',tool:'read_research_context'}},
  {sequence:6,data:{stage:'tool.completed',tool:'read_research_context'}},
 ];
 const rows=traceRows(items,'running');
 assert.deepEqual(rows.map(r=>r.kind),['step','thinking','step']);
 assert.equal(rows[0]!.number,1);
 assert.equal(rows[1]!.number,undefined);
 assert.equal(rows[2]!.number,2);
 const text=rows[1]!.meta.detail!;
 assert.ok(!text.startsWith('…'));
 assert.ok(!text.includes('asts'));
 assert.ok(text.includes('股票走势图'));
 assert.ok(text.includes('预测结果卡片'));
 assert.ok(text.includes('读取研究上下文'));
 assert.ok(!text.includes('Then read_research_context'));
});

test('parallel calls to the same tool are not collapsed as duplicate status',()=>{
 const items=[
  {sequence:1,data:{stage:'tool.started',tool:'collect_evidence',role:'research'}},
  {sequence:2,data:{stage:'tool.started',tool:'collect_evidence',role:'research'}},
  {sequence:3,data:{stage:'tool.completed',tool:'collect_evidence',role:'research'}},
  {sequence:4,data:{stage:'tool.completed',tool:'collect_evidence',role:'research'}},
 ];
 assert.equal(compactProgress(items).length,4);
 const rows=traceRows(items,'running');
 assert.equal(rows.length,1);
 assert.equal(rows[0]!.meta.state,'done');
 assert.equal(rows[0]!.count,2);
});

test('consecutive identical completed calls merge, other tools or thinking break the group',()=>{
 const same=[
  {sequence:1,data:{stage:'tool.started',tool:'read_research_context',role:'research'}},
  {sequence:2,data:{stage:'tool.completed',tool:'read_research_context',role:'research',detail:'证据 10 条'}},
  {sequence:3,data:{stage:'tool.started',tool:'read_research_context',role:'research'}},
  {sequence:4,data:{stage:'tool.completed',tool:'read_research_context',role:'research',detail:'证据 20 条'}},
 ];
 const merged=traceRows(same,'running');
 assert.equal(merged.length,1);
 assert.equal(merged[0]!.count,2);
 assert.equal(merged[0]!.number,1);
 assert.deepEqual(merged[0]!.details,['证据 10 条','证据 20 条']);

 const interleaved=[...same.slice(0,2),
  {sequence:5,data:{stage:'reasoning.delta',detail:'继续读取'}},
  {sequence:6,data:{stage:'tool.started',tool:'read_research_context',role:'research'}},
  {sequence:7,data:{stage:'tool.completed',tool:'read_research_context',role:'research'}}];
 const split=traceRows(interleaved,'running');
 assert.deepEqual(split.map(r=>r.kind),['step','thinking','step']);
 assert.equal(split[0]!.count,undefined);
 assert.equal(split[2]!.number,2);

 const different=traceRows([
  {sequence:1,data:{stage:'tool.started',tool:'market_history'}},
  {sequence:2,data:{stage:'tool.completed',tool:'market_history'}},
  {sequence:3,data:{stage:'tool.started',tool:'read_research_context'}},
  {sequence:4,data:{stage:'tool.completed',tool:'read_research_context'}},
 ],'running');
 assert.equal(different.length,2);
});

test('trace rows resolve unfinished tools on terminal status and keep orphan completions',()=>{
 const failed=traceRows([{sequence:1,data:{stage:'tool.started',tool:'market_history'}}],'failed');
 assert.equal(failed[0]!.meta.state,'failed');
 const completed=traceRows([{sequence:1,data:{stage:'tool.started',tool:'market_history'}}],'completed');
 assert.equal(completed[0]!.meta.state,'done');
 const orphan=traceRows([{sequence:1,data:{stage:'tool.completed',tool:'market_history'}}],'running');
 assert.equal(orphan.length,1);
 assert.equal(orphan[0]!.meta.state,'done');
});
test('finished traces drop stale queue and running rows',()=>{
 const items=[
  {sequence:1,data:{status:'queued'}},
  {sequence:2,data:{status:'running'}},
  {sequence:3,data:{stage:'tool.started',tool:'market_history'}},
  {sequence:4,data:{stage:'tool.completed',tool:'market_history'}},
  {sequence:5,data:{status:'completed'}},
 ];
 const rows=traceRows(items,'completed');
 assert.deepEqual(rows.map(r=>r.meta.label),['查询历史行情','研究完成']);
 assert.equal(rows[0]!.meta.state,'done');
 assert.equal(rows[1]!.meta.state,'done');
});

test('consult tasks describe an answer, not a finished research run',()=>{
 // A question about saved data must never read as 研究完成: no evidence was
 // frozen and no forecast published.
 const items=[
  {sequence:1,data:{status:'queued',role:'consult'}},
  {sequence:2,data:{status:'running',role:'consult'}},
  {sequence:3,data:{status:'completed',role:'consult'}},
 ];
 const rows=traceRows(items,'completed');
 assert.equal(rows.at(-1)!.meta.label,'已回答');
 const running=traceRows([{sequence:1,data:{status:'running',role:'consult'}}],'running');
 assert.equal(running[0]!.meta.label,'正在回答');
 const research=traceRows([{sequence:1,data:{status:'completed',role:'research'}}],'completed');
 assert.equal(research[0]!.meta.label,'研究完成');
});
