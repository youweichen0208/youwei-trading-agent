# 平台执行契约

`youwei-contracts` 是 Core、独立 Runner 与 Agent Runtime 共同依赖的轻量包，只依赖 Pydantic 与标准库。支持 Python 3.13/3.14；Core/Runner 保持3.13，Agent Runtime 保持3.14，各自用自己的锁文件引用本地包。

当前实现 `sandbox-v1`：`SandboxRequest`、`SnapshotBundle`、`ExecutionResult`、`ExecutionStatus` 和带路径/尺寸/hash 校验的 `Artifact`。JSON 不接受额外字段；请求不能携带镜像、宿主挂载或网络设置。

`research-v1`（S07b–e）：`FrozenEvidence`（计划中的 Controller 冻结 case + 证据快照）与 `ResearchProposal`（Hermes 返回的 `llm_adjusted` 研究提案，仅 produced/unavailable，不携带 fallback）。Controller 转成 `sealing.SourcePrediction` 的封存接线尚未实现；Hermes 无 Ledger 写权限。

## 冻结证据引用

`kind="evidence"` 的 locator 格式为 `snapshot:<snapshot_id>/rows/<index>`；index 是冻结 content 数组的原始零基位置，必须为规范 ASCII 非负整数，不接受负数、前导零、URL、自由文本或旧冒烟占位符 `row-0`。快照 hash 固定数组内容和顺序；展示中的排序/过滤不能重新编号。

```python
from youwei_contracts.research import (
    ResearchReference, evidence_row_locator, resolve_reference,
    validate_proposal_references,
)

reference = ResearchReference(
    kind="evidence", locator=evidence_row_locator(evidence, 0)
)
row = resolve_reference(evidence, reference)
rows = validate_proposal_references(evidence, proposal)
```

- `resolve_reference` 校验 kind、快照、位置及当前内容 hash，返回行的深拷贝；没有网络、文件或数据库访问。
- `validate_proposal_references` 校验 run/case 绑定，并解析所有引用。当前 bundle 无 memory/code/model 的授权解析信息，因此这些类型在该校验入口拒绝；它们仍可表示为 wire 数据，不能因此视为已验证。
- 行是否存在不证明论断成立；本函数不新增引用数量或论断覆盖率政策。空引用列表不提供任何证据支持证明。
- 输入必须由可信调用方授权提供。一个 batch 的快照可以含多只证券，解析范围是该完整快照；不另行按当前 case 的证券过滤，也不授予快照读取权限。
- 实际消费方是 Agent Runtime：简报提供定位符与完整行，`run_research` 在返回前校验。`parse_proposal` 只处理 wire/值域；未来 Controller 必须从授权存储重取绑定证据并再次校验，然后执行租约、时间、预算和封存检查。

本次收紧尚未用于正式研究部署的 `research-v1` 引用语义；冒烟脚本已同步，后续研究 release 必须记录更新后的简报/适配代码版本。目标机真实 Hermes/网关联调单独验收。

共享能力令牌的编码位于 `capability.py`；Runner 使用与 Core 通用能力令牌不同的专用签名密钥，范围绑定作业、attempt、租户、规范化请求 hash 和有效期。Worker 在每次签发/续期时检查数据库中的当前租约，Runner 的缓存不构成业务状态来源。

修改 wire 字段、默认值、规范化算法或异常语义时，同步两端并运行 `tests/contracts/` 和跨进程集成测试。破坏已有部署兼容性时新增契约版本及迁移方式；不能只升级一端的包版本。
