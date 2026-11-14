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

import {compactProgress,currentProgress} from '../../apps/web/lib/research-progress.js';
test('current action distinguishes unknown work from concrete tools and collapses duplicate status',()=>{
 const items=[{sequence:1,data:{status:'queued'}},{sequence:2,data:{status:'running'}},{sequence:3,data:{status:'running'}}];
 assert.equal(compactProgress(items).length,2);
 assert.equal(currentProgress(items,'running'),'等待模型响应');
 assert.match(currentProgress([...items,{sequence:4,data:{stage:'tool.started',tool:'quant_calculate'}}],'running'),/计算指标与量化结果/);
 assert.match(currentProgress([{sequence:5,data:{stage:'tool.completed',tool:'market_history'}}],'running'),/等待下一步/);
 assert.equal(currentProgress(items,'failed'),'研究未完成');
});

test('reply streaming replaces the tool waiting label',()=>{
 assert.equal(currentProgress([{sequence:7,data:{stage:'tool.completed',tool:'market_history'}},{sequence:8,data:{stage:'response.streaming'}}],'running'),'正在接收回复');
});
