# S12d 研究链路生产启用运行手册（Runner + 研究网络声明式化 + 端到端样例）

日期：2026-10-03（工程准备完成）。状态：**仓库资产就绪、镜像已发布、未部署**——上线需先完成下述所有者决策。

本文是 S12d 的完整启用程序。执行者为所有者（或受权操作）；每一步都有验证点，任何一步失败即停止并按文末回滚。

## 0. 前置：所有者决策（未完成前不得进入第 2 步）

| # | 决策 | 说明 |
| --- | --- | --- |
| D1 | **数据源 LLM 转发授权** | 把冻结的 Tiingo EOD 行情证据发送给 LLM 供应商（火山引擎，经 LiteLLM 网关）。这是数据许可层面的决定，无法由工程替代。 |
| D2 | **探索性研究默认模型与登记形态** | 研究回合用的网关模型名（S07n 管线验证用车为 glm-5.3，不构成定稿）；以及探索性研究是否/如何绑 Trial 登记。每个任务的 config manifest 与报告 content 均记录实际配置。 |

不满足时：生产 worker 的 research 接线 env 保持空（研究任务如实失败，不伪造），Runner 可独立部署（不影响任何现有服务）。

## 1. 已就绪资产（本仓库，commit 记录见实施计划 S12d 完成记录）

- **Runner 镜像**：`ghcr.io/youweichen0208/youwei-runner@sha256:c3c0a0c525a818a863e1d70aa6db821631abdb26e1276e57c22f87a53fe794b9`（2026-10-03 于 sg-prod 从当前仓库构建——contracts + services/sandbox-runner checksum 同步核对——push 后 digest 拉取验证；构建目录 `/root/youwei-runner-build-s12d` 已清理）。
- **生产 compose**（`infra/compose/production.json`）：新增 `sandbox-runner` 服务——runsc 强制、sandbox 基镜像 `python:3.13-alpine@sha256:2dd78ad5…`、agent-runtime `@sha256:bca0a5b9…`、docker socket、**同路径 spool bind**（宿主=容器 `/var/lib/youwei-runner-spool`）、experiment store 卷、`core`+`experiment` 网络、 hardened（read_only/cap_drop/no-new-privileges）；外部网络 `youwei-research` / `youwei-experiment` 声明为 external。core-worker 增研究接线 env **占位（空默认=关闭）**。
- **聊天栈 compose**（`infra/compose/chat.json`）：litellm 声明式接入 `youwei-research`（别名 `litellm`）——**替代 S07n 的临时 `docker network connect`**（该临时接线在 litellm 容器重建后即失效；声明式后重建自动恢复）；openwebui 接入 `youwei-production_edge`（S12c）。
- **upstreams**：`sandbox-runner` 组件已登记（enabled=false、deployment.image=null——发布记录在 notes；启用随部署完成）。

## 2. 密钥与配置（sg-prod，/opt/youwei/secrets/production.env 追加，0600）

```bash
# 2.1 Runner 共享密钥（>=32 字符）
openssl rand -hex 24   # -> YOUWEI_RUNNER_SECRET（worker 与 runner 同值）

# 2.2 研究签名密钥对（Controller 持私钥；Runner 只收公钥）
python3 - <<'PY'
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
k = Ed25519PrivateKey.generate()
priv = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
pub = k.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
import json
print("YOUWEI_RESEARCH_SIGNING_PRIVATE_KEY=<单行 PEM，换行转 \\n>")
print("YOUWEI_RESEARCH_PUBLIC_KEYS_JSON=" + json.dumps({"research-key-1": pub.replace("\n", "\\n")}))
PY

# 2.3 网关研究虚拟 key（LiteLLM 管理面签发，独立限额；不与 chat 无预算 key 混用）
#     建议限额：max_parallel_requests=2（S07n 实测单回合 84-224s；批量并发另评估）
#     -> YOUWEI_RESEARCH_GATEWAY_KEY（runner 侧注入研究容器）

# 2.4 worker 接线（D1/D2 完成后才填非空）
YOUWEI_RUNNER_URL=http://sandbox-runner:8091
YOUWEI_RESEARCH_MODEL=<D2 定稿的模型名>
YOUWEI_EXPERIMENT_EXPLORATION_ENABLED=true   # 探索循环（S08）一并启用
```

## 3. 网络与目录准备（sg-prod）

```bash
# 受限研究网络与实验网络（S07m/S08 已建；external 声明要求其存在，缺失即 fail-closed）
docker network inspect youwei-research youwei-experiment >/dev/null || \
  { docker network create --internal youwei-research; docker network create --internal youwei-experiment; }
# 秒杀 S08 教训：spool 必须宿主=容器同路径
mkdir -p /var/lib/youwei-runner-spool && chmod 700 /var/lib/youwei-runner-spool
# 临时接线清理（声明式接管后）
docker network disconnect youwei-research youwei-chat-litellm-1 2>/dev/null || true
```

## 4. 部署顺序（每步验证通过再进下一步）

```bash
# 4.1 Runner（不影响现有服务；生产模式强制 runsc + digest）
cd /opt/youwei/production && set -a && . /opt/youwei/secrets/production.env && set +a
docker compose up -d sandbox-runner
docker compose ps sandbox-runner            # healthy
docker logs sandbox-runner 2>&1 | tail -5   # 无启动错误
docker exec sandbox-runner python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8091/healthz', timeout=3).status)"

# 4.2 聊天栈（litellm 声明式接入研究网络；openwebui 接入 edge）
cd /opt/youwei/chat && set -a && . ./secrets.env && set +a
docker compose up -d litellm openwebui
docker exec youwei-chat-litellm-1 python3 -c \
  "import socket; print(socket.gethostbyname('litellm'))"   # 研究网络内自解析
# 研究网络上的 litellm 别名对 Runner 派生容器可见（容器重建后仍成立——声明式）
docker run --rm --network youwei-research python:3.13-alpine \
  python3 -c "import urllib.request as u; u.urlopen('http://litellm:4000/health', timeout=5); print('gateway reachable')"

# 4.3 Core worker 研究接线（D1/D2 完成后；重启 worker 生效）
cd /opt/youwei/production
docker compose up -d core-worker
docker logs core-worker 2>&1 | grep -i error | tail -3   # 无错误
```

## 5. upstreams 启用与部署校验（部署完成、验证证据落档后）

1. `infra/upstreams.lock.yaml`：`sandbox-runner` 组件 `enabled: true`、`deployment.image` 填 runner digest、`bindings: [{"service": "sandbox-runner", "field": "image"}]`；verification evidence 指向本次部署验收记录（含 sha256）。
2. 重生部署清单并校验（s09b_deploy 渲染流程 + `python3 infra/validate_upstreams.py --mode deployment --compose <渲染后> --manifest <清单>` → VALID）。

## 6. Open WebUI 入口（S12c README 四步）

建 Core 租户 key（研究提交用，与 chat key 分离）→ 上传 `integrations/openwebui/youwei_research_pipe.py` → 配置 Valves（core_url/core_api_key）→ 复核 `ENABLE_SIGNUP=False` 与模型列表出现"youwei 研究助手"。

## 7. 真实证券端到端样例验收（S12d 交付判据）

在聊天入口发送 `研究 SPY D20`（或任一 panel 证券），逐项确认：

- [ ] 提交返回任务号；轮询期内聊天可见中态；完成返回摘要（结论/quant_relation/量化输入/依据/限制 + Dashboard 链接）
- [ ] Dashboard `/#/research/{id}` 六块齐全、引用对冻结快照可解析、证据快照 as_of=提交时刻（forward）
- [ ] 报告 config 记录实际模型与量化车辆版本
- [ ] 网关 spend logs 走**研究虚拟 key**（限额生效；非 chat key）
- [ ] 取消路径：提交后立即取消 → 任务 cancelled、无报告、无残留研究容器（`docker ps -a | grep agent-runtime` 为空）
- [ ] Runner 重启后回执/幂等语义完好（S08b 已验，抽查）
- [ ] **正式 Ledger 零写入**：`SELECT count(*) FROM predictions` 等不变（S12a 已有断言，生产抽查）
- [ ] 记录单回合延迟与 token 用量（Phase 1B 批量并发设计的输入）

## 8. 回滚

- 研究关闭：`production.env` 清空 `YOUWEI_RESEARCH_SIGNING_PRIVATE_KEY` / `YOUWEI_RESEARCH_MODEL` → `docker compose up -d core-worker`（Runner 可保留，无流量）。
- 入口下线：Open WebUI 管理面板禁用研究函数。
- Runner 下线：`docker compose stop sandbox-runner`（不影响 core/postgres）。
- 全量回退：`git revert` 本批 compose 变更 + 按原 manifest 重新部署。

## 已知限制

- 研究网络出口仅网关（S07n 探针验证过外网/外部 DNS 阻断）；fail-open 行为（网关计数库不可达）沿用 S07n 结论，如需收紧另行评估。
- 单回合延迟 84-224s（真实模型实测）；批量并发设计随 Phase 1B 正式启用另做。
- 探索性研究的 Trial 登记形态未定（D2）；比较用途的配置差异须事前登记。
