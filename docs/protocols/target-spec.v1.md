# 预测目标协议 v1

protocol_id：target-spec-v1\
登记日期：2026-09-27\
状态：horizon、基准、收益口径与停牌政策已确认（2026-09-27）；正式 campaign 仍待 S01+ 依赖解除。

## 1. 目标集合

| 字段 | 值 |
| --- | --- |
| target_spec_id | 分别为 excess-tr-d1-v1、excess-tr-d20-v1、excess-tr-d60-v1 |
| horizon_td | 1、20、60 |
| primary_horizon_td | 20 |
| exploratory_horizons_td | 1、60 |
| benchmark_policy_id | spy-total-return-v1 |
| benchmark | SPY；永久 security_id 及有效标识待 S01/S04 登记 |
| currency | USD |
| return_type | 毛总收益：计分红，不计税、交易成本与滑点 |
| binary_event | realized_excess_return > 0；等于0记 false |
| predictions | p_outperform、expected_excess_return |

三个 horizon 分别形成 case，不合并标签；D60 需要60个真实交易日，不能承诺恰好三个月。每批初始20证券 × 3 horizon = 60个计划 case；每个 release 保留180个来源位置。Phase 1A 最多120个来源位置产生有效预测，另外60个 llm_adjusted 为 unavailable/not_enabled。

## 2. 收益与持有窗口

```text
R_security = V_security(exit_at) / V_security(entry_at) - 1
R_benchmark = V_benchmark(exit_at) / V_benchmark(entry_at) - 1
realized_excess_return = R_security - R_benchmark
```

V 表示单位初始投入持有到相应时点的财富，包含有权获得的公司行为与再投资。它不等于随意混用 adjusted close 和 raw open。

正常情形：entry 是 cutoff 后下一常规时段开盘；该日算 D1，D1 在当天常规收盘退出，D20 在第20个交易日收盘，D60 同理。SPY 必须使用完全相同的起止时点与公司行为口径。时间规则见 [time-protocol.v1.md](time-protocol.v1.md)。

目标本身固定于 TargetSpec 内容 hash；case 保存实际证券、benchmark security_id 和解析后的窗口。目标或窗口不同不能共享一个 Outcome 作为同 case 的来源对照。

## 3. 分红、拆分与并购

### 3.1 现金分红

采用用户选择的除息日收盘再投资约定，仅对除息前已持有并有权获得分红的数量计入现金。若在除息日开盘才入场，不计入该次分红。

对于当日有权领取的分红 D，在除息日收盘价 P_close 再投资，相当于持仓数量乘以 (1 + D/P_close)。这是研究总收益的合成记账约定，不代表实际在派息日之前收到并可用于交易的现金。税后收益和可执行现金账户回测属于其他 TargetSpec。

再投资价格不可用时不能改用未来价格；按缺失结果政策处理。退出恰在除息日收盘时仍计入应得权益，避免漏记分红或重复记现金。

### 3.2 拆分与其他证券分派

依条款和生效时点调整数量与证券标识；2:1 拆分使数量翻倍，本身不创造收益。入场价格已经在拆分后时，不再重复应用拆分因子。

股票分红、分拆等形成的可估值证券权益纳入持有组合；缺少条款、标识映射或估值数据则 unresolved，不凭当前行情推定历史价值。

### 3.3 并购与退市

现金并购按法律/公司行为生效时点确定对价；现金部分按零利息持有至原计划 exit_at。换股部分跟踪继承证券至同一 exit_at，不能在并购日提前终止 benchmark 收益窗口。

明确且经核实的退市对价按条款计算；确认普通股权益归零时可使用有证据的0终值。仅因缺行情或未知终值不能填0，记 unresolved 并保留事件、估值依据与更正路径。

退出指数不等于退市。固定研究样本中的证券退出 S&P 500 后不因此删除既有或后续计划记录。

### 3.4 总收益供应商验证

可使用经 S01 验证、与本协议等价的供应商总收益序列，但必须保留原始 open/close、公司行为、源版本和验证报告。不能因字段名叫 total_return 就假定其再投资、税费、入场或公司行为口径一致。

除息日再投资是总收益指数中使用的研究约定；本项目具体事件资格、日内起止和异常处理仍以本协议为准。[S&P DJI 总收益说明](https://www.spglobal.com/spdji/en/education/article/faq-sp-500-dividend-points-index/)

## 4. 停牌与无价格（固定目标窗口，已确认）

固定窗口为唯一正式政策：不移动入场/出场时点，不用复牌价格替代原目标时点价格。顺延成交仅可用于后续另行定义的可交易策略回测，须单独定义实际窗口、顺延上限与同窗口 SPY 收益，不与本协议 case 混同。

区分“市场未形成价格”与“供应商晚到”：

- 原入场时段确实没有形成有效开盘价格（停牌、无成交）→ case 记 unscorable，保留在计划分母。
- 原时点存在价格而供应商尚未提供 → 属于数据缺失，不得判为 unscorable，按宽限期政策处理。

统一结果解析宽限期：从原计划 exit_at 起算，最多 5 个后续交易日。到期时任何必要数据仍缺失——包括入场价、出场价或公司行为——该 case 记 unresolved。不为入场数据另设期限：入场价在 exit 后的解析期内等待，上限即统一宽限期；D1 也没有更长的等待时间。

宽限期后取得原时点数据时追加新的 OutcomeRevision；永不用复牌日或其他时点价格替代原目标时点。原窗口中的 unresolved/unscorable 不能静默删除。

股票所属市场与 SPY 的常规交易时段不一致、市场临时关闭、数据未通过质量检查时也进入明确异常状态，禁止模型自行选择替代时间或价格。

## 5. Outcome 与修正

每个 horizon 到达原计划 exit_at 后开始解析。必要数据（入场价、出场价、公司行为）在统一宽限期内齐备才记 resolved；宽限期到期仍缺失记 unresolved；原入场时段确无有效价格形成记 unscorable，与数据晚到无关。

所有状态认定都属于结果版本；后续取得可接受证据时追加新 OutcomeRevision，保留原报告。修正不改变原预测、目标和及时性资格。

源特征缺失和结果价格缺失分别记录；既不能用当前特征回填预测输入，也不能为凑齐评估任意填补结果。
