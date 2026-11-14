export type ProgressItem = { sequence: number; data: { stage?: string; tool?: string; status?: string; role?: string } };
export function progressLabel(item: ProgressItem) {
 const {stage,tool,status,role}=item.data;
 const names:Record<string,string>={market_history:'查询历史行情',market_news:'检索相关新闻',quant_compute:'计算研究指标',quant_calculate:'计算指标与量化结果',read_dataset:'读取行情数据',read_result:'读取分析结果',read_holdings:'读取模拟持仓',get_operation:'检查数据计算进度',submit_specialist:'安排预测或组合分析',get_specialist_result:'读取专项分析结果',get_workspace:'读取已有图表',save_research_summary:'保存研究分析',canvas_upsert:'生成可视化产物',canvas_patch:'更新可视化产物',canvas_remove:'调整研究产物'};
 const states:Record<string,string>={queued:'任务已进入队列',running:'Agent 已接收任务',completed:'研究完成',failed:'研究未完成',cancelled:'任务已取消',stopping:'正在停止',unknown:'正在核对执行状态',interrupted:'研究已中断'};
 const roles:Record<string,string>={research:'研究 Agent',prediction:'预测 Agent',portfolio:'组合 Agent'};
 const prefix=roles[role??''] ? `${roles[role!]} · ` : '';
 if(stage==='response.streaming') return '正在接收回复';
 if(stage==='event_history_gap') return '部分活动记录缺失，正在同步任务状态';
 if(stage==='tool.started'||stage==='tool.completed') return prefix+(names[tool??'']??'执行研究工具')+(stage==='tool.completed'?' · 已结束':' · 开始');
 return prefix+(states[status??'']??'任务状态更新');
}

export function compactProgress(items: ProgressItem[]) {
 return items.filter((item,index)=>index===0 || progressLabel(item)!==progressLabel(items[index-1]!));
}
export function currentProgress(items: ProgressItem[], status: string) {
 if(status !== 'running') return progressLabel({sequence:0,data:{status}});
 const latest = items.at(-1);
 if(latest?.data.stage==='event_history_gap') return progressLabel(latest);
 if(latest?.data.stage==='response.streaming') return '正在接收回复';
 const tool = [...items].reverse().find(item=>item.data.stage==='tool.started'||item.data.stage==='tool.completed');
 if(!tool) return '等待模型响应';
 return progressLabel(tool)+(tool.data.stage==='tool.completed'?'，等待下一步进度':'');
}
