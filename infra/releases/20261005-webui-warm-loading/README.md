# WebUI 热缓存加载发布证据

完整结论、命令、性能与恢复边界见 [运维记录](../../../docs/ops/webui-warm-loading-20261005.md)。本轮热缓存中位数 7.877 → 5.166 秒，减少 34.4%；≤5 秒目标仍未达到。

- `image.json`、`image-inspect.txt`：CI 固定源码 amd64 构建、registry digest、目标机标签核对。
- `preflight.json`：本地/CI、备份副本、跨服务 mock 与候选浏览器验证；`chat.lock.json` 绑定其 hash，`chat.manifest.json` 固定候选/线上渲染 Compose hash。
- `candidate-restore.log`、`cross-service.log`、`refreshed-restore.log`、`post-restore.log`：实际隔离/恢复结果。秘密与完整数据副本仅保存在 SG 受控目录。
- `browser-candidate.json`、`browser-direct-fixture.js`：真实页面请求重叠、刷新配置、最新设置、失效登录、旧聊天与工作台 mock 流式/停止；配合 `ops/chat_startup_probe.js`，不用于生产测速。
- `browser-before.json.gz`、`browser-after.json.gz`：各 5 次完整热缓存资源/API 时间；`browser-first-version.json.gz` 单独保留第一次新版本资源下载。
- `measurement-method.json`、`performance-summary.json`：方法、文件 hash、原始秒数、中位数/最大值、体积和等待估计。
- `latency.json`、`public-checks.json`：剩余 API 延迟与服务端处理时间对照、h2/静态缓存/404/未认证检查。非同时采样，不定位每一跳网络。
- `postflight.json`：生产仅换 WebUI、15 个其他容器不变、API/版本/浏览器/后备份恢复结果。
- `cutover.py`、`postflight-webui.py`：本次执行的固定版本脚本，不能直接套用于任意后续版本。

此次没有数据库迁移、付费模型调用或正式前向评估。旧镜像和匹配配置保留，恢复时保留当前数据卷；没有触发实际生产回滚。冷加载五次矩阵未重测，两分钟现象未复现。
