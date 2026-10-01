# 数据供应商许可核对表（S06 准备材料）

日期：2026-10-01  
状态：核对材料，供项目所有者执行采购/确认。**账号能调用 API 不等于权限已具备**；本表把许可条件按阶段拆分，避免「尚未取得 LLM 转发权」错误阻断完全不调用 LLM 的 Phase 1A。

依据：[us-data-vendor-selection](../research/us-data-vendor-selection.md)、[tiingo-token-verification](../research/tiingo-token-verification.md)、[eodhd-constituents-verification](../research/eodhd-constituents-verification.md)。

## 1. 阶段拆分原则

| 阶段 | 需要落实的许可 | 说明 |
| --- | --- | --- |
| **Phase 1A**（不调 LLM） | 内部计算、留存、备份、退订后数据处理、套餐限额 | baseline/quant 只读数据、本地计算与存储，不发送给第三方模型 |
| **Phase 1B**（首次转发 LLM） | 向模型发送供应商数据的授权 | 在首次把数据交给 Hermes/LLM 网关**之前**取得，不是 Phase 1A 的前置 |

因此：**LLM 转发授权未取得，不阻断 Phase 1A**。Phase 1A 只要求数据可被本项目内部计算/留存/备份，且限额已知。

## 2. Tiingo（行情源：OHLC、分红、拆分）

| 项 | 当前状态 | 待核实 | 证据位置 |
| --- | --- | --- | --- |
| 套餐 | free/evaluation（响应内容标记） | 需升级为可留存/内部使用的付费套餐 | [tiingo-token-verification §6](../research/tiingo-token-verification.md) |
| Phase 1A：内部计算/留存/备份 | Starter/Trial 禁止持久保存；付费计划条款待确认 | 购买付费套餐时浏览器确认留存范围、云部署、用户数 | [us-data-vendor-selection](../research/us-data-vendor-selection.md) |
| Phase 1A：退订后处理 | 付费计划停订后要求删除（要点已记录） | 确认删除范围与期限 | 同上 |
| Phase 1A：限额 | 响应头无 rate-limit，精确配额在网页控制台 | 控制台确认每日请求配额 | [tiingo-token-verification](../research/tiingo-token-verification.md) |
| Phase 1B：LLM 转发 | 未取得 | 首次转发前确认条款允许（或另取授权） | 待询问 |

**待询问 Tiingo 的草稿**（供项目所有者发送或控制台核对）：

> 我们计划将贵方行情数据用于内部研究（不向公众展示），在自有新加坡服务器上计算、留存并备份，用于训练和回测。请确认：(1) 当前/升级后套餐是否允许内部计算、持久留存与备份；(2) 订阅终止后的删除要求与期限；(3) 每日请求限额；(4) 是否允许将数据片段发送给第三方 LLM 供应商用于生成研究（若不，请说明取得此类授权的途径）。

## 3. EODHD（成分/分类源：S&P 500 成员、板块）

| 项 | 当前状态 | 待核实 | 证据位置 |
| --- | --- | --- | --- |
| 套餐 | 已购买 Indices Historical Constituents Data 产品 | 产品条款的留存/转发范围待确认 | [eodhd-constituents-verification](../research/eodhd-constituents-verification.md) |
| Phase 1A：内部计算/留存/备份 | 未确认产品条款是否允许留存与备份 | 控制台/条款确认 | 待核实 |
| Phase 1A：限额 | Marketplace 产品的精确每日限额未在响应头出现 | 控制台确认 | [eodhd-constituents-verification 未决项 4](../research/eodhd-constituents-verification.md) |
| Phase 1A：退订后处理 | 未确认 | 条款确认 | 待核实 |
| Phase 1B：LLM 转发 | 未取得 | 首次转发前确认 | 待询问 |

**待询问 EODHD 的草稿**：

> 我们已购买 Indices Historical Constituents Data 产品。请确认：(1) 该产品是否允许内部留存、计算与备份（在新加坡自有服务器）；(2) 该产品的每日请求限额；(3) 订阅终止后的删除要求；(4) 是否允许将成分/板块数据片段发送给第三方 LLM 供应商用于生成研究。

## 4. 汇总：阻断归属

| 阶段 | 阻断项 | 当前是否阻断 Phase 1A |
| --- | --- | --- |
| Phase 1A | Tiingo 付费套餐 + 留存/备份/限额/退订条款 | **是**（free 套餐禁止持久保存） |
| Phase 1A | EODHD 留存/备份/限额条款确认 | **是**（条款未确认） |
| Phase 1B | Tiingo LLM 转发授权 | **否**（Phase 1B 才需要） |
| Phase 1B | EODHD LLM 转发授权 | **否**（Phase 1B 才需要） |

结论：**当前真正阻断 Phase 1A 的是两家供应商的「留存/备份/限额/退订」条款确认**，而不是 LLM 转发授权。项目所有者按本表逐项在供应商控制台/条款确认，并把结果记录到对应研究文件后，即可解除 Phase 1A 的数据许可阻断。
