# S12d 研究链路生产部署执行记录（D1 授权后）

日期：2026-10-03。执行者：Agent（受所有者指令）；授权：**所有者 D1 = 全部授权**（2026-10-03，聊天确认）——把冻结的 Tiingo EOD 行情证据发送给 LLM 供应商（火山引擎，经 LiteLLM 网关）的数据转发授权，无范围保留。**D2（探索性研究默认模型与登记形态）仍待所有者决策**。

对应运行手册：[s12d-deployment](s12d-deployment.md)。本记录覆盖 §2/§3/§4.1/§4.2 与 §5；§2.4（worker 研究接线 env）按手册保持空值（D2 未定），Runner 以「已部署、无流量」状态运行。

## 1. 执行前检查（只读）

- 网络：`youwei-research` 与 `youwei-experiment`（均 internal）已存在；`youwei-research` 上挂有 litellm 的 S07n 临时手工接线（本记录 §5 由声明式重建替代）。
- 镜像：`ghcr.io/youweichen0208/youwei-runner@sha256:c3c0a0c5…`（phase1a-s12d）与 `youwei-agent-runtime@sha256:bca0a5b9…`（phase1a-s07-r2）均在 sg-prod 本地（runner 于本机构建并推送，push+digest 拉取验证见 S12d 准备记录）。
- 部署副本：`/opt/youwei/production/compose.json` 为 r4 时代渲染版；SG 仓库副本 `/root/youwei-trading-agent` 为 rsync 副本（非 git），内容停在 10-02。
- 遗留容器：`cool_poincare`（`youwei-runner:development`，挂 docker socket、无发布端口、容器内 127.0.0.1:8091 不可达）——S08 测试拓扑泄漏，本次清理（见 §6）。

## 2. 密钥与配置（runbook §2）

`/opt/youwei/secrets/production.env`（0600）幂等追加 4 项，现有 5 项不动；全程不打印值：

| 键 | 生成方式 | 备注 |
| --- | --- | --- |
| `YOUWEI_RUNNER_SECRET` | `openssl rand -hex 24` | worker 与 runner 同值（worker 侧在 §2.4 启用前不消费） |
| `YOUWEI_RESEARCH_SIGNING_PRIVATE_KEY` | pinned runner 镜像内 `generate_research_keypair()`，PKCS8 PEM，`\n` 转义单行，**单引号包裹** | Controller 持私钥；kid `research-key-1`，thumbprint `b17839db1ff42414d7e564dcf3fa82e8e9c47443e99935e19bbfeb12d9bc3ffd` |
| `YOUWEI_RESEARCH_PUBLIC_KEYS_JSON` | 同上公钥 | `{"research-key-1": "<PEM \\n 转义>"}`，runner 经 pydantic JSON 解析自动还原换行 |
| `YOUWEI_RESEARCH_GATEWAY_KEY` | LiteLLM 管理面 `/key/generate` | alias `research`，key_id `fbbf225a0116871dae882426ea4750ef653d9a7418dc32ebbc5545b275f9fda5`，max_parallel_requests=2，5 模型（glm-5.3 / glm-5.3-flash / deepseek-v4-flash / deepseek-v4-pro / qwen3.8-flash），与 chat key 及 S07n 四枚验证 key 分离 |

**env 格式决策（含 `\n` 转义的值）**：单引号包裹使 shell sourcing（runbook §4.1 模式）与 `docker compose` 插值都保留反斜杠；已在 SG 实测三段链路——① `set -a; . production.env` 后值保留字面 `\n` 且 `json.loads` 还原换行；② 以 compose 同款环境变量集在 pinned runner 镜像内构造 `RunnerSettings()` 成功（public keys 解析为 `['research-key-1']`、PEM 真换行、`load_pem_public_key` 通过、生产约束 runsc+digest 满足、实验面配置有效）；③ 部署后 `docker compose config --quiet` 通过。配套补丁：`ops/s09b_deploy.py` 的 `read_secrets` 现剥离一层成对包裹引号并跳过注释行（否则程序化读取会把引号带进插值环境）——离线用例验证 plain/带引号 JSON/双引号/注释四路径。

**已知代码缺口（D2 前必须修复，随下一次 Core 镜像）**：`youwei_core` 的 `research_signing_private_key` 为纯 `str`，无 `\n` 还原——worker 启用研究接线时 `load_pem_private_key` 会失败。修复很小（Settings/接线处单行 replace），但需要随 **r5 Core 镜像**发布：r4（`54df4db5…`）构建于 S12a 之前，生产 Core 尚无 `POST /v1/research` 探索性研究 API——D2 后的完整启用本就需要新镜像。

## 3. 网络与目录准备（runbook §3）

- `/var/lib/youwei-runner-spool` 创建，0700（宿主=容器同路径 bind，S08 教训）。
- 两个 internal 网络已存在，无需创建；compose 以 external 声明引用（缺失即 fail-closed）。

## 4. 生产部署副本更新 + sandbox-runner 部署（runbook §4.1）

- **部署副本更新（遵守 r3 事故教训「禁整文件覆盖」）**：先备份 `compose.json.bak-s12d-20261003`；以 `s09b_deploy._fix_bind_paths` 同一逻辑从仓库 `infra/compose/production.json`（HEAD `63a7b6d`）渲染目标文件；与现行副本 diff 审查——**增量恰为 S12d delta**（core-worker env +6 占位键、sandbox-runner 服务、research/experiment 外部网络、runner_experiments 卷），无任何其他差异（无路径回归、无既有服务 digest 变化）；`docker compose config --quiet` 通过后安装。
- **部署**：`docker compose -p youwei-production -f compose.json up -d sandbox-runner`。结果：卷 `youwei-production_runner_experiments` 创建；postgres/core-api/core-worker **零影响**（Up 13h 不重启）；`youwei-production-sandbox-runner-1` **Up (healthy)**。
- **验证**：容器内 `/healthz` 200；网络归属 `youwei-production_core` + `youwei-experiment`；从 core 网络一次性容器解析 `sandbox-runner`（172.23.0.6）并 healthz 200（未来 worker→Runner 通路）；启动日志干净（uvicorn 8091）。
- **说明**：Runner 进程为 root——镜像无 USER 指令，Runner 是架构中唯一持有 docker socket 的组件（socket 访问本身即 root 等价），compose 层以 read_only/cap_drop ALL/no-new-privileges/资源限额加固；派生的沙箱/研究容器自身的非 root 与网络隔离由 Runner 强制。

## 5. 聊天栈声明式研究网络接线（runbook §4.2）

- 仓库同步：`rsync -rc`（排除 .git/.venv/.env/缓存）本地 HEAD → `/root/youwei-trading-agent`；`chat.json`/`production.json`/`deploy_chat.sh` sha256 逐一核对一致。
- `ops/deploy_chat.sh up`：postgres 无变化不重建；**litellm 重建**并按声明式加入 `youwei-research`（别名 `litellm`）——S07n 的临时 `docker network connect` 随旧容器销毁，重建后接线为声明式（容器重建不再失效）。
- **验证**：litellm 网络 = `youwei-chat_chat` + `youwei-research`；研究网络内一次性容器解析 `litellm`（172.27.0.2）且 liveliness 200（Runner 派生研究容器的网关通路）；宿主 `/health/liveliness` OK；研究虚拟 key `/models` 返回 5 模型；**openwebui 未动**（保持 10-02 起的旧配置——其 compose 变更 edge 网络/`YOUWEI_CORE_KEY` 随 D2 后的 S12c 入口上线再应用），健康 200；公网 `https://trading.youwei-agent.com/health` → `{"status":true}`。
- **与 runbook 的偏差（记录）**：§3 的清理行 `docker network disconnect youwei-research youwei-chat-litellm-1` **未执行**——该行针对「声明式接管前旧容器仍在跑」的场景；本次 litellm 已重建，手工接线随旧容器消失，若重建后执行该命令反而会拆掉声明式接线。以 `docker inspect` 网络清单作为等效验证。

## 6. 清理

- `cool_poincare`（development runner 遗留，挂 docker socket）已 `docker rm -f`；其余 14 个容器为在役服务。

## 7. 未执行项与 D2 前置

| 项 | 状态 | 阻断 |
| --- | --- | --- |
| §2.4 worker 研究接线（`YOUWEI_RUNNER_URL`/`YOUWEI_RESEARCH_MODEL`/`YOUWEI_EXPERIMENT_EXPLORATION_ENABLED`） | **未填**（production.env 无这些键，compose 占位默认空=关闭） | D2 模型名；Core r5 镜像（S12a API + 私钥 `\n` 修复）；S07o 的 release 重登记问题（pipeline.py hash 已变） |
| §6 Open WebUI 入口（pipe 函数 + Core 租户 key + openwebui 重建） | 未执行 | 依赖 worker 接线生效，否则提交即失败 |
| §7 真实证券端到端八项验收 | 未执行 | 同上 |
| S09c 告警 webhook URL | 未配置 | 所有者 |

回滚路径不变（runbook §8）：研究关闭=清空 env 重建 worker；Runner 可 `stop` 保留；本次未改任何既有服务配置（除部署副本按 diff 验证新增 runner）。

## 8. 验证命令索引

```bash
# runner 健康（sg-prod）
docker compose -p youwei-production -f /opt/youwei/production/compose.json ps sandbox-runner
docker exec youwei-production-sandbox-runner-1 python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8091/healthz', timeout=3).status)"
# 研究网络内网关可达
docker run --rm --network youwei-research python:3.13-alpine python3 -c "import urllib.request; print(urllib.request.urlopen('http://litellm:4000/health/liveliness', timeout=5).status)"
# 公网聊天入口
curl -sf https://trading.youwei-agent.com/health
```
