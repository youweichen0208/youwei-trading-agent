# S01 目标环境实测记录（sg-prod）

日期：2026-09-27。环境：`sg-prod`（DigitalOcean droplet，Ubuntu 24.04.3，kernel 6.8，4C / 7.8G，Docker 29.5.2，cgroup v2/systemd）。
既有部署：`/opt/youwei-trading-agent` 原型栈（quant / data-service / redis / postgres:17，全部 `unless-stopped`）。测试全程未修改该栈；期间一次 dockerd 重启后全部容器自动恢复 healthy。

测试工件留在服务器 `~/s01-verify/`（bench 脚本、Dockerfile、litellm 配置；`volc_key.txt`/`master_key.txt` 权限 600）。镜像 `s01-quant-bench`（595MB）与 `ghcr.io/berriai/litellm:main-stable`（1.65GB）保留复用。

## 1. gVisor 沙箱与量化栈 ✅

- runsc `release-20260921.0`（官方 apt 仓库安装；kernel 6.8 满足 Linux 5.6+ 要求）
- `/etc/docker/daemon.json` 新增：注册 `runsc` runtime + **开启 `live-restore`**（此后 dockerd 重启不再中断容器）
- 基准：5M 行 DataFrame、59MB Parquet、mmap 读、groupby 聚合、rolling(1000)；镜像 `python:3.12-slim` + numpy 2.5.3 / pandas 3.0.6 / pyarrow 25.0.1

| 步骤 | runc | runsc | runsc+锁定 |
| --- | --- | --- | --- |
| build 5M dataframe | 4.44s | 4.54s | 4.01s |
| write parquet | 2.03s | 2.04s | 1.93s |
| read parquet (mmap) | 0.46s | 0.40s | 0.59s |
| to_pandas + groupby | 0.70s | 0.74s | 0.72s |
| read + rolling | 1.23s | 1.25s | 1.49s |

- **结论：runsc 开销在噪声范围内（±10%）**。补充双模式读取基准（评审后修正“pyarrow 默认 mmap”误判：`read_table()` 默认 `memory_map=False`，两种模式分别实测）：mmap=True/False 在 runsc 下均正常（0.28s / 0.32s vs runc 0.25s / 0.25s），详见 [gvisor-quant-stack](gvisor-quant-stack.md)。锁定配置（`--network=none --read-only --tmpfs /tmp --memory=1g --pids-limit=256 --cpus=2`）全部生效，网络逃逸按预期阻断（socket 连接 OSError）。S03 沙箱验收的架构前提成立。

## 2. Pi 运行时 ✅

- `node:22-slim` + `npm install -g @earendil-works/pi-coding-agent@0.87.1` 成功（Node ≥22.19 满足）
- `--no-builtin-tools` / `--no-tools` 存在
- RPC 冒烟：`{"type":"get_state"}` → 合法响应（sessionId、model state 等 JSON）；`--mode rpc` 实测工作
- 白名单依据（命令全集）已固定于 [runtime-version-pinning](runtime-version-pinning.md)

## 3. Hermes 固定安装 ✅（含评审后 Python 3.14 重验）

- 首轮（python:3.12-slim）+ 评审修正后重验（**python:3.14-slim**）：
- main `7fa45eb349a1a6f1eebc010b3fef0a9d996f386a`：`requires-python >=3.11,<3.15`，仓库 `.python-version=3.14`；`uv sync --frozen --python 3.14` 成功，venv **Python 3.14.7**，`hermes --version` → `v0.21.5+3129.g7fa45eb`，venv 141MB
- tag `v2026.9.24`（f97608f1）：上界仍为 `<3.14` 且 `.python-version=3.11`——**3.14 支持在 tag 之后合入 main**；钉法：当前固定 main 完整 SHA，官方下个含 3.14 的 tag 发布后改钉 tag
- 钉定配方：`python:3.14-slim` + `git checkout <完整 SHA>` + `uv sync --frozen --python 3.14`
- 测试 clone 留在 `~/s01-verify/hermes314/`

## 4. LLM Gateway（LiteLLM → 火山）部分 ✅

- 镜像 `ghcr.io/berriai/litellm:main-stable`。注意：droplet `~/.docker/config.json` 存有**过期的 ghcr.io 凭证**，导致 `docker pull` 被 denied；用 `DOCKER_CONFIG=/tmp/空目录` 匿名拉取绕过。建议 `docker logout ghcr.io` 清理。
- 关键配置：anthropic provider + `api_base` 指火山端点 + **`extra_headers: Authorization: Bearer`（火山网关不认纯 x-api-key，必须带 Bearer）**
- 通过：OpenAI 兼容 chat（`'OK'`, finish stop）；**工具调用**（`get_quote {"ticker":"AAPL"}`）；**流式 SSE**（含 reasoning_content / thinking_blocks 透传）
- 未通过：`/anthropic` passthrough 路由认证（LiteLLM 侧拒绝 "API key is invalid"）→ 接线候选与替代路径见 [llm-gateway-options](llm-gateway-options.md) 的方案比较（统一 OpenAI 兼容路径已验证可用；LiteLLM 另有统一 `/v1/messages` 待测）
- 预算机制（评审后修正）：LiteLLM 1.102.1 **请求级预算预留默认启用**（请求前预估最大成本并预留、超额在上游前拒绝、响应后结算）；另有 Model Access Group 共享预算池、`max_parallel_requests`（在途并发，与 tpm/rpm 速率独立）、ITPM/OTPM、per-request spend 入库——职责分层与待补项见 [llm-gateway-options](llm-gateway-options.md)
- 未测（S02 网关实现时补）：取消传播、usage 入库（需 `DATABASE_URL`）、并发/速率限额验收、fail-closed 行为（计数器故障时拒绝请求）、Pi/Hermes 实际指向网关的端到端、自定义 cost map 对账
- 测试容器已停止删除；重启方式：`cd ~/s01-verify/litellm && docker run -d --name s01-litellm -p 127.0.0.1:4000:4000 -v $PWD/config.yaml:/app/config.yaml:ro -e LITELLM_MASTER_KEY=$(cat master_key.txt) -e VOLC_API_KEY=$(cat volc_key.txt) ghcr.io/berriai/litellm:main-stable --config /app/config.yaml --port 4000`

## 5. 遗留与下一步

| 事项 | 状态 | 归属 |
| --- | --- | --- |
| Tiingo 试用（字段级验证、EOD 延迟、许可） | 待注册账号/token | 用户 → S01 收尾 |
| Hermes memory 隔离键运行时验证 | 键名已从官方文档确认 | S07 接入时 |
| LiteLLM 接线方案比较 + 取消/用量/限额 + fail-closed | 配置已留存 | S02 |
| 备份演练（OSS 已排除出 MVP） | 改为本地 WAL 归档 + 恢复演练 | S02 |
| 容量假设收缩（4C/7.8G 实测 vs 架构 §11 的 8C/32G） | 待项目所有者确认 | S01 记录 |
| ghcr 过期凭证清理 | 建议 `docker logout ghcr.io` | 用户 |

## 6. 评审修正记录（2026-09-27）

项目所有者评审后修正的结论（均经重新验证）：

1. **Python 3.14**（P1）：Hermes 目标运行时是 3.14（pyproject "only support 3.14"；main `.python-version=3.14`），>=3.11 仅为升级通道——已用 py3.14.7 重验安装
2. **网关预算**（P1）：LiteLLM 请求级预算预留默认启用；文档改为 Controller/Gateway 分层与补齐清单——官方 users 文档证实
3. **Tiingo 公司行为端点存在**（P2）：distributions/splits 独立端点官方存在，首版“父路径 404 推断无端点”属方法论错误——已探测证实
4. **Tiingo 行业字段为 SIC 派生**（P2）：fundamentals/meta 的 sector/industry 非 GICS，直接判定不满足——无需购买验证
5. **“必须双协议”不成立**（P2）：两 agent 均双协议，改为候选接线方案比较
6. tpm/rpm 是速率而非并发；`max_parallel_requests` 为独立在途并发参数（另四处小修之一）
7. EODHD 改为“候选之一”，Sharadar $9 需同标准比较
8. pyarrow `read_table()` 默认 `memory_map=False`，基准已补双模式实测
