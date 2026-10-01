# S06 真实候选冻结与恢复记录

日期：2026-10-01。环境：本地独立候选 PostgreSQL 容器；不是 SG 生产部署。数据许可沿用项目所有者已确认的个人内部 Phase 1A 范围。本次没有调用 LLM、登记批准或启动 Campaign。

## 已完成

| 对象 | 实际引用 |
| --- | --- |
| panel registration | `a7ce1959-3b3f-459d-8dd3-69f698ca7dc8` |
| 总体 / panel | 503成员证券 / 20证券；种子20260927，eodhd_sector分层，固定不替换 |
| frame hash | `2c204af0fbbee83cb74896d87c66b903b426ddd34b478ef81e1158fe3aa597f5` |
| 映射 hash | `31cf03286812958c5fc9e08861d210d8d89797c3c29c9731779bef8c84404f2a` |
| 名单 hash | `b41568a62664306d046e27905ca5765cb0c5e5e306fd24299db260d622beacab` |
| 完整恢复包 payload hash | `e36a875ce5b7eda95d35b4ea92b92bda7cd22d50cb131cfaab78f8f500a3f4e4` |
| SPY永久ID | `ee161699-08b6-4250-986a-b39a393a68df`（身份观察有效期从2026-10-01开始，不冒充历史映射证明） |
| 日历 | `xnys-2016-2028-nyse-rules-v2` |
| 日历内容 hash | `0c4bae63bd467a8e28638bb1b034c26430aca656274b81568c9884d303cb0988` |
| tzdb | tzdata 2026.4 / IANA 2026d；实际加载锁包的America/New_York字节 |
| timezone文件 hash | `d7f2206b3a45989fc9ad63d558922532fa7352280d5f87176bf1db79cb1d1fa9` |

日历v2补齐交易日7月3日提前收盘，新build不改写旧build。[NYSE日历依据](https://www.nyse.com/trade/hours-calendars)

## 恢复证据

实际将包含原响应、源元数据、登记、证券和身份映射的JSON包导入新建已迁移数据库。validate全部通过；重新导出逐字段一致，hash一致；第二次恢复幂等。源库未修改。报告保存在私有目录的 `panel-recovery-report.json`。

`candidate-database.dump`另保存完整候选库，覆盖SPY、日历、行情、training manifest及未批准release；其hash见`database-backup-manifest.json`。另在新库完成完整恢复，行数与原始内容、日历、training/release hash均一致（`database-restore-report.json`）。外部行情/模型/Trial文件不在pg_dump内，必须同时保留完整私有目录和代码快照。这是一份本地备份，不代表独立故障域、WORM或生产PITR验收。

## 模型与 release

- [Trial真实结果](../trials/trial-001-results.md)：D20测试Brier 0.253012 vs baseline 0.250000；未显示增量。
- training manifest：`tm-logistic-ridge-candidate-20261001-v1`，hash `febd10801f8ea99819197007805bf53b82e81b9880f2516aac02b0bee1c38098`。
- 候选 release：`release-logistic-ridge-candidate-20261001-v1`。
- **候选 release hash：`06618a0c769cceb75968106897b4f3638cd6ff61de0fe3565e73fa3c1f6eceb5`**。
- 候选库批准记录=0，Campaign=0；formal_campaign_allowed=false。
- 该hash固定供审阅，不能把本地测试或生成hash视为批准。后续更改模型、代码、引用或规则须新候选及相应登记。

## 可复核文件与命令

私有目录：`.local/s06-candidate-20261001/`，权限受本地用户控制，已加入gitignore。凭证仅在`.env`和私有`environment.json`中；运行脚本通过环境变量读取，不打印DSN/token。

- `panel-candidate.json`：初始采集/抽样校验摘要。
- `panel-bundle.json`：完整可移植恢复包；正式冻结后按registration ID恢复，不重新采集抽样。
- `references.json` / `calendar.json`：SPY、日历与实际timezone内容引用。
- `historical-input.json` / `prices-*.json`：冻结的授权原始数据，停订处理与备份一致。
- `trial-001/`：可复算数据集、固定模型、逐案例结果和评估。
- `training-manifest.json` / `release-candidate.json` / `release-summary.json`：可审阅的实际候选。

```bash
# 需先导出独立候选库的 YOUWEI_DATABASE_URL；不要连接生产库。
uv run --frozen python ops/build_s06_release.py --candidate-dir .local/s06-candidate-20261001
# 相同候选幂等；更改内容拒绝。不会批准或创建Campaign。
```

初次采集命令为`ops/prepare_s06_campaign.py`后接`ops/collect_s06_trial_data.py`；本次已完成，禁止当作恢复命令重跑。`ops/run_s06_trial.py`核验预登记hash与panel，并拒绝同Trial换输出目录重复运行。若需新比较，事前登记新Trial及其原因。

## 尚未完成

人工判断是否允许该候选开展未来实验，以及具体release批准；SG生产部署、权限和运维验收仍按S09推进。本次历史回顾存在PIT/总体/公司行为覆盖限制，不将其写成正式能力验收。


## 验证结果

- `uv run --frozen pytest -q --tb=short`：396 passed，无skip（97.52秒）。
- `python3 infra/validate_upstreams.py --mode catalog`：VALID；仅登记一致性，不授予发布权限。
- `git diff --check`、v2四份协议内容hash核对通过。
- 重新加载冻结模型JSON，六组历史验证/测试共7,800条预测与评分逐值一致；`numeric-reproduction-report.json`。
- 实际代码版本以候选manifest和Trial provenance中的文件hash固定；`source-snapshot.tar.gz`保存本次工作区源码（不含.env、私有数据或凭证）。未自动提交Git、未发布镜像或部署SG。
