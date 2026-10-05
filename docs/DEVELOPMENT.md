# 开发与测试

开始前阅读 [Agent 指引](../AGENTS.md)、[领域术语](../CONTEXT.md) 和 [实施计划](IMPLEMENTATION_PLAN.md) 对应任务，检查工作区并保留无关改动。

## 平台独立环境

仓库根目录使用 Python 3.13 和已提交锁文件：

```bash
uv sync --frozen --group dev --python 3.13
uv run --frozen pytest -q tests/contracts tests/known_answers
uv run --frozen pytest -q
python3 infra/validate_upstreams.py --mode catalog
```

contracts 与 known_answers 可独立运行；完整测试需要可用 Docker，fixture 创建一次性 PostgreSQL、执行真实迁移并清理数据，Runner 测试还启动受限容器。因缺 Docker 而跳过不算验收通过，禁止把测试清理连接到开发共享库或生产库。

任务、权限、PIT、迁移和收益计算按行为先写失败测试，逐个实现。改共享契约或 fixture 后运行完整测试；纯文档修改检查引用、命令与内容一致性即可。

## 本地运行

应用配置见 [config.py](../youwei_core/config.py)。先确认 `YOUWEI_DATABASE_URL` 指向自己的开发数据库；Alembic 读取导出的变量，不自动加载 `.env`。

```bash
uv run --frozen alembic upgrade head
uv run --frozen youwei-api
# 在另一个终端启动
uv run --frozen youwei-worker
```

Runner 独立安装：

```bash
uv sync --project services/sandbox-runner --frozen --no-dev
```

Runner 配置使用 `YOUWEI_RUNNER_*`，不加载 Core `.env`；本地运行显式选择 development，生产要求固定沙箱镜像和 runsc。更多隔离要求见 [仓库边界](REPOSITORY.md)。

## 跨仓验证

各仓独立安装测试，不以兄弟目录源码作为运行依赖。助手与 WebUI 通过固定镜像组合验证，金融包通过固定 wheel 消费。命令见 [个人助手运维](ops/hermes-personal-assistant.md)；该组合测试使用 mock 模型。目标机真实供应商、生产切换、付费模型与正式前向评估分别记录，不能相互替代。
