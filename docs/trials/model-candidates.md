# 模型候选规格与 Trial 预登记（S06 准备材料）

日期：2026-10-01  
状态：baseline 常量 + Logistic/Ridge 候选已实现，真实历史 Trial 已完成；D20 测试未优于基线。候选 release 已登记但未批准，详见 [Trial 结果](trial-001-results.md) 与 [真实冻结记录](../ops/s06-campaign-freeze-20261001.md)。现有 `quant-momentum-v0` 保留为旧 release 的工程载具。

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
- **特征（已在 [Trial 规格](trial-001-spec.v1.json) 固定）**：20/60交易日相对SPY总收益动量和20日总收益波动率；分别需要连续21/61个收盘价，不跳缺失日。
- **主目标**：D20（excess return > 0 的标签）；D1/D60 为探索性切片。
- **训练约束**（全部满足 PIT）：
  - 固定训练窗口、特征集合、预处理、正则化强度、标签成熟规则；
  - 训练/验证/测试切分 + purge/embargo；
  - 标签只在训练截止时已成熟可用（从实际entry计D1，在原exit后第5个后续交易日收盘作为保守历史可用假设；真实供应商当时可用时间未验证）；
  - 历史验证带 purge/embargo，不得用未来数据拟合。
- **概率输出**：Logistic Regression 输出的是**拟合概率**，本次无独立校准器；校准质量以真实结果中的分箱统计评估，不能默认视为已校准。
- **期望收益**：**不从胜率反推**；本次固定独立 Ridge（alpha=1，svd），使用同一组训练标准化后的特征，输出期望超额收益。规格在比较前登记。

Logistic Regression 是合适的简单概率模型候选，但**实际校准质量需要验证**——这是 Trial 的核心检验，不是默认成立。

## 3. 正式化步骤与当前证据

1. **Trial 登记与结果（已做）**：`registered`、规格固定、`started`、`completed` 均已追加到 [registry.md](registry.md)；失败/负向结果保留。
2. **实现与冻结（已做）**：`quant/logistic.py`、`quant/dataset.py`；产物为JSON数值参数，不使用pickle。
3. **历史验证（已做）**：按登记执行带purge/embargo的当前panel历史回顾。供应商原历史版本/可用时间未验证，不能称为严格历史PIT验证。
4. **training manifest（已做）**：独立候选库登记 `tm-logistic-ridge-candidate-20261001-v1`，固定特征集、预处理、拟合窗口、无独立校准、标签成熟及产物hash。
5. **release 引用绑定（已做）**：候选 release 已绑定 manifest/hash、每horizon模型参数、panel和日历；未来获批准才允许注册Campaign。
6. **人工批准 release**：生成具体 release hash，由项目所有者批准。

## 4. training manifest 现状

- 载具 manifest `tm-vehicles-v0`（`vehicle_training_manifest()`）已能登记，明确声明是工程载具（`fitting_window=none`、`calibration=none`、`label_maturation=none`）。
- Logistic/Ridge候选 manifest 已登记为新ID，未与载具 manifest 混用。
- 当前没有批准的正式 release；工程实现和候选登记不等于证明模型有增量。

## 5. Trial Registry 现状

- [registry.md](registry.md) 保持 `trial_count=1`，追加规格固定、`started`和`completed`；未更改原登记事件，未签署批准。
- 登记的是**试验计划**（假设、特征、时间切分、主指标、停止规则），不是结果；不等待正式 release 批准即可登记。
