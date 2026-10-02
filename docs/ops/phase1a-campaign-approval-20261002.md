# Phase 1A 正式 Campaign 批准包（一次性审阅材料）

日期：2026-10-02。状态：**待项目所有者一次性批准**（r1：2026-10-02 所有者审阅后修订，见 §0）。当前生产库 `release_approvals=0`、`campaigns=0`、`formal_campaign_allowed=false`；本文档不含任何批准记录。

本包汇总正式启动 `phase1a-pilot-2026q4` 所需的全部决定性信息。批准是项目所有者的行为，工具与 Agent 不代为批准；批准后按下文 §6 机制登记。本包定稿后不再修改——若任何要素变化（release、计划、tenant），需重新生成本包。

## 0. 修订记录（r1，2026-10-02）

项目所有者审阅初版后指出三项问题，均已处理（hash 集不变）：

1. **首批量化模型缺历史输入（阻断项）**：已回补 2026-06-01 → 2026-10-01 全部 86 个已完成交易日 × 21 对象（零缺口，标准 ingest 路径，保留实际入库时间），并经真实预测读取路径验证 **60/60 可用**（20 证券 × 3 horizon，特征完整，详见 §4.1）。
2. **随访日期写错**：末批 D60 随访延伸至 2027 年 3 月底而非初版误写的「2 月下旬」，采集与随访需覆盖至 **2027-04-01 收盘**（见 §5 修正后的时间线）。
3. **`approve_release` 示例缺必填参数**：已补齐 `scope` 审计文本参数（见 §6）。

审阅意见本身不构成批准；批准须所有者对本文 hash 集的明确确认。

## 1. 批准对象（精确引用）

### 1.1 ResearchRelease

| 项 | 值 |
| --- | --- |
| release_id | `release-logistic-ridge-candidate-20261002-v1` |
| **release_content_sha256** | **`bdb8bbe034551d7bcc413965925dc9d7c6f52fa31d4344b8b0c199672c5b4994`** |
| training_manifest | `tm-logistic-ridge-candidate-20261001-v1`（sha256 `febd10801f8ea99819197007805bf53b82e81b9880f2516aac02b0bee1c38098`） |
| panel_bundle_sha256 | `e36a875ce5b7eda95d35b4ea92b92bda7cd22d50cb131cfaab78f8f500a3f4e4`（registration `a7ce1959-3b3f-459d-8dd3-69f698ca7dc8`，503 成员 → 20 selected） |
| code_files（5） | `quant/logistic.py`、`quant/dataset.py`、`youwei_core/ledger/model_registry.py`、`youwei_core/ledger/pipeline.py`、`uv.lock`（逐文件 hash 已入库并在导入时校验一致） |
| protocol_refs（4） | `target-spec.v1.md`、`time-protocol.v1.md`、`campaign-policy.v2.md`、`known-answer-cases.v1.md`（hash 已入库并校验；`s00-registration.v2.json` 由计划侧引用） |

已导入生产库并逐项复核（[s09b-production-db-prep.md](s09b-production-db-prep.md) §二）；本轮组装前再次确认库内值与本表一致、幂等。

### 1.2 Campaign 计划（S06j 冻结输入，2026-10-02 所有者确定）

| 项 | 值 |
| --- | --- |
| campaign_key | `phase1a-pilot-2026q4` |
| **campaign_plan_sha256** | **`d403c8e553e32e554cc4b587e7f08772f253aced70d2896062a5984aab7b1d29`** |
| **planned_cutoffs_sha256** | **`59f7aa3e1303c75da6d1795f5353628ec8dc9e54b78fcd48edbce12a3b290788`** |
| phase | `1a`（enabled_sources = `baseline` + `quant_model`；fallback = `phase1a-none`，无回退） |
| primary_metric | **`paired_brier_quant_minus_baseline`**（D20 主指标；`register_campaign` 默认参数是测试占位值，正式注册显式传本值） |
| 目标窗口 | D1 / D20 / D60（`target-spec.v1`） |
| 时间协议 | `time-protocol-v1`（sha256 `82d6b3473d8ce609f138e476d157c7032c3818753cd50c4b404ac568952cf8fe`；周六 06:00 ET cutoff、入场前 15 分钟 deadline） |
| 批次 | 12 批每周一次，**批准登记后不自动顺延、不删除漏跑周次** |

12 个 cutoff（UTC；批 1–4 为 EDT 10:00Z，11-07 DST 切换起批 5–12 为 EST 11:00Z，即均为周六 06:00 America/New_York）：

```text
2026-10-10T10:00:00+00:00    2026-11-07T11:00:00+00:00
2026-10-17T10:00:00+00:00    2026-11-14T11:00:00+00:00
2026-10-24T10:00:00+00:00    2026-11-21T11:00:00+00:00
2026-10-31T10:00:00+00:00    2026-11-28T11:00:00+00:00
                             2026-12-05T11:00:00+00:00
                             2026-12-12T11:00:00+00:00
                             2026-12-19T11:00:00+00:00
                             2026-12-26T11:00:00+00:00
```

### 1.3 scope_manifest（批准的结构化绑定）

```json
{
  "schema_version": 1,
  "kind": "campaign_execution",
  "phase": "1a",
  "tenant_id": "f497c122-45b6-497b-bb99-9c42401c3e5f",
  "campaign_key": "phase1a-pilot-2026q4",
  "release_content_sha256": "bdb8bbe034551d7bcc413965925dc9d7c6f52fa31d4344b8b0c199672c5b4994",
  "campaign_plan_sha256": "d403c8e553e32e554cc4b587e7f08772f253aced70d2896062a5984aab7b1d29"
}
```

**scope_manifest_sha256 = `0b899b003c923efca10dc7de63f4921c1e87ad5223f610caa4ee602689a11805`**（`CampaignPlanScope` 规范化 hash，批准时按此绑定）。

### 1.4 Tenant 与 panel

- 正式 tenant：`youwei-internal-research` = `f497c122-45b6-497b-bb99-9c42401c3e5f`（按 slug 幂等创建）。
- Panel 20 只（selected 顺序）：`AVY GL LOW DVN CCI VRTX GWW FFIV JKHY ICE PODD GE KIM MLM SJM TYL CEG TMUS TGT SO`；benchmark SPY（`ee161699-08b6-4250-986a-b39a393a68df`，身份有效期自 2026-10-01，不冒充历史映射证明）。
- 日历 `xnys-2016-2028-nyse-rules-v2`（3391 交易日，内容 hash `0c4bae63...`）；tzdata 2026.4 / IANA 2026d。

## 2. 批准的含义

批准 = 授权以 release `bdb8bbe0...` 为冻结模型实现，按计划 `d403c8e5...` 与 scope_manifest `0b899b00...` 启动并运行 `phase1a-pilot-2026q4`：12 个周六 cutoff 各生成一批预测（20 只 × D1/D20/D60，baseline + quant_model 双源），原子封存，等待原时点数据成熟后按协议评分与月度汇总。

**不批准、也不隐含**：

- 对模型预测有效性的任何宣称——历史 Trial 未显示增量（见 §3），前向结论以本实验的评估为准；
- 未来任何 release、Lesson、模型或 prompt 变更（各自走独立批准）；
- Phase 1B 的 LLM 数据转发授权（Tiingo/EODHD 条款下首次向 LLM 发送供应商数据前另行取得）；
- 自动交易或对外提供投资建议（项目范围外）。

## 3. 候选与证据基础（如实披露）

- **Trial 001 结果为负向**：D20 测试集 Brier `0.253012` vs baseline `0.250000`，logistic-ridge 候选在历史回放中**未优于**基线（[trial-001-results](../trials/trial-001-results.md)，登记 [registry](../trials/registry.md)）。本实验的目的正是在 PIT 纪律下测量该候选的前向表现，不预设其有效。
- 历史回放存在已知限制（PIT/总体/公司行为覆盖，见 [冻结记录](s06-campaign-freeze-20261001.md)）；历史结果不作为预测能力证据。
- 模型可复算：冻结模型 JSON 六组历史验证/测试 7,800 条预测逐值复现（`numeric-reproduction-report.json`）。
- 已知答案用例（K01）与协议契约测试覆盖抽样、评分与封存规则。

## 4. 生产与运维状态（截至 2026-10-02）

- 生产 API/Worker/PostgreSQL 运行中（镜像 digest 固定：core `53631663...`、postgres `8d69232c...`）；deployment + catalog 校验 VALID。
- 采集已启用并实际工作；**特征历史已回补**（2026-10-02，2026-06-01 → 2026-10-01，86 个已完成交易日 × 21 对象零缺口，`ingested_at`/`usable_at` 为实际入库时间，均早于首个 cutoff）；生产库 price_observations 1911 行（含滚动采集与回补的版本化重叠，PIT 读取取最新可用版本）、raw_objects 43。

### 4.1 首批可评分性验证（r1 新增，真实读取路径）

`ops/verify_prediction_coverage.py`（只读，生产库）：按批次同路径构建 `ReleasePredictor`（release manifest 的模型 artifact hash、特征契约、冻结日历、tzdb pin）+ `daily_bars_asof`（PIT 读取，mode=forward，最新可用版本）+ `predict_case`。结果：

- 模拟 cutoff 2026-10-01 20:00Z（最后已完成 session）：**60/60 quant 预测可用**（20 证券 × D1/D20/D60，特征完整，无 `incomplete_frozen_features`）。
- 批次 1 真实窗口（cutoff 2026-10-10T10:00Z 需 2026-07-16 → 2026-10-09 共 61 个 session）：**55 个已覆盖**（至 2026-10-01），余 6 个（10-02 → 10-09）由每日滚动采集在 cutoff 前落地（采集窗口 ET 17:30–05:30，全部早于周六 06:00 ET cutoff）。
- 回补脚本：`ops/backfill_history.py`（幕等，覆盖对照冻结日历逐日校验）。
- 残余风险：10-02 → 10-09 期间供应商数据缺口或停牌将按协议封存 `unavailable` 并留在分母；不事后回补用于首批。
- 备份：同机 pgBackRest（WAL 归档 + 每日全量保留 7 + 监控告警生效）；**异地副本按所有者决策（2026-10-02）暂缓**。
- 恢复能力：PITR/全量丢失恢复演练通过（机制级）；droplet 整机重启演练 RTO ≈ 3 分钟零人工干预；镜像回滚 drill 通过。
- S09c 搁置项（所有者决策）：告警 webhook URL、Campaign 负载下资源复测；不阻断本批准。

## 5. 风险与已知义务

- 供应商：Tiingo Personal + EODHD 个人自用许可；停订后删除数据为已知义务（[data-license-checklist](data-license-checklist.md)）。
- 12 批横跨 2026-10-10 → 12-26：期间出现不可用日按协议保留在计划分母（漏跑不补、不删除）。
- **随访时间线（r1 修正，按冻结日历 xnys-2016-2028-nyse-rules-v2 重算）**：末批（cutoff 2026-12-26）入场 **2026-12-28**；D60 出场 **2027-03-24 收盘**（入场日计 D1，第 60 个交易日）；异常数据宽限期（exit 起 5 个后续交易日，campaign-policy §134；2027-03-26 耶稣受难日休市）至 **2027-04-01 收盘**。**采集与结果随访须覆盖至 2027-04-01 之后**，评估汇总需等标签成熟。
- 若批准后 release 依赖的 code_files hash 因后续提交漂移，须按流程重新生成候选与计划（本包固定引用，不自动追踪 HEAD）。

## 6. 批准后机制（工程执行，不需所有者操作）

1. `approve_release(release_id="release-logistic-ridge-candidate-20261002-v1", approver_principal_id="human-owner", scope="phase1a-pilot-2026q4：批准 release bdb8bbe0… 按计划 d403c8e5… 开展 12 批前向实验（审计文本）", scope_manifest=<§1.3>, basis="phase1a-pilot-2026q4 one-time approval 2026-10-02")` → 记录 `scope_sha256=0b899b00...`（幂等；`scope` 为必填审计文本，结构化绑定由 `scope_manifest` 提供；所有者 principal 惯例 `human-owner`，如需具名可替换）。
2. `register_campaign(tenant=f497c122..., campaign_key=phase1a-pilot-2026q4, release=<§1.1>, benchmark=SPY, panel=20 selected, enabled_sources=[baseline, quant_model], fallback=phase1a-none, primary_metric=paired_brier_quant_minus_baseline, planned_cutoffs=<§1.2 列表>, planned_cutoffs_sha256=59f7aa3e..., campaign_plan_sha256=d403c8e5...)` —— 注册内部校验批准 scope 与计划 hash 绑定。
3. 调度器接管：首个批次 cutoff **2026-10-10 06:00 ET（周六）**自动冻结输入、生成并原子封存预测；采集已就位（滚动 5 交易日窗口 + §4.1 已验证覆盖）。
4. 每批结果按 `s00-registration.v2` / `campaign-policy.v2` 评分；月度汇总与更正按协议追加。

## 7. 可复核命令

```bash
# SG 上重算计划 hash（纯计算，不写库；输出应与 §1.2/§1.3 一致）
cd /root/youwei-trading-agent && .venv/bin/python ops/s09b_make_plan.py \
  --tenant-id f497c122-45b6-497b-bb99-9c42401c3e5f --out /tmp/plan-verify.json
# 生产库状态（应见 release/tenant/manifest 且 approvals=0、campaigns=0）
docker exec youwei-production-postgres-1 psql -U youwei_app -d youwei -Atc \
  "select release_id, release_content_sha256 from research_releases; select count(*) from release_approvals; select count(*) from campaigns;"
# 计划工件（与 §1.2 逐项一致）
cat /opt/youwei/production/campaign-plan.json
# 首批可评分性验证（只读，真实预测读取路径；输出 §4.1）
docker run --rm --network youwei-production_core \
  -v /root/youwei-trading-agent/ops/verify_prediction_coverage.py:/harness/verify.py:ro \
  -e YOUWEI_DATABASE_URL="postgresql+asyncpg://youwei_app:<pw>@postgres:5432/youwei" \
  ghcr.io/youweichen0208/youwei-core@sha256:53631663f04096f3bcb19d4ee14d1c5598785f1bb39a12c75a736691d9cc1848 \
  python /harness/verify.py --release release-logistic-ridge-candidate-20261002-v1
```

r1 修订时已执行上述全部复核：库内值与本文所有 hash 一致；make_plan 重算 `d403c8e5...`/`59f7aa3e...` 与记录一致；可评分性验证 60/60 可用、批次 1 窗口 55/61 已覆盖（余 6 由采集覆盖，见 §4.1）。

---

**待所有者确认**：是否批准 release `bdb8bbe0...` 按计划 `d403c8e5...` 与 scope_manifest `0b899b00...` 启动 `phase1a-pilot-2026q4`（r1 修订版，三项审阅意见已处理，hash 集不变）。
