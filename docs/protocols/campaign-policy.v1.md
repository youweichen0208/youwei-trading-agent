# 批次、评估与批准政策 v1

protocol_id：campaign-policy-v1\
登记日期：2026-09-27\
状态：用户选择已登记；Q5 停牌目标、Q7 区间方法及实际数据/模型引用尚未就绪，禁止创建正式 campaign。

## 1. 使用范围（Q1）

仅自用或受控内部研究，不自动实盘交易，不向公众提供服务，不对外提供投资建议。未来扩大范围按 [ARCHITECTURE §13](../ARCHITECTURE.md#13-对外开放的增量条件)重新评估。

这是用途登记，不是法律或供应商许可的豁免结论。身份、请求、数据向模型传输和数据留存仍按实际授权配置。

## 2. 总体与固定研究样本（Q2）

总体为正式样本登记时点有效的 S&P 500 成员 PIT 快照，不使用今天的成分表冒充过去总体。成员资格与 GICS 分类均引用当时有效版本。

“登记日期2026-09-27”是本协议决策的日期，不是已取得的成员快照日期。实际 frame_as_of、provider、source version/hash、usable_at 在 S01/S04 取得并验证数据后登记；不得把未来取得的数据谎报为今天已经可用。

使用 GICS 一级 Sector 作为“行业分层”的实现约定，以在 N=20 内覆盖各大板块；不使用更细的 Industry/Sub-industry 全覆盖。GICS 存在多个分类层级，层级与分类版本必须明示。[S&P DJI GICS 说明](https://www.spglobal.com/spdji/en/index-family/equity/sp-sectors/)

抽样单位为唯一 permanent security_id 的成员证券行，N=20。多个股类可对应同一发行人，保留 issuer_id；不假定成员证券行数恰好500，也不保证样本为20家不同公司。抽样总体文件必须体现这个单位。

### 2.1 固定种子与名额分配

以下为使“分层固定种子抽样”可复现的实现约定，在获取样本表现前登记：

```text
sampler_version = sector-stratified-hash-v1
seed = "20260927"
stratification_level = gics_sector
sample_size = 20
frame_sort_key = security_id
```

设有 K 个非空板块，N_s 是各板块证券数，M 是总体证券数：

1. 要求 M >= 20、K <= 20，且每个成员有 PIT sector code；不满足时停止登记，不静默删除缺分类的证券。
2. 每个板块先分1个名额。
3. 剩余20-K个名额，按 (N_s-1)/(M-K) 分配；先取整，再按余数降序分配剩余名额，同余按 sector code 升序。
4. 当 M=K=20 时直接每板块选1个；若剩余名额为0则不计算分母。
5. 每板块按如下 hash 升序取足名额，同 hash 按 security_id 排序：

```text
SHA256(UTF8(compact_JSON([
  sampler_version, seed, frame_hash, sector_code, security_id
])))
```

compact_JSON 固定数组顺序、对象键按字典序、无额外空格、UTF-8、无 BOM/结尾换行；输入标识使用已规范化的字符串。抽样总体规范化为仅含 security_id 和 sector_code 的对象数组，按 security_id 升序；frame_hash 对这些实际字节计算。原始成员/PIT分类快照和 issuer 映射另有完整源文件及 hash。选中名单为按 security_id 升序的字符串 JSON 数组，使用同一序列化规则计算 selected_list_hash。manifest 保存算法版本、种子、所有名额、源版本/映射及两个 hash。

上述规范化版本登记为 sampling-json-v1；未取得真实文件前 hash=null，禁止捏造。重跑相同输入必须得到相同名单。

### 2.2 固定样本与推断范围

首次抽样形成 panel_id；每周 campaign 复用同一 panel，不每周随当前 S&P 500 成分重抽。退出指数、分类变化、停牌、退市和缺数据都保留历史；不以事后表现或数据便利性更换成员。

固定 panel 的既有20个证券槽位继续进入计划，不能再研究/交易的证券标记状态并保留分母。未来确需换样本，创建新的 panel/protocol 版本，单独报告。

初始每批60个 case。报告默认证券等权、批次等权，结论限于这个固定样本；它既不复制 SPY 的市值权重，也不自动代表整个 S&P 500 的模型能力。分层和 SPY 基准不能消除所有选择偏差。

首次批准运行时同时登记连续周次计划的起点、频率和观察期；计划中的整周失败/停机也进入分母。只在有好数据的周生成 campaign 会造成新的选择偏差，禁止借“未就绪”静默跳过已经计划的周次。

## 3. 来源与回退（Q3、Q6）

每周锚定规则见 [time-protocol.v1.md](time-protocol.v1.md)；目标见 [target-spec.v1.md](target-spec.v1.md)。D20 为主，D1/D60 分别作为探索性切片并独立成熟。

| 阶段 | enabled_sources | 回退 |
| --- | --- | --- |
| Phase 1A | baseline、quant_model | 无回退；失败来源 unavailable + reason |
| Phase 1B | baseline、quant_model、llm_adjusted | LLM 失败/超时可复制同 case/release 的有效 quant 输出，标 fallback；quant 无有效输出则仍 unavailable |

Phase 1A 的 llm_adjusted 位置标 source_status=unavailable、reason=not_enabled，不计入 LLM 执行失败或计划来源分母。baseline 成功而 quant 失败时仍保留 baseline 结果，三种来源位置在同一个 commit 中封存。

Phase 1B 回退必须在 prediction_deadline 前完成，不能事后把缺失案例“补齐”。保留原始 LLM 缺失原因、回退所引用的 quant prediction_id、实际输出和政策版本。评估区分实际回退策略与 LLM 原始成功输出。

## 4. 评分与报告（Q7）

Phase 1A 主指标仅计算 D20：

```text
y_i = 1(realized_excess_return_i > 0)
d_i = (p_quant_i - y_i)^2 - (p_baseline_i - y_i)^2
```

d_i 越小越好。批次点估计是在该批可配对、准时、有 resolved Outcome 的 case 上平均 d_i。每个报告同时保留全量计划、启用来源、缺失、unscorable、unresolved、迟到和实际配对子集；子集均值不能宣称是缺失案例的收益。

月度报告按批次等权汇总点估计，列出证券数、计划批次数、可评分批次数与成熟标签数，避免把20只同周股票当作20个独立市场周期。没有可配对 case 的批次显示 NA，不用0填充。

### 4.1 Q7 待确认：区间方法

原提案是日期块自举、块长1日。但本协议每周预测且 D20 持有窗口跨多个周批次，独立抽取单个日期块不能保留窗口重叠。

已向用户提出以下两种方式，确认前不输出推断性区间或显著性结论：

- 建议方式：单批和早期报告只做描述统计；跨批次汇总按连续周批次做区块自举，保留每批完整横截面和成对关系。具体块长、样本门槛、重复次数、随机种子和评估窗口在查看结果前另行冻结；块长至少覆盖已登记日历下的 horizon 重叠跨度，不能由事后最好看的区间选择。
- 简化方式：v1 全部只做描述统计；区间方法作为后续新协议版本登记。

即使按重叠跨度选择块长，也需考察更长的时间依赖，不能自动保证区间校准。区块自举用于依赖数据的原始研究见 [Politis 与 Romano](https://www.stat.purdue.edu/docs/research/tech-reports/1991/tr91-03.pdf)。

### 4.2 报告时间与版本

每批 D20 到计划出场后开始解析；待该 horizon 的所有计划 case 有 resolved/unresolved/unscorable 认定后生成批次报告。无预测、取消或永久不满足入场条件的 case 也需要明确状态，不能让报告无限等待。异常数据等待上限依 Q5 最终协议，不通过提前填 unresolved 规避等待。

D1/D60 按各自时点单独生成探索性结果，不阻塞 D20 报告。每月汇总计划在纽约当地次月首个常规交易日06:00生成，以前一自然月末为数据截止；这是实施默认值，执行前固定到 schedule 配置。

月度报告按 campaign 的 cutoff 月份归属，列明其截至月末已成熟和未成熟结果。后续成熟或更正后追加新版本；不能把未成熟当0，也不静默覆盖旧报告。

报告固定 protocol/release/case/commit/outcome revision 清单和评分版本。试验比较每次查看都登记；Phase 1A 不依据月度多次查看自动 promote。

## 5. Release 与人类批准（Q8）

最小 manifest：

```text
release_id
target_spec_ids + content_hashes
time_protocol_ref + hash
campaign_policy_ref + hash
baseline_version + artifact_hash
quant_model_version + artifact_hash
training_manifest_ref + hash
feature_set_version
enabled_sources
fallback_policy
memory_policy = empty
memory_snapshot/set = empty
prompt_version = not_enabled
llm_model = not_enabled
code_commit / quant_image_digest / environment_lock_hash
release_content_hash
```

其中数据、模型与产物值只能在实际生成后填写。release_content_hash 覆盖规范化 manifest 的所有内容字段，排除自身 hash 和批准记录；批准记录引用该 hash，避免循环依赖。批准记录单独保存 approver_principal_id、approved_at、release_content_hash、scope 与审批依据。

批准人为项目所有者本人（人类用户），Git 作者配置不能代替认证身份。Agent 可以整理和提交候选，不能作为批准主体。当前消息并未批准任何尚未生成的模型、release hash 或正式运行。

审批存储和鉴权在 S02/S05 建立，从 S06 第一个正式 campaign 起强制执行；S00 先固定契约。回退、模型、数据特征、工具或协议发生变化时需要新的 release，不能重用已批准 hash。

## 6. Trial 登记与当前阻断项

从第一次用于选择模型或修改特征的比较开始，在 [docs/trials/registry.md](../trials/registry.md)事前登记，并追加结果、失败、放弃、更正和查看记录。协议编写、工具连通和合成已知答案检查不伪装成模型效果 trial。

Git 作为版本审计基础；本地历史可以重写，不能保证不可追溯修改。保护方式、外部锚定与后续 PG 迁移见 registry。

正式 campaign 必须同时具备：Q5/Q7 已定稿、已授权的 PIT 总体及 GICS 快照、真实20证券名单/hash、日历/tzdb、SPY 永久ID、模型及训练 manifest、预算配置、权限和持久执行验收、指向具体 hash 的人类批准。任何一项缺失时登记为未就绪，不创建正式预测。
