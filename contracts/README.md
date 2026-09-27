# 平台执行契约

`youwei-contracts` 是 Core 与独立 Runner 共同依赖的轻量包，只依赖 Pydantic 与标准库。Python 导入路径为 `youwei_contracts`；根 Core 与 Runner 各自用自己的锁文件引用这个本地包。

当前实现 `sandbox-v1`：`SandboxRequest`、`SnapshotBundle`、`ExecutionResult`、`ExecutionStatus` 和带路径/尺寸/hash 校验的 `Artifact`。JSON 不接受额外字段；请求不能携带镜像、宿主挂载或网络设置。研究提案等未来契约在实际接入时新增。

共享能力令牌的编码位于 `capability.py`；Runner 使用与 Core 通用能力令牌不同的专用签名密钥，范围绑定作业、attempt、租户、规范化请求 hash 和有效期。Worker 在每次签发/续期时检查数据库中的当前租约，Runner 的缓存不构成业务状态来源。

修改 wire 字段、默认值、规范化算法或异常语义时，同步两端并运行 `tests/contracts/` 和跨进程集成测试。破坏已有部署兼容性时新增契约版本及迁移方式；不能只升级一端的包版本。
