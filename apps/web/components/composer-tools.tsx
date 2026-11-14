"use client";
export function ComposerTools({onSelect,disabled=false}:{onSelect:(text:string)=>void;disabled?:boolean}) {
 return <details className="composer-tools"><summary aria-label="研究工具">＋ <span>研究工具</span></summary><div className="composer-tools-menu">{[
 ['走势分析','请分析这只股票近期的价格走势，展示图表并标明数据日期。'],
 ['新闻研究','请整理这只股票近期的相关新闻，分析潜在影响并列出来源。'],
 ['公司对比','请比较这些公司的基本面、近期走势与主要风险。']
 ].map(([label,prompt])=><button type="button" disabled={disabled} key={label} onClick={e=>{onSelect(prompt!);e.currentTarget.closest('details')?.removeAttribute('open');}}>{label}</button>)}</div></details>;
}
