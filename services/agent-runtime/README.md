# youwei-agent-runtime (S07)

独立 Hermes 研究运行时。Python 3.14，不依赖 Core / Ledger / 数据库 / 供应商密钥。

- 输入：Controller 冻结的计划与证据（FrozenEvidence）。
- 输出：ResearchProposal，`run_research` 返回前校验引用定位；Controller 的接收与封存接线仍待实现，Agent 无权写 Ledger 或改动 Lesson。
- 隔离：关闭内置 memory / user_profile / session_search / 后台复盘，研究工具白名单。
- Hermes 上游以完整 commit SHA 固定（见 `infra/upstreams.lock.yaml`、`docs/UPSTREAMS.md`）。

## 安装（本机）

```bash
uv sync --project services/agent-runtime --frozen --python 3.14
```

Hermes 为源码安装，固定 commit 见锁文件与上游登记。

## 本地引用验收

在 `services/agent-runtime/` 目录运行（`uv --project` 不会自动改变 pytest 工作目录）：

```bash
uv run --frozen pytest -q
uv run --frozen pytest -q -c pyproject.toml --confcutdir ../../tests/contracts \
    ../../tests/contracts/test_research.py ../../tests/contracts/test_research_references.py
```

前者测试简报、wire 解析和 Runtime 返回路径（外部 Hermes SDK 用测试替身）；后者在 Python 3.14 下复跑共享研究契约。不会调用真实模型或访问数据库。

简报按冻结数组原始顺序提供 JSON 行及 `snapshot:<uuid>/rows/<index>` 定位符。`run_research` 使用共享 `validate_proposal_references` 拒绝无效引用；直接调用 `parse_proposal` 仅完成格式和值域检查。冻结数组内容 hash、原位置、快照 ID 和 run/case 一起约束解析，具体边界见 [契约说明](../../contracts/README.md)。这不替代 Controller 端的租户授权、证据选择和封存验收。

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
