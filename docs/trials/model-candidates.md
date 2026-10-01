# 模型候选规格与 Trial 预登记（S06 准备材料）

日期：2026-10-01  
状态：候选方向已确定（baseline 常量 + quant 正则化 Logistic Regression）；Trial 已做 `registered` 预登记、结果留空；正式 release 未批准。现有 `quant-momentum-v0` 保留为工程载具。

## 1. 现状：现有模型是工程载具，不是正式模型

Phase 1A 预测管线已用两个「工程载具」跑通，它们**不是正式模型**，只用于验证管线正确性（`quant/models.py` 文档字符串明示「not approved production models」）。

| 载具 | 版本 | 行为 | 局限 |
| --- | --- | --- | --- |
| baseline | `baseline-constant-v0` | 常量 `p_outperform=0.5`、`expected_excess_return=0`，不消费任何市场证据 | 作为无信息基线可保留，但需明确其「不消费证据」的定位 |
| quant | `quant-momentum-v0` | 20 日动量直接映射：`p = clip(0.5 + momentum, 0.05, 0.95)` | **把动量直接当概率**，无拟合窗口、无校准、无标签成熟——管线占位，不提升为正式模型 |

关键结论（与 [ARCHITECTURE §8](../ARCHITECTURE.md) 一致）：`quant-momentum-v0` 把动量分数线性映射为概率，**不能未经重新设计、验证和登记就提升为正式量化模型**。正式模型的概率必须来自「冻结的、过去数据拟合的模型/映射」。

## 2. 已确定的候选规格（2026-10-01）

### 2.1 baseline

- **常量 `p_outperform=0.5`、`expected_excess_return=0`**，不消费任何市场证据。这与现有 `baseline-constant-v0` 语义一致，正式化时不改公式，只固定其定位（无信息基线、无拟合、无校准、无标签）。

### 2.2 quant：正则化 Logistic Regression

- **模型**：用少量价格特征的 L2 正则化 Logistic Regression，预测「该证券在目标窗口内是否跑赢 SPY（excess return > 0）」这一二分类标签。
- **特征（候选，需在 Trial 中固定）**：动量、波动率等少量价格衍生特征（具体集合在 Trial 的 `parameters` 里登记，登记后不可改）。
- **主目标**：D20（excess return > 0 的标签）；D1/D60 为探索性切片。
- **训练约束**（全部满足 PIT）：
  - 固定训练窗口、特征集合、预处理、正则化强度、标签成熟规则；
  - 训练/验证/测试切分 + purge/embargo；
  - 标签只在训练截止时已成熟可用（D20 标签须在 cutoff 后 20 交易日 + 宽限期才成熟）；
  - 历史验证带 purge/embargo，不得用未来数据拟合。
- **概率输出**：Logistic Regression 输出的是**校准后的概率**（不是线性映射），实际校准质量需在 Trial 中验证（校准曲线、Brier）。
- **期望收益**：**不从此胜率直接反推**。若正式预测需要 `expected_excess_return`，必须单独定义收益预测模型（如分位数或回归），在 Trial 中登记其规格。Phase 1A 若只输出概率而不输出收益点估计，须在 manifest 中明示 `expected_excess_return` 的语义（none 或单独模型）。

Logistic Regression 是合适的简单概率模型候选，但**实际校准质量需要验证**——这是 Trial 的核心检验，不是默认成立。

## 3. 正式化步骤（Trial 已预登记，其余待执行）

1. **Trial 登记（已做）**：`registered` 事件已写入 [registry.md](registry.md)，结果引用留空。
2. **实现与冻结**：按 §2 规格实现，冻结公式、窗口、特征、缺失政策。
3. **历史验证**：按 Trial 登记的切分/指标/停止规则执行，带 purge/embargo 的 PIT 验证。
4. **training manifest 登记**：`register_training_manifest` 登记正式 manifest（特征集、预处理、拟合窗口、校准、标签成熟五项固定事实）。
5. **release 引用绑定**：campaign 注册时 `training_manifest_ref + sha256` 解析到已登记 manifest。
6. **人工批准 release**：生成具体 release hash，由项目所有者批准。

## 4. training manifest 现状

- 载具 manifest `tm-vehicles-v0`（`vehicle_training_manifest()`）已能登记，明确声明是工程载具（`fitting_window=none`、`calibration=none`、`label_maturation=none`）。
- 正式 Logistic Regression 模型的 manifest 待实现并验证后登记（新 `manifest_id`），不与载具 manifest 混用。
- 当前未登记任何「正式」training manifest（正式模型尚未实现）。

## 5. Trial Registry 现状

- [registry.md](registry.md) 已登记 1 条 `registered` 事件（见下文），`trial_count=1`，结果引用留空，无 `started`/`observed`/`completed` 事件。
- 登记的是**试验计划**（假设、特征、时间切分、主指标、停止规则），不是结果；不等待正式 release 批准即可登记。
