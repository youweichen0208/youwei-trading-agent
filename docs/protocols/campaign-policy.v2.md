# 批次、评估与批准政策 v2（候选）

protocol_id：campaign-policy-v2\
准备日期：2026-09-28\
状态：方案 (a) 的候选修订，供下一份 ResearchRelease 绑定；未批准实际 release，未登记或启动正式 campaign。v1 与其登记 hash 保留不变。

## 0. 修订范围

- 将分类依据由官方 GICS Sector 改为明示来源的 `eodhd_sector`；相似名称不构成分类等价证据。
- 具体化首次前向采集、完整证券 ID 映射冻结、manifest 交叉校验的登记要求，不声称这些工程检查已经实现。
- N=20、seed=20260927、配额/抽样算法、S&P 500 总体要求、SPY、D1/D20/D60、主 D20、时间协议、固定 panel、来源和评分规则沿用 v1。
- 本候选无独立上线权限；最终采用须由人类对绑定本文件 hash 的实际 release 批准。选择分类方案、通过本地测试和购买账号均不能代替该批准。

## 1. 使用范围（Q1）

仅自用或受控内部研究，不自动实盘交易，不向公众提供服务，不对外提供投资建议。未来扩大范围按 [ARCHITECTURE §13](../ARCHITECTURE.md#13-对外开放的增量条件)重新评估。

这是用途登记，不是法律或供应商许可的豁免结论。身份、请求、数据向模型传输和数据留存仍按实际授权配置。

## 2. 总体与固定研究样本（Q2 修订）

总体仍要求为正式样本登记时点有效的 S&P 500 成员，抽样分类改为 EODHD Indices Historical Constituents 产品返回的 `Components[].Sector`，登记名称为 `eodhd_sector`。采用该产品的决定只固定分类来源；成员资格、可用时点与授权仍须按实际证据核对。

**本版采用供应商板块分类，不宣称是官方 GICS 或与 GICS 等价。** 观察到 11 个板块、名称可大致对应，不足以证明逐证券归属、分类方法和历史修订相同。正式 GICS 根据企业活动按统一规则分类；后续若研究必须按官方 GICS 比较，另行取得满足需求的数据并建立新 protocol/panel/campaign/release。[MSCI GICS 定义](https://www.msci.com/indexes/index-resources/gics)

EODHD 产品页说明当前成员与部分指数的增删历史；这不能独自证明当前 Sector 字段有历史版本。[EODHD 产品说明](https://eodhd.com/lp/spglobal)。本项目已有响应与时间证据见 [实测记录](../research/eodhd-constituents-verification.md)。

本版首次 panel 的分类只采用**实际采集并冻结时观察到的值**。需要记录精确的 `observed_at`、`usable_at`、原始对象及内容 hash；无来源发布时间或分类生效时间时保留 null 和缺失依据，不把抓取时间写成供应商生效时间。不将当前分类附给历史成员后宣称为历史 PIT 分类。`frame_as_of` 日期不能单独证明任何时点的成员有效性；成员状态如不能满足原总体要求，登记继续阻断，不能借更换分类标签自动放行。

协议准备日期2026-09-28不代表已冻结实际 panel。首次计划 cutoff 必须晚于协议、授权、总体、ID 映射、分类快照及批准所要求的就绪时点，禁止为先前批次补造准时总体或预测。

抽样单位为唯一 permanent security_id 的成员证券行，N=20。多个股类可对应同一发行人，保留 issuer_id；不假定成员证券行数恰好500，也不保证样本为20家不同公司。抽样总体文件必须体现这个单位。

### 2.1 固定种子与名额分配

以下为使“分层固定种子抽样”可复现的实现约定，在获取样本表现前登记：

```text
sampler_version = sector-stratified-hash-v1
seed = "20260927"
stratification_level = eodhd_sector
classification_field = Components[].Sector
classification_semantics = vendor_observed_not_official_gics
sample_size = 20
frame_sort_key = security_id
```

设有 K 个非空板块，N_s 是各板块证券数，M 是总体证券数：

1. 要求 M >= 20、K <= 20，且每个成员有已冻结且非空的供应商 sector 值；不满足时停止登记，不静默删除缺分类的证券。
2. 每个板块先分1个名额。
3. 剩余20-K个名额，按 (N_s-1)/(M-K) 分配；先取整，再按余数降序分配剩余名额，同余按 sector code 升序。
4. 当 M=K=20 时直接每板块选1个；若剩余名额为0则不计算分母。
5. 每板块按如下 hash 升序取足名额，同 hash 按 security_id 排序：

```text
SHA256(UTF8(compact_JSON([
  sampler_version, seed, frame_hash, sector_code, security_id
])))
```

compact_JSON 固定数组顺序、对象键按字典序、无额外空格、UTF-8、无 BOM/结尾换行；输入标识使用已规范化的字符串。抽样总体规范化为仅含 security_id 和 sector_code 的对象数组，按 security_id 升序；frame_hash 对这些实际字节计算。原始成员与所观察分类快照、完整 security_id/issuer 映射另有源文件及 hash。sector_code 使用返回字符串原值（校验非空，不按 GICS 重命名、翻译或合并）；展示别名不参与抽样。选中名单为按 security_id 升序的字符串 JSON 数组，使用同一序列化规则计算 selected_list_hash。manifest 保存算法版本、种子、所有名额、源版本/映射及两个 hash。

上述规范化版本继续为 sampling-json-v1；未取得真实文件前 hash=null，禁止捏造。K01 合成已知答案继续适用。重跑相同**冻结 frame 与 ID 映射**必须得到相同名单；固定种子不保证新建随机 ID 后仍选中相同证券。

### 2.1.1 正式登记必须核对的证据

登记包需要保存或以不可变引用绑定：

- `protocol_ref` 与 `protocol_sha256`；本版 classification 来源、字段及 `stratification_level=eodhd_sector`；
- 原始响应、供应商/产品/端点、parser/source version、`raw_object_id`、原始内容 hash、精确观察/可用时刻及来源时间依据；
- 完整的 ticker/交易所到 permanent security_id 映射、映射内容 hash、标识有效期依据与规范化 frame 的实际字节及 hash；
- 种子、算法与规范化版本、每层成员数与配额、选中 security_id 列表及 hash、与选中证券对应的冻结板块；
- 所用数据的许可证据引用及正式登记环境的备份/恢复证据。

登记入口必须从源对象和映射重建 frame，核对 sample 的 frame hash、配额、选中名单、协议与调用方 panel_security_ids 一致。不同 frame 的 sample、任意填写的源对象或时间不得通过。仅检查 manifest 为字典或保留短 hash 前缀不满足这一要求。

正式抽样前固定证券映射，恢复时还原同一映射；不得删除库重建随机 UUID 后重新选取“更好”的名单。新增标识的有效期从有证据的时点开始，禁止无依据回溯至1990年。首次冒烟的名单与短 hash 保留为工程证据；只有完整登记包可恢复并通过校验，才可作为实际 panel。

### 2.2 固定样本与推断范围

首次抽样形成 panel_id；每周 Batch 复用同一 panel，不每周随当前 S&P 500 成分重抽。退出指数、分类变化、停牌、退市和缺数据都保留历史；不以事后表现或数据便利性更换成员。

固定 panel 的既有20个证券槽位继续进入计划，不能再研究/交易的证券标记状态并保留分母。未来确需换样本，创建新的 panel/protocol 版本，单独报告。

初始每批60个 case。报告默认证券等权、批次等权，结论限于这个固定样本；它既不复制 SPY 的市值权重，也不自动代表整个 S&P 500 的模型能力。分层和 SPY 基准不能消除所有选择偏差。

首次批准运行时同时登记连续周次计划的起点、频率和观察期；计划中的整周失败/停机也进入分母。只在有好数据的周生成批次会造成新的选择偏差，禁止借“未就绪”静默跳过已经计划的周次。

### 2.3 Campaign 与 Batch 层级（Q9）

```text
Campaign：事前登记的多周研究计划
  └─ Batch：一个 decision_cutoff 对应的周实例
       └─ ForecastCase：证券 × horizon
```

Campaign 固定 panel、目标、来源、频率、观察期、主指标和 release；Batch 保存具体 cutoff、prediction_deadline、入场时段及该批计划 case。月报按 Batch 的 cutoff 月份归属；Campaign coverage 覆盖全部预登记周次，包括漏跑和跳过的 Batch。Phase 1B 启用 llm_adjusted 必须创建新 Campaign 与新 release，不能仅在原 Campaign 的下一批次改变来源。

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

### 4.1 区间方法（Q7 已确认）

本版沿用 v1：单批报告与早期月报只做描述统计：点估计、coverage 与缺失/迟到披露。跨批次汇总启用连续周批次区块自举：保留每批完整横截面与成对关系；块长、样本门槛、重复次数、随机种子和评估窗口在查看结果前另行冻结登记。块长至少覆盖已登记日历下的 horizon 重叠跨度，并需考察更长的时间依赖；不能自动保证区间校准，也不能由事后最好看的区间选择。

满足参数登记与样本条件前，不输出推断性区间或显著性结论；方法启用属于后续工作，按 Trial 登记规则事前登记。区块自举用于依赖数据的原始研究见 [Politis 与 Romano](https://www.stat.purdue.edu/docs/research/tech-reports/1991/tr91-03.pdf)。

### 4.2 报告时间与版本

每批 D20 到计划出场后开始解析；待该 horizon 的所有计划 case 有 resolved/unresolved/unscorable 认定后生成批次报告。无预测、取消或永久不满足入场条件的 case 也需要明确状态，不能让报告无限等待。异常数据等待上限为统一宽限期：原计划 exit_at 起 5 个后续交易日（见 [target-spec](target-spec.v1.md)）；到期未齐即 unresolved，不提前、也不无限等待。

D1/D60 按各自时点单独生成探索性结果，不阻塞 D20 报告。每月汇总计划在纽约当地次月首个常规交易日06:00生成，以前一自然月末为数据截止；这是实施默认值，执行前固定到 schedule 配置。

月度报告按 Batch 的 cutoff 月份归属，列明其截至月末已成熟和未成熟结果。后续成熟或更正后追加新版本；不能把未成熟当0，也不静默覆盖旧报告。

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

正式 campaign 必须同时具备：已授权且满足总体要求的成员证据、冻结的 EODHD 分类、可恢复且已校验的完整 frame/ID 映射/panel 登记包、日历/tzdb、SPY 永久ID、正式模型及训练 manifest、预算配置、权限和持久执行验收、指向具体 release hash 的人类批准。Q5/Q7/Q9 规则继续沿用。任何一项缺失时保持未就绪。

预算按阶段登记：Phase 1A 不调用 LLM，建议明确配置 LLM 金额上限0、模型调用禁用；数据订阅和主机费用另列，不从此推导已获采购授权。Phase 1A 上线前确认数据的内部计算、留存、备份及终止订阅处理许可；LLM 转发授权在 Phase 1B 发送任何供应商数据前落实。未取得转发授权不能发送数据，但不把未使用的 LLM 能力算作 Phase 1A 的来源执行失败。

当前仍有工程收尾：证券 ID/有效期与恢复验证、完整源证据到 panel 的登记校验。已有采集与抽样冒烟证明局部链路可工作，不能据此写“仅剩人工项”。后续完成证据记录在 [实施计划](../IMPLEMENTATION_PLAN.md)。
