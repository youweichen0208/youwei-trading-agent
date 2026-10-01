# agent-runtime 跨容器接线验收（S07m）

日期：2026-10-01
环境：sg-prod（DigitalOcean droplet，Docker 29.5.2，linux/amd64）
状态：**研究链路跨容器接线端到端贯通**；受限网络（仅网关出口）实测通过；部署引用保持未就绪（无镜像仓库）。

## 背景

S07m 把研究链路从「Controller 本地 subprocess 启动 agent-runtime」改为「Controller 经 Runner 受控接口启动研究容器」。研究授权从 HMAC（对称）改为 Ed25519（非对称），研究容器只持有公钥、只可验签不可签发；sandbox-v1 的 HMAC 链路保持不变。

## 交付物

- `contracts/src/youwei_contracts/research_capability.py`：Ed25519 研究令牌（`ywr_`），audience/scope/kid/身份绑定/续租语义，无 HMAC 回退。
- `contracts/src/youwei_contracts/agent_runtime.py`：`agent-runtime-v1` 契约（`invocation_id` 键 + envelope 分离稳定内容与 auth）。
- `services/sandbox-runner/src/youwei_runner/research.py`：研究执行入口（`docker run -i` 转发 stdin/stdout，注入公钥 + 网关凭据，仅网关出口）。
- `services/sandbox-runner/src/youwei_runner/app.py`：`/v1/research-invocations` 三端点（Ed25519 授权 + invocation 幂等 + 取消）。
- `youwei_core/ledger/research_client.py`：Controller 研究链路（签发双令牌 + HTTP 提交/轮询 + 每 Case 一 invocation）。
- `services/agent-runtime`：公钥验签 + stdin/stdout 字节上限。
- `infra/images/agent-runtime.Dockerfile`：cryptography 由 Hermes 锁提供（`==50.0.1`），Dockerfile 显式 VERIFY 版本而非二次安装。

## 固定信息

| 项 | 值 |
| --- | --- |
| 研究镜像 tag | `youwei/agent-runtime:dev` |
| 研究镜像 manifest digest | `sha256:998f060eb0f7fadaa1712e9540370a4dc1e79995b56f1a41a9c1f7b1e892f153` |
| cryptography（镜像内） | `50.0.1`（Hermes uv.lock 间接 pin，Dockerfile 验证） |
| pydantic（镜像内） | `2.13.4`（Hermes 锁） |
| 受限网络 | `youwei-research`（`docker network create --internal`，subnet 172.19.0.0/16） |

## 验证结果

### 1. 容器内 Ed25519 冒烟（5/5 通过）

用 mock 网关（SSE 流式）+ 真实 `research-once` 入口：

| 场景 | 结果 |
| --- | --- |
| 合法 Ed25519 grant | `produced` proposal（p=0.6、1 引用、usage=session_delta） |
| 错 tenant | 拒绝 `tenant does not match` |
| 缺 scope | 拒绝 `missing required scope 'research:run'` |
| 坏签名 | 拒绝 `signature mismatch` |
| 过期 | 拒绝 `expired` |

错误片段证明容器用 Ed25519 公钥验签（`research capability` + `research:run`），非旧 HMAC。

### 2. 端到端贯通（Controller → Runner → 研究容器 → 网关）

`services/sandbox-runner/smoke/research_e2e.py` 驱动真实 `execute_research_request` → `run_research_container`（`docker run -i` + `--network youwei-research` + Ed25519 验签 + 网关注入）：

```
ok=True exit_code=0 image_digest=youwei/agent-runtime:dev
proposal: source_status=produced p_outperform=0.6 refs=1
usage: session_delta complete=True (prompt 10 / completion 20 / total 30 / api_calls 1)
END_TO_END OK
```

### 3. 受限网络（仅网关出口）

internal network `youwei-research` 实测：

| 目标 | 结果 |
| --- | --- |
| mock 网关（同 network，172.19.0.2:9901） | 可达（HTTP 501 因 mock 只支持 POST，证明连通） |
| 外网（8.8.8.8） | `Network is unreachable` |
| 宿主机公网 IP（168.144.39.34:80） | `Network is unreachable` |
| docker socket 网关（172.17.0.1:2375） | `Network is unreachable` |

原 sandbox-v1 执行路径仍用 `--network none`（`execution.py` 未改动），无网络回归。

## 发现并修复的缺陷

1. **build context 打包**：`build-agent-runtime.sh` 的 `rsync --relative` 锚点在 macOS rsync 下失效，嵌套的 `services/agent-runtime` 丢失 `services/` 前缀。改为 `cd` 后显式分步 rsync。
2. **cryptography 版本**：Hermes uv.lock 已 pin `cryptography==50.0.1`（经 alibabacloud 间接），与消费者锁的 44.0.3 冲突。改为统一 50.x（contracts `[signing]` 放宽到 `>=44,<51`），Dockerfile 由「强制装 44.0.3」改为「验证 Hermes 提供的 50.0.1」。
3. **公钥未注入容器**：`run_research_container` 最初未把 `YOUWEI_RESEARCH_PUBLIC_KEYS` 注入研究容器，端到端测试暴露 `requires YOUWEI_RESEARCH_PUBLIC_KEYS`。已补 `-e` 注入 JSON 公钥 map。
4. **空 api_key 导致 Hermes 认为未配置 provider**：`_gateway_credential()` 读 `YOUWEI_GATEWAY_API_KEY` 为空时，Hermes 报 `No LLM provider configured`。端到端测试需设非空 mock key。

## 剩余限制

- manifest digest 是本地 digest 非 registry 引用，`upstreams.lock.yaml` 的 `deployment.image` 保持 null（无镜像仓库）。
- 受限网络的 DNS/IPv6 通路未显式测试（internal network 无外网，DNS 解析依赖容器内配置；需 S09 生产部署时按网关实际地址固定）。
- 真实 LLM 网关（litellm）调用、取消计费语义、数据源 LLM 转发授权、Phase 1B 正式启用（新 Campaign + 人工 release 批准）仍待后续。
- DB 级测试（迁移 + 现有测试全量）仍需 SG 上完整跑（本片只跑纯逻辑 + 端到端冒烟）。

## 验证命令

```bash
# 1. 容器内 Ed25519 冒烟（临时 --network host 验证 Ed25519 正确性）
docker run --rm --network host --entrypoint python \
    -v /tmp/youwei-agent-runtime-build/services/agent-runtime/smoke:/smoke:ro -w /smoke \
    youwei/agent-runtime:dev /smoke/container_smoke.py --gateway-port 9911

# 2. 受限网络端到端贯通
#    (1) 创建 internal network
docker network create --internal youwei-research
#    (2) 启动 mock 网关容器
docker run -d --name mock-gateway --network youwei-research \
    -v /tmp/youwei-agent-runtime-build/services/agent-runtime/smoke:/smoke:ro \
    --entrypoint python youwei/agent-runtime:dev /smoke/mock_gateway.py 9901 0.0.0.0
#    (3) 跑端到端（SG 部署目录 3.13 venv + sandbox-runner）
cd ~/youwei-trading-agent && YOUWEI_GATEWAY_API_KEY=mock-key \
    .venv/bin/python services/sandbox-runner/smoke/research_e2e.py
```
