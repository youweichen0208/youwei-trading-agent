# 模型候选说明与正式化要求（S06 准备材料）

日期：2026-10-01  
状态：准备材料，供项目所有者审阅与决策。未选择正式模型、未登记真实模型比较 Trial、未生成正式 release。

## 1. 现状：现有模型是工程载具，不是正式模型

Phase 1A 预测管线已用两个「工程载具」跑通，它们**不是正式模型**，只用于验证管线正确性（`quant/models.py` 文档字符串明示「not approved production models」）。

| 载具 | 版本 | 行为 | 局限 |
| --- | --- | --- | --- |
| baseline | `baseline-constant-v0` | 常量 `p_outperform=0.5`、`expected_excess_return=0`，不消费任何市场证据 | 是「无信息基线」的合理起点，但正式基线是否用历史基准率（如长期 SPY 胜率）需要决策 |
| quant | `quant-momentum-v0` | 20 日动量直接映射：`p = clip(0.5 + momentum, 0.05, 0.95)`、`expected = clip(momentum, -0.5, 0.5)` | **把动量直接当概率**，无拟合窗口、无校准、无标签成熟、无历史验证——这是管线占位，不是可辩护的预测模型 |

关键结论（与 [ARCHITECTURE §8](../ARCHITECTURE.md) 一致）：`quant-momentum-v0` 把动量分数线性映射为概率，**不能未经重新设计、验证和登记就提升为正式量化模型**。正式模型的概率必须来自「冻结的、过去数据拟合的模型/映射」，不能把任意动量分数直接当概率。

## 2. 正式模型候选方向（待项目所有者决策）

以下只列出候选方向与各自必须补齐的验证，不在此替所有者做选择。

### 2.1 baseline

- **候选 A**：保留 `p=0.5` 常量（纯无信息基线）。最保守、最可辩护，但作为「相对基准」信息量低。
- **候选 B**：历史基准率（如全样本 SPY 超额为正的长期频率），用冻结历史数据拟合一个常数。需要：拟合窗口 PIT 干净、样本量、以及「基准率本身不引入未来信息」的证明。
- 无论选哪个，都必须明确：baseline 的 `p` 从何而来、是否消费证据、是否含拟合参数。

### 2.2 quant

- **候选 C（简单校准动量）**：在冻结历史窗口上，把动量信号映射为概率时**实际拟合校准**（如 logistic 或分箱频率），而非线性 `0.5 + momentum`。需要：训练/验证/测试切分、purge/embargo、PIT 标签成熟、样本量与校准曲线披露。
- **候选 D（其它因子）**：换用其它已测试因子。需要同样的验证链路。
- **候选 E（保持占位 + 明确降级）**：若 MVP 只想跑通三组对照而不宣称 quant 有效，可保留载具但**在 manifest 和披露中明确其非正式、非校准性质**，并把「quant 改善」的解释严格限定为管线验收，不做统计推断。

## 3. 正式化所需步骤（顺序固定）

1. **Trial 登记**：首次用于「选择/比较模型」的试验，在查看结果前登记到 [registry.md](registry.md)。当前没有真实模型比较发生，因此**尚无真实 Trial 可登记**——候选方向确认后、实际比较开始前登记。
2. **重新设计与冻结**：按选定方向实现模型，冻结公式、窗口、特征、缺失政策。
3. **历史验证**：纯量化候选做带 purge/embargo 的 PIT 历史验证（[ARCHITECTURE §8](../ARCHITECTURE.md)）；LLM 相关候选走前向 shadow。
4. **training manifest 登记**：用 `register_training_manifest` 登记正式的 `training_manifests` 记录，显式声明特征集、预处理、拟合窗口、校准、标签成熟（五项固定事实，`none` 须明说）。
5. **release 引用绑定**：campaign 注册时 `training_manifest_ref + sha256` 解析到已登记 manifest，`baseline_version/quant_model_version` 与其模型一致。
6. **人工批准 release**：生成具体 release hash，由项目所有者批准。Agent 不填批准人、时间或放行标志。

## 4. training manifest 现状

- 载具 manifest `tm-vehicles-v0`（`vehicle_training_manifest()`）已能登记，且 artifact hash 锁定 `quant/models.py` 实际字节。它**明确声明是工程载具**，`fitting_window=none`、`calibration=none`、`label_maturation=none`。
- 正式模型的 manifest 待选定模型后登记（新的 `manifest_id`），不与载具 manifest 混用。
- 未在本材料中登记任何「正式」training manifest，因为没有正式模型被选定。

## 5. Trial Registry 现状

- [registry.md](registry.md) 已有登记格式与追加约束，`trial_count=0`，没有真实比较试验。
- 本材料不虚构任何 Trial 记录。首次模型比较开始前，由项目所有者确认候选方向后，我再按格式登记真实的 `registered` 事件。
