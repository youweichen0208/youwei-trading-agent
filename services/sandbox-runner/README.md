# 独立 Sandbox Runner

独立 Python 包 `youwei-runner`，不依赖 Core、SQLAlchemy、asyncpg 或供应商 SDK。Core 仅通过 HTTP 提交签名请求、轮询、取消；Runner 无数据库和供应商凭证。Core 返回结果仍须经过数据库的 attempt fencing。

```bash
uv sync --project services/sandbox-runner --frozen --no-dev
uv run --project services/sandbox-runner --frozen youwei-runner
```

启动前设置 `YOUWEI_RUNNER_SECRET`（至少32字符）、`YOUWEI_RUNNER_IMAGE`（实际镜像 digest），目标主机安装 `runsc`。仅开发环境显式设置 `YOUWEI_RUNNER_DEVELOPMENT=true`、`YOUWEI_RUNNER_RUNTIME=` 允许本机默认 Docker runtime。Runner 不读取 Core `.env`，容器策略只来自自身配置。

## 执行与恢复

- `POST /v1/executions` 提交 `sandbox-v1` 请求，签名绑定请求内容；同一 job/attempt 重复提交返回已有记录，不同内容拒绝。
- `GET /v1/executions/{job_id}/{attempt_no}` 查询并用新的有效能力令牌续期；令牌过期且未续期时取消计算。
- `DELETE` 同一路径取消并等待容器清理；清理结束前仍占并发槽。
- 一台 Docker daemon 只运行一个本 Runner 实例（单 uvicorn worker）。启动时回收 `youwei.runner=standalone-v1` 标记的遗留容器；内存中的执行回执不会恢复，Core 通过失败/租约过期创建新 attempt，旧结果不能跨 attempt 写入。
- 签名密钥只用于内部控制网络。生产部署还需验收宿主时钟同步、网络准入、socket 权限、真实 gVisor 与故障恢复；当前开发 Compose 不是生产发布配置。

## 数据与容量

输入快照以有限大小的 HTTP JSON 发送并做 hash 校验。作业只能设置 `SBX_*` 环境变量，不能替换执行器的 PATH 或加载器配置。Runner 只创建受控 spool；容器部署必须把 spool 的同一绝对路径同时挂在宿主和 Runner 内，见 [联调说明](../../docs/UPSTREAMS.md)。

默认并发1；请求上限8MiB，单产物4MiB、作业产物总量8MiB、输出 tmpfs 16MiB，暂只支持 JSON/CSV/TXT/MD。回执按保守估算计入32MiB缓存预算，并限制记录数；超过额度明确拒绝/失败，终态回执在租约到期后可回收。`contracts` 的类型上限可以更宽，Runner 的实际资源政策更严格。需要更大 Parquet/图表时另行实现流式产物协议并验收，不能直接放大到占满宿主内存。

Docker 日志轮转和响应限长、容器 CPU/内存/PID/墙钟限制由 Runner 强制。强杀 Runner 后遗留容器在下一次启动回收；它们不获得新业务提交权限，但停机期间的清理时延仍需 S09 运维验收。
