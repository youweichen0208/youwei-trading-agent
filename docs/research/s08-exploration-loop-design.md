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
  │ 回合 N 输出 experiment_request → 校验边界 → **持久登记实验授权**(Core 表:绑定 tenant/run/job/attempt/case、实验 ID、冻结快照、执行配置;限额:计算次数/并发/累计时长/产物大小)
  │ → 向 Runner 登记同一授权(控制面,含快照注入源与限额)→ 派实验实例
  │ 简报 = 冻结快照说明 + 明确问题
  │ 生成代码(不可信输入)→ sandbox_submit → sandbox_status → artifact_read
  ▼
Runner(执行权威;工具端点按操作分别验 Ed25519;**计算回执持久化,重启后幂等仍成立**)
  │ 沙箱容器执行生成代码(无网络、无密钥、无工具令牌、只读冻结 bar)
  ▼
实验实例返回实验结果(findings/warnings/代码/产物 hash)
  ▼
Controller **从 Runner 回执核验代码/镜像/快照/产物 hash + 当前 fencing 再校验**后接纳,append-only 登记 → 重入研究实例(回合 N+1,实验结果附入上下文)
  ▼
研究实例返回最终提案,引用 kind="code"、locator="experiment:<id>/computations/<computation_id>/artifacts/<path>"
```

- 取消传播:父任务取消、租约失效或 attempt 更替 → Controller 调 Runner 终止实验(拒绝新增计算 + 清理在运行容器),令牌过期为纵深;最终接纳时 fencing 再次校验。
- 研究/实验实例**都不经网络直接触达 Core**:实验实例的工具调用只到 Runner 工具端点(见 D1)。
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
- 引用解析:contracts 增加 locator 形态 `experiment:<uuid>/computations/<computation_id>/artifacts/<path>`(kind="code",含 computation ID 保证不同计算的同名文件不冲突),校验产物 hash 一致。

## 5. 决策记录(2026-10-02 所有者确认:均选 A;D2 附五项最小要求)

**D1 工具通路 = A**:实验容器可访问网关 + 专用实验工具入口。限定:①实验容器:仅网关 + 工具端点;②研究容器:维持现有权限(仅网关);③执行生成代码的沙箱:继续无网络、无密钥、无工具令牌。Runner 工具入口只开放获授权的实验操作;**用网络探针 + HTTP 越权测试共同验证,实验令牌不能调用 Runner 控制接口**(/v1/executions、/v1/research-invocations 等)。

**D2 计算执行归属 = A + 执行前登记与恢复能力**(轻量计算执行,不建完整 Core job):

```
Controller 持久登记实验授权
  → Runner 执行并保存计算回执
  → Controller 校验当前 fencing 后接纳结果
```

最小要求(实施与评审依据):
1. **派发前登记**:绑定 tenant/run/job/attempt/case、实验 ID、冻结快照与执行配置;限定计算次数、并发、累计时长与产物大小。
2. **持久幂等**:同实验、同 computation ID、同 payload 返回原回执;不同 payload 拒绝。**Runner 重启后仍成立(内存字典不足,回执存储落盘)**。
3. **取消传播**:父任务取消、租约失效或 attempt 更替后,拒绝新增计算并清理相关容器;最终提交再次校验 fencing。
4. **可信执行证据**:Controller 从 Runner 回执核验代码、镜像、快照及产物 hash;失败、超时和中断也留痕。
5. **产物引用唯一**:不同 computation 的同名文件不产生引用冲突(locator 含 computation ID)。

**编排成本提示**(知会):研究↔实验是多回合编排,管线改动集中在 Core worker 的研究 fetcher 侧;每 case 总时延与 LLM 调用次数显著高于单回合(实测单研究回合 84-224s)。

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
- 实验授权派发前持久登记(绑定 + 限额:次数/并发/累计时长/产物大小);计算回执落盘持久,Runner 重启后同键同内容幂等、异内容拒绝。
- 父任务取消/租约失效/attempt 更替 → 拒新增计算 + 清理容器 + 最终接纳时 fencing 再校验;失败、超时、中断均留痕(回执含代码/镜像/快照/产物 hash)。
- 网络隔离按 D1 限定:实验容器仅 {网关, Runner 工具端点},研究容器仅网关,沙箱无网络;越权面(实验令牌 × 控制端点)以 HTTP 测试验证拒绝。
