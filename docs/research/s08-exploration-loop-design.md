# S08 受控量化探索闭环:往返契约设计

日期:2026-10-02(设计稿,待所有者确认两个决策点后实施)。依据:[ARCHITECTURE](../ARCHITECTURE.md) §量化探索、[实施计划 S08](../IMPLEMENTATION_PLAN.md)、S07i 工具收敛记录(四工具名不预先承诺)、S07m/S07n 研究链路实测。本设计不启动 Phase 1B:探索闭环的正式使用依赖 release 批准与数据转发授权;本设计只固定契约形态。

## 1. 完整探索用例(接口由此决定)

Phase 1B 某研究回合(case X,D20):冻结证据只含本证券与 SPY 的日线 bar。研究实例判断需要标准 quant 库未覆盖的统计量——**"本证券/基准的 20 session 滚动已实现波动率比,以及当前比值在自身历史中的分位数"**。该计算确定性、只读冻结 bar、产物为小 JSON——足以驱动完整往返:研究实例请求 → Controller 派实验实例 → 实验实例生成代码 → 沙箱执行 → 产物引用 → 研究实例消费 → 提案。

## 2. 角色与回合编排(按既定架构,非新决策)

```
研究实例(Hermes, toolset=youwei-research, 仅 snapshot_manifest)
  │ 回合 N 输出: proposal 或 experiment_request{question, motivation}
  ▼
Controller(Core worker,持有 Ed25519 签发密钥)
  │ 校验请求边界(每 case 限 K 次、问题长度、声明用途)→ 签发实验授权
  ▼
实验实例(独立 Hermes 容器, toolset=youwei-experiment, 与研究实例无共享记忆)
  │ 简报 = 冻结快照说明 + 明确问题
  │ 生成代码(不可信输入)→ sandbox_submit → sandbox_status → artifact_read
  ▼
Runner(执行权威;工具端点按操作分别验 Ed25519)
  │ 沙箱容器执行生成代码(无网络、无密钥、资源受限、只读冻结 bar)
  ▼
实验实例返回实验结果(findings/warnings/代码/产物 hash)
  ▼
Controller 校验并登记 experiment record(append-only)→ 重入研究实例(回合 N+1,实验结果附入上下文)
  ▼
研究实例返回最终提案,引用 kind="code"、locator="experiment:<id>/artifacts/<path>"
```

- 研究/实验实例**都不经网络直接触达 Core**:实验实例的工具调用只到 Runner(见决策 D1)。
- 标准量化不经此循环(直接执行库函数);本循环只为"库未覆盖、需生成代码"的探索。
- 每 case 实验次数有上限(K,默认 1-2),避免回合膨胀;实验结果附入研究上下文有字节上限。

## 3. 工具接口(实验实例侧,youwei-experiment toolset)

按 S07i"不预先承诺名称"原则,接口由用例决定为三操作,名称实施时可调:

| 操作 | 语义 | 授权(分别签发) |
| --- | --- | --- |
| `sandbox_submit` | 提交一次计算:code + argv + SBX_* env + 期望产物扩展名 + 超时提示;绑定 experiment_invocation_id + computation_id + 冻结快照 hash;幂等(同 computation_id 同内容重放返回原状态) | scope `experiment:submit` |
| `sandbox_status` | 查询计算状态:running / succeeded / failed / cancelled / timeout;**partial 语义**——失败/超时/取消时已落盘的产物仍可读(带 partial 标记) | scope `experiment:status` |
| `artifact_read` | 按 (computation_id, path) 读取单个产物,内容 hash 校验,大小上限;只读 | scope `experiment:read` |

- 授权载体:Controller 签发的 Ed25519 令牌,新 audience `runner-tools`(区别于 `runner-exec` 生命周期入口),绑定 tenant/run/job/attempt/case/experiment_invocation_id/快照 hash/有效期;Runner 每次调用重验(与研究链路同模式)。
- 代码与环境引用:产物与实验记录均记 code_sha256 + 环境(镜像 digest + SBX_* env),保证可复现归因。
- warnings:沙箱 stderr 的有界摘录 + 实验实例自报 warnings 都进实验记录。

## 4. 契约(wire)形态

- `ExperimentRequest`(研究实例 → Controller):question、motivation、requested_shape(声明要算什么,供 Controller 校验边界)。
- `ExperimentComputationRequest`(实验实例 → Runner):contract_version `experiment-v1`、experiment_invocation_id、computation_id、code(≤1MB,同 sandbox-v1 script 界)、argv、env(SBX_*)、expected_extensions、timeout_seconds;快照由 **Runner 注入**(实验实例不可选快照——与研究链路网关注入同哲学:调用方不能重定向输入)。
- `ExperimentComputationStatus`:状态 + 有界 stdout/stderr 摘录 + 产物清单(path/size/sha256,不含内容)+ partial 标记。
- `ExperimentResult`(实验实例 → Controller):findings(有界文本)、warnings、computations 清单(每项:code_sha256、状态、产物 hash)、引用的产物路径。
- `ExperimentRecord`(Controller 持久化,append-only):request、result、快照 hash、代码 hash、产物 hash、时间戳;供 `experiment:<id>/artifacts/<path>` 引用解析。
- 引用解析:contracts 增加 locator 形态 `experiment:<uuid>/artifacts/<path>`(kind="code"),校验产物 hash 一致。

## 5. 决策点(需所有者确认,改变权限/隔离语义)

**D1 工具通路**(实验容器的工具调用如何到达 Runner):
- **方案 A(建议)**:扩展批准出口为 {网关, Runner 工具端点}——Runner 以受控接口接入实验网络,工具调用走 HTTP + Ed25519;net-probe 验证仅此两目的地可达。与现网关接线同模式,标准、可测;代价是研究网络的批准目的地从 1 个变 2 个。
- 方案 B:Unix socket 侧信道——Runner 把 socket 传入容器,工具走 socket,网络保持仅网关。信任边界与 A 完全相同(都是 Runner 中介 + 令牌授权),只是传输层;代价是自定义协议与挂载管道,测试面更差。

**D2 计算执行归属**:
- **方案 A(建议)**:Runner 按实验键直接执行(复用其沙箱容器执行核心,键 = experiment_invocation_id + computation_id,幂等/可取消/超时杀),产物有界内联返回;Controller 收到实验结果后统一登记 Core(append-only experiment_records)。避免从容器内嵌套发起 Core job 生命周期。
- 方案 B:每次计算走完整 Core job(复用租约/fencing/store_artifacts)——复用最大,但要求容器内可达 Core 提交通道(网络面更大),且子回合计算套完整 job 生命周期(租约、attempt)过重。

**编排成本提示**(非决策,知会):研究↔实验是多回合编排(回合 N 请求 → 实验 → 回合 N+1 消费),管线改动集中在 Core worker 的研究 fetcher 侧;每 case 的总时延与 LLM 调用次数显著高于单回合(实测单研究回合 84-224s)。

## 6. 实施切片(建议顺序)

1. 契约层:`experiment-v1` wire 类型 + locator/解析 + Ed25519 scopes/audience + 纯逻辑测试(本设计获批后即可动工,不依赖 D1/D2 的传输细节)。
2. Runner 工具端点:submit/status(+cancel)/read,Ed25519 验签、幂等、沙箱执行复用、产物有界返回。
3. Controller 编排:实验请求校验/上限、实验实例派发(复用 research-invocation 机制 + youwei-experiment toolset)、experiment_records 登记、研究回合重入。
4. 端到端样例:§1 用例全链(研究 → 实验 → 沙箱 → 引用 → 提案),mock 网关零成本验收 + SG 真实网关小样本。
5. Trial 登记:探索用例作为实验候选登记(不把生成代码热加载为生产库)。

## 7. 不变式(评审依据)

- 生成代码只在无网络、无密钥、资源受限的沙箱跑;实验/研究实例维持工具允许列表,无 bash/文件/浏览器工具集。
- 快照由 Runner/Controller 注入,实例不能自选输入;一切外部文本(代码、产物、findings)按不可信输入校验(长度/hash/类型/路径)。
- 提交、状态、读取分别授权;令牌绑定实验 invocation 与快照 hash;Runner 每调用重验。
- 实验记录 append-only;引用可解析回 hash 一致的产物;研究/实验实例不共享可演化记忆。
- 标准量化路径不经 Agent 循环;探索候选登记 trial,不热加载。
