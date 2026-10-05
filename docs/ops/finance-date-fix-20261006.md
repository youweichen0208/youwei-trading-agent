# 个人助手日期边界与分析约束修复（2026-10-06）

用户报告 AAPL 三个月走势查询先两次 invalid_financial_arguments，随后结果只到 10 月 2 日。实际保存的工具参数先为 start=2026-07-05/end=2026-10-06，重试 end=2026-10-05。固定 UTC 2026-10-05 重放确认：金融包要求排除式 end 不晚于 UTC 今天，导致不能包含当天；拒绝发生在供应商请求之前。

## 修复与来源

- 金融包 [PR #2](https://github.com/youweichen0208/trading_core/pull/2)，源码 `a0e396b`：允许排除式 end 为 UTC 明天，仍拒绝更远未来、空区间和超过五年；Schema 提示当天日线可能不完整。
- 助手 [PR #3](https://github.com/youweichen0208/trading-assistant/pull/3)，发布源码 `9b38567be3f401730bc5a150e83bbb5b92f6c58f`：固定新 wheel/hash，日期错误返回静态修复指引，不回显输入或凭证。个人分析指令要求区分 OHLC/Adj Close、SEC 申报日/业绩公布日、事实/推断、季度季节性和同比；解释异动须读取相应来源，不凭同日申报推定财报导致下跌。
- 指令变化属于个人助手，不修改正式 ResearchRelease。提示词约束不保证模型每次回答正确，未把工程修复当分析质量评估。

固定镜像为 `ghcr.io/youweichen0208/trading-assistant-candidates@sha256:73e28618c2fe5a67c05098e855a668e808a8e336ca498339d23af0a865315ad7`。初次向已有 trading-assistant GHCR 包推送被 write_package 权限拒绝，未部署；改用助手仓库关联的独立候选包，重新构建验证后发布。[成功构建](https://github.com/youweichen0208/trading-assistant/actions/runs/37387250503)。没有修改官方 Hermes/Python/依赖版本。

## 实际验证

- 金融包日期回归先观察失败，修复后全量 24 项通过；助手提示回归先失败后通过，全量 68 项通过。助手 uv.lock 仅 wheel hash 改变。
- 金融包 CI、助手单元/固定 Hermes 原生/镜像 CI 通过；发布前镜像内原生 mock 与金融 fixture 通过，不调用付费模型。
- SG 真实免费来源重放原 AAPL 请求：价格与指标均成功，65 个样本，实际期间 2026-07-06 至 **2026-10-05**。候选与上线后各验证一次；修复前用户结果为 64 样本、截至 10 月 2 日。
- WebUI 跨服务 mock：模型发现、聊天/完整历史、SSE、知识/重启、备份恢复、后台模型发现失败分流、关闭注册、历史保留通过。
- 标准聊天/Hermes/配置备份与隔离恢复切换前后 ALL PASS；新助手镜像对旧 Hermes 备份无网络恢复通过。
- 仅切换 gateway 与只读技能两个助手服务，WebUI/Core 等另外 14 个容器 ID/StartedAt/镜像不变。线上健康、登录/聊天/工作台 API 200；匿名工作台 401、原始 Responses 403。实际渲染 Compose 的 deployment 锁/清单校验通过。公网浏览器工作台与技能 API 正常。
- 线上 instructions.txt hash 与固定源码一致。旧回答不自动重算；需要重新发送问题。没有新建真实模型对话，没有正式研究数据变更或正式前向评估。

## 部署与回滚

目标机 `/root/finance-date-fix-20261006/` 保存私密备份、渲染配置和 rollback 副本；公开证据在 `infra/releases/20261006-finance-date-fix/`，不含凭证。

回滚使用 `/root/finance-date-fix-20261006/rollback/compose.json` 恢复 `/opt/youwei/chat/compose.json`，保留现有 secrets.env 与数据卷，再执行：

```bash
docker compose -p youwei-chat --project-directory /opt/youwei/chat \
  -f /opt/youwei/chat/compose.json --env-file /opt/youwei/chat/secrets.env \
  up -d --no-deps hermes-assistant hermes-workbench-skills
```

旧镜像 `ghcr.io/youweichen0208/trading-assistant@sha256:ffd1768a4eb036083c37f4d16627922db66dd4318b4d949336c815e9ba4dcdc1`。本次无数据迁移、不覆盖正式研究库，也未实际触发回滚。全流程未调用真实付费模型，因此没有声称完整分析回答质量已实测通过。
