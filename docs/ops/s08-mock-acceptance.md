# S08 隔离环境 mock 验收记录（网络探针 + HTTP 越权实测 + 端到端冒烟）

日期：2026-10-03\
依据：[s08-exploration-loop-design](../research/s08-exploration-loop-design.md) D1=A（实验容器=网关+工具端点；研究容器维持仅网关；沙箱无网络）、D2 最小要求；[实施计划 S08c](../IMPLEMENTATION_PLAN.md)\
执行主机：sg-prod（Docker 29.5.2，真实容器与 internal 网络，零 LLM 费用——模型为脚本化 SSE mock）

## 结论

**三阶段全部通过（verdict: 0 failure(s)）**：网络隔离按 D1 限定成立；HTTP 越权矩阵 17/17 拒绝（含对照组）；探索闭环端到端贯通（研究回合 → 实验请求 → 授权登记 → 实验实例（真容器，工具经 Tool Search 桥真实调用工具面）→ 沙箱计算（真容器，Runner 注入快照）→ 回执核验 → 研究重入提案以 kind="code" 引用产物 → 引用解析到回执产物）。

## 验收拓扑（一次性，驱动脚本自建自拆）

```
youwei-research (--internal)   研究容器 + mock 网关
youwei-experiment (--internal) 实验容器 + mock 网关(双网接入) + Runner 容器(docker socket)
                                └─ 沙箱计算容器 (--network none, python:3.13-alpine)
mock-gateway                    脚本化 SSE 模型（experiment_mock_gateway.py，零费用）
s08-accept-runner               youwei-runner:development（experiment store + 工具端点 http://s08-runner:8091）
驱动                             宿主进程（仓库 venv），经 Runner 容器 IP 直连（internal 网络上 docker-proxy 发布端口不可靠，已绕开）
```

镜像：agent-runtime `youwei/agent-runtime:s08c`（manifest digest `sha256:66899225b3ffa9c66b16e72c32db93768048747b5988e8c18ff9e64c4c9204ac`，含 `experiment-once` 入口与 youwei-experiment 工具集）；Runner `youwei-runner:development`（本仓库当前代码构建）。

## A. 网络探针（experiment_net_probe.py，容器内执行）

**实验容器（youwei-experiment 网络）**：

| 检查 | 结果 |
| --- | --- |
| DNS 解析 Runner 别名（172.28.0.3）/ mock 网关别名（172.28.0.2） | ✓ |
| Runner 工具端点 `/healthz`（HTTP 200，`experiment: experiment-v1`） | ✓ |
| mock 网关可达 | ✓ |
| 外网 8.8.8.8 / 1.1.1.1 | `Network is unreachable`（阻断）✓ |
| docker socket 网关 172.17.0.1:2375 | 阻断 ✓ |
| 宿主公网 IP :80 | 阻断 ✓ |
| 跨网络隔离（research 网络成员经限定别名访问） | 阻断（gaierror/unreachable）✓ |

**研究容器（youwei-research 网络）**：mock 网关可达 ✓；**Runner 别名无法解析、工具端点不可达** ✓（Runner 仅接入 youwei-experiment——研究容器维持"仅网关"的 D1 限定）。

## B. HTTP 越权矩阵（对运行中的真实 Runner，17 项）

| 令牌 × 端点 | 结果 |
| --- | --- |
| 工具令牌（runner-tools + experiment:submit）× `/v1/executions` | 403 ✓ |
| 工具令牌 × `/v1/research-invocations` | 403 ✓ |
| 工具令牌 × `/v1/experiment-authorizations`（控制面） | 403 ✓ |
| 工具令牌 × `/v1/experiment-invocations`（派发） | 403 ✓ |
| 工具令牌（experiment:status）× 工具面状态端点（对照组） | **404**（通过授权门，仅计算不存在）✓ |
| admin 令牌（experiment:admin）× 派发 | 403 ✓ |
| admin 令牌 × 工具面 | 403 ✓ |
| 派发令牌（experiment:run）× 登记/终止/回执 | 403（×3）✓ |
| 派发令牌 × `/v1/executions` | 403 ✓ |
| research:run 令牌 × 登记/派发/工具面 | 403（×3）✓ |
| run_status 单 scope × 派发 | 403 ✓ |
| submit 单 scope × 计算轮询 | 403 ✓ |
| 跨实验绑定令牌 × 计算轮询 | 403 ✓ |
| 伪造签名（他钥同 kid）× 工具面 | 403 ✓ |

## C. 端到端探索闭环（experiment_e2e.py phase loop，7 项全过）

| 步骤 | 结果 |
| --- | --- |
| 研究回合 1（真研究容器 + mock 模型）→ ExperimentRequest | ✓（vol-ratio 分位数问题） |
| 控制面授权登记（绑定+限额+快照注入源） | ✓ `{"status":"registered"}` |
| 实验实例派发（真实验容器；工具经 Tool Search 桥 `tool_call` 真实调用工具面：submit → status 轮询至终态 → artifact_read） | ✓ usage=session_delta complete=True |
| 沙箱计算（真容器 `--network none`，Runner 注入快照） | ✓ `ratio.json`（内容 `{"ratio": 0.025}`，实测 1.9s，回执 `succeeded`） |
| 回执核验（verify_experiment_evidence：代码 hash/终态/产物 manifest/快照 hash/单一镜像） | ✓ 1 computation，image=python:3.13-alpine |
| 研究重入（携 ExperimentContext）→ 提案 kind="code" 引用 | ✓ locator `experiment:<id>/computations/<cid>/artifacts/ratio.json` |
| 引用解析到回执产物 | ✓ |

## 验收中发现并修复的缺陷

1. **实验简报的沙箱契约与真实执行不符**（代码缺陷）：简报曾描述"SBX_INPUT_PATH 环境变量 + 当前目录产物"；真实契约是快照只读挂载于 `/inputs/snapshot/content.json`、产物必须写 `/outputs/`（根文件系统只读）。已修正 `experiment.py` 简报与工具 schema 描述，并重建镜像。
2. **重入回合的实验上下文未贯通**（代码缺陷，S08c 遗留）：`ResearchInvocationRequest.experiments` 在 Runner 的 research wire（`{capability_token, evidence, config}`）和 agent-runtime `honor_request → run_research` 两处均未传递——重入回合拿不到已接纳实验结果（简报缺失、kind="code" 引用无法解析）。已补 Runner wire `experiments` 字段与 invoke 透传；单元测试同步更新。
3. **mock 网关节奏与 Hermes 工具护栏冲突**（测试资产缺陷）：毫秒级轮询在沙箱完成前触发 `identical_call_streak_halt`（5 次相同调用叫停）。mock 状态轮询响应加 1s 延迟模拟真实模型节奏。
4. **Runner spool 的同路径 bind 规则**（测试资产缺陷）：不同路径的 bind（宿主 `/tmp/s08-accept-spool` ≠ 容器 `/tmp/youwei-runner`）使 `docker run -v` 的容器路径在宿主被自动建为空目录（`job_script.py` 缺失）。改为同路径 bind（dev-compose 模式）。

另记录：internal 网络上 docker-proxy 的端口发布不可靠（宿主 curl 发布端口 connection reset，直连容器 IP 正常）——驱动改为解析容器 IP 直连，不依赖 `-p`。

## 可复现资产

```
services/sandbox-runner/smoke/
  experiment_net_probe.py       # 容器内网络探针（实验/研究两侧通用）
  experiment_mock_gateway.py    # 脚本化 SSE 模型（研究首回合/重入/实验工具循环三角色）
  experiment_e2e.py             # 验收驱动：自建拓扑（网络+网关+Runner 容器）+ 三阶段
```

复现命令（SG，仓库 venv）：

```bash
docker build -f infra/images/runner.Dockerfile -t youwei-runner:development .
.venv/bin/python services/sandbox-runner/smoke/experiment_e2e.py \
    --repo "$(pwd)" --image youwei/agent-runtime:s08c
```

## 剩余限制

- 真实 LLM 网关下的探索闭环（真实模型驱动工具循环、真实费用与取消计费语义）随 Phase 1B 启用另行验收；本验收模型为脚本化 mock。
- Core 侧登记/接纳（fencing、逐 case 上限、append-only）不在本验收范围——由 SG 真实 PostgreSQL 的 DB 级测试覆盖（585 passed）。
- agent-runtime 镜像为本地 tag（`youwei/agent-runtime:s08c`），未推 registry（deployment 引用保持未就绪，与 S07m/S07l 同状态）；Runner 生产部署的 `experiment_store_dir`/`experiment_tool_base_url`/`youwei-experiment` 网络接入配置随 Phase 1B 部署决策落地。
- `youwei-experiment` 网络已在 SG 创建（internal、当前无成员），与生产命名一致；`youwei-research` 上仍挂有 litellm（S07n 接线，Phase 1B 声明式化时一并处理）。
