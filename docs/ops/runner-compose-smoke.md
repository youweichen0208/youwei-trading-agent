# 独立 Runner 的本机 Compose 冒烟

日期：2026-09-28（Asia/Shanghai）；成功运行 UTC 2026-09-27 17:20:20–17:20:52。

## 范围与结果

**通过**：本机 Docker Desktop 上，真实 API → Core Worker → HTTP Runner → Docker 沙箱 → 产物入库与事件查询全链路。未访问服务器，未调用真实行情或模型供应商，未创建正式 Campaign。

基于 `infra/compose/development.json` 生成临时 JSON：去除 build，将Core/Runner分别替换为已构建的 `youwei-core:verification` / `youwei-runner:verification`，使用唯一Compose project、独立PG volume和随机 `127.0.0.1` API端口。全部密钥随机合成；Tiingo token为空。spool为仓库外临时绝对路径，宿主和Runner内路径完全相同。

本轮本地镜像ID（**不是注册表发布digest**）：

| 镜像 | 本地 image ID |
| --- | --- |
| `youwei-core:verification` | `sha256:12943a6082e0cc97f8df6f3dda47f4797e4c5b9ac35db8fc87e3906902428051` |
| `youwei-runner:verification` | `sha256:a28779538091481678e484ce61ed6b3ea5be1222624a38d299b5a151c23cc748` |

## 执行证据

1. `docker compose ... config --quiet` 通过。
2. 独立PostgreSQL和Runner健康；Core镜像执行 `alembic upgrade head` 成功。
3. API与Worker启动。经管理员API创建临时tenant/key，再以tenant凭证提交 `sandbox.execute`，预算为0，不包含供应商调用。
4. 脚本在沙箱内断言非root、无数据库/Runner/供应商环境变量、无Docker socket，再写 `/outputs/result.json`。
5. 经API轮询得到 `succeeded`；读取events依序得到 `run.submitted`、`job.claimed`、`sandbox.artifacts_stored`、`job.succeeded`、`run.succeeded`。补充检查独立测试库产物为 `result.json`，内容包含 `ok=true`、`uid=65534`、`message=compose-smoke`。
6. 从实际容器inspect检查权限与网络，结果如下。

| 进程 | Docker socket | 实际网络 |
| --- | --- | --- |
| Core API | 无 | core、edge |
| Core Worker | 无 | core、runner_control、egress |
| Runner | 有，唯一持有者 | 仅runner_control |

Runner没有 `YOUWEI_DATABASE_URL` 或 `YOUWEI_TIINGO_TOKEN` 环境变量。测试run ID：`b0723a06-944f-4137-ba75-0c1fa47e292f`；临时project：`youwei-smoke-c41ed2fbd0`，均只用于本次隔离验收。

## 发现并修复的问题

首轮API仅接internal `core` 网络，容器内 `/healthz` 为200，但Docker Desktop宿主随机发布端口连接被拒绝。为API增加普通 `edge` 网络后重跑，上述完整链路通过。API仍只发布 `127.0.0.1` 端口；`core`、`runner_control` 继续为internal，Runner不加入edge/egress；Worker继续负责供应商egress。

该修复只调整开发Compose与网络说明，没有修改Core/Runner业务代码。

## 清理与限制

两轮均在finally执行本project的 `docker compose down --volumes --remove-orphans`，确认没有遗留project容器、网络、PG volume及本轮沙箱；移除临时spool与Compose文件。成功轮清理返回0。没有删除验证镜像或其他项目资源。

本次是 `development=true`、默认容器运行时的本机验证；不证明目标Linux上的runsc接线、生产镜像digest、生产身份/审批、持续运行恢复或正式前向评估通过。发布仍须通过独立的upstream deployment清单检查及既有授权流程。
