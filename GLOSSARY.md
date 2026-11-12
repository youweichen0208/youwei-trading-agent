# Hermes Alpha Lab

多用户美股研究与模拟分析的共同术语。

| 列 1 | 列 2 | 列 3 | 列 4 |
| --- | --- | --- | --- |
| hello | <br> | <br> | <br> |
| <br> | <br> | <br> | <br> |

## Language

<p data-rich-format="" style="color: rgb(36, 36, 36); background-color: rgb(255, 213, 229)"><strong>研究任务（Task）</strong>：用户一次请求所对应的可追踪研究工作，可包含专项子任务。 <em>Avoid</em>: 将任务与单次模型运行混称</p>

**运行（Run）**：<mark style="background-color: #ffd5e5">某个 Profile 为任务执行的一次 Agent 工作过程。</mark>

**角色（Role）**：research、prediction、portfolio 三种职责类别。

<p data-rich-format="" style="color: rgb(36, 36, 36); background-color: rgb(255, 213, 229)"><strong>Profile</strong>：属于特定用户和角色的独立 Agent 身份及其记忆、配置和会话集合。 <em>Avoid</em>: 全体用户共用的功能标签</p>

**对话（Thread）**：用户可见的连续交流及其关联研究结果。

**执行会话（Session）**：某一 Profile 内连续执行所使用的上下文。
_Avoid_: 与用户对话或登录会话混称

**数据集引用（datasetRef）**：指向一个可追溯数据版本的标识。

**结果引用（resultRef）**：指向一次已保存计算或研究产物的标识。

**模拟持仓快照**：用户在指定时点声明的证券数量和现金，不代表券商实际资产。
