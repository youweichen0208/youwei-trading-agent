# youwei-agent-runtime (S07)

独立 Hermes 研究运行时。Python 3.14，不依赖 Core / Ledger / 数据库 / 供应商密钥。

- 输入：Controller 冻结的计划与证据（FrozenEvidence）。
- 输出：ResearchProposal，由 Controller 校验后封存；Agent 无权写 Ledger 或改动 Lesson。
- 隔离：关闭内置 memory / user_profile / session_search / 后台复盘，研究工具白名单。
- Hermes 上游以完整 commit SHA 固定（见 `infra/upstreams.lock.yaml`、`docs/UPSTREAMS.md`）。

## 安装（本机）

```bash
uv sync --project services/agent-runtime --frozen --python 3.14
```

Hermes 为源码安装，固定 commit 见锁文件与上游登记。

## 无成本冒烟（SG 上）

真实 `AIAgent` 调用需 LLM 网关 + 非零预算（Phase 1B 才启用）；先用本地 mock 网关验证链路零成本可跑通（Hermes `chat()` 走 SSE 流式，mock 必须返回 `text/event-stream`）：

```bash
# 终端 1：启动 mock 网关（OpenAI 兼容 SSE）
$PY services/agent-runtime/smoke/mock_gateway.py 9901

# 终端 2：跑全链路冒烟（冻结证据 → 简报 → chat → proposal）
PYTHONPATH=<hermes-checkout>:<contracts-src>:<agent-runtime-src> \
    $PY services/agent-runtime/smoke/smoke_e2e.py
```

其中 `$PY` 是固定 Hermes checkout 的 Python 3.14 venv 解释器（`~/s01-verify/hermes314/.venv/bin/python`）。预期输出以 `SMOKE OK` 结尾，且 `agent valid tools: []`、`agent memory store: None`（隔离键生效）。
