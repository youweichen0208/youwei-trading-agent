# 仓库结构与 Runner 分离验证

日期：2026-09-28。范围：本地开发环境与 Docker Desktop，未部署 SG/CN 主机，未启动正式 Campaign。

## 实现边界

- 保留单业务仓库、Core Python 3.13 和现有迁移链；无 schema 变更。
- `quant/` 提取现有工程模型；纯函数已知答案覆盖常量基线、输入不足及动量窗口等行为，原模型语义与版本保持不变。
- `contracts/` 是独立轻量包；Runner 使用自身锁文件，Core 通过 HTTP 请求执行并保存结果。
- Core 镜像不含 Runner 或 Docker CLI；Runner 镜像不含 Core、SQLAlchemy、asyncpg、quant。开发依赖组安装 Runner 只为本仓库集成测试。
- 官方上游固定与适配策略见 [UPSTREAMS.md](../UPSTREAMS.md)，实际模块路径见 [REPOSITORY.md](../REPOSITORY.md)。

## 实际执行的验证

| 验证 | 命令 / 方法 | 结果 |
| --- | --- | --- |
| 全量回归 | `uv run --frozen pytest -q --tb=short --maxfail=3` | **216 passed in 64.08s**；真实一次性 PG、实际迁移、HTTP 子进程及 Docker 沙箱 |
| Runner 独立依赖 | `uv sync --project services/sandbox-runner --frozen --no-dev` | 成功，独立环境安装16个依赖包 |
| 独立安装边界 | Runner 自身解释器加 `-I` 导入 runner/contracts，并检查 Core/数据库依赖不存在 | 通过；`-I` 排除仓库当前目录误入模块搜索路径 |
| 上游登记 | `python3 infra/validate_upstreams.py --mode catalog` | 通过；未启用组件仍为 disabled，不能据此发布 |
| 部署校验反例 | `tests/contracts/test_upstreams.py` | 全量测试中通过；拒绝浮动镜像、未登记绑定、报告或配置 hash 不匹配等情况 |
| 镜像构建 | 分别构建 `infra/images/core.Dockerfile`、`runner.Dockerfile`，使用下表基础镜像 | 两个镜像均成功，本地 tag 为 `youwei-core:verification`、`youwei-runner:verification` |
| 实际镜像依赖隔离 | `docker run --rm --network none` 中检查导入与 CLI | Core 不含 Runner/Docker CLI，Runner 不含 Core/SQLAlchemy/asyncpg/quant；均通过 |

本地 Compose 的迁移、API/Worker/Runner 完整任务链路与清理结果见 [Runner Compose 联调](runner-compose-smoke.md)。

镜像构建使用的参数：

| 参数 | 实际基础镜像 |
| --- | --- |
| `PYTHON_IMAGE` | `python:3.13-slim@sha256:7c61056e61ac89e852de05f3dc6fa51a6dd2181797bceed46aa725dd7cb2cd3b` |
| `UV_IMAGE` | `ghcr.io/astral-sh/uv:0.11.16@sha256:440fd6477af86a2f1b38080c539f1672cd22acb1b1a47e321dba5158ab08864d` |
| `DOCKER_CLI_IMAGE`（仅 Runner） | `docker:29-cli@sha256:018edbc908e08fcc9dbf029c812c34251e9b4719e6f71ca0e5eae2a987d014ca` |

上述本地 verification tag 未作为生产登记或 release 批准。Core 和 Runner 各自以 `uv sync --frozen --no-dev` 构建；生产发布仍需登记实际发布镜像 digest、目标机验收报告与部署清单。

## 回归中修复的问题

1. Worker 心跳失败或进程退出不能伪装成已确认的用户取消；保留运行中 attempt，允许租约过期后恢复。
2. 取消测试使用 `shield` 等待，避免测试自己的超时取消掩盖实际取消链路失效。
3. Runner 限制回执缓存字节数；任务清理结束前不释放并发槽。
4. 集成 fixture 通过真实 Alembic CLI 子进程迁移，避免同步迁移入口重置 pytest 的事件循环；测试配置由 fixture 传入，避免多个 `conftest` 同名导入冲突。

## 验证范围

本轮证明本地包与进程依赖分离、授权快照传递、结果校验、fencing、正常取消与租约故障路径可工作。测试使用合成业务输入，不是前向预测成绩。

SG Linux 上当前 Runner 接线的 runsc、磁盘/内存/OOM 压测、硬杀恢复时限、生产凭证与备份故障域仍需 S09 验收。Runner 使用短期进程内回执缓存，重启后由 Core 租约机制恢复；每个 Docker daemon 当前只运行一个 Runner，启动时回收其标签下的遗留容器。外部 Agent/UI/检索适配器未因本次调整而自动接入。
