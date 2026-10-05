# WebUI 性能切片发布证据

当前聊天组合由 `chat.lock.json` / `chat.manifest.json` 固定；仅 WebUI 镜像变化。完整结论、实际命令和恢复方式见 [运维验收](../../../docs/ops/webui-performance-20261005.md)。热加载 ≤5 秒未达到，不代表两分钟现象已解决。

- `preflight.json`：构建、隔离测试、候选备份恢复及限制；由锁文件绑定 hash。
- `postflight.json`：实际切换、API、其他容器身份与生产浏览器复核。
- `browser-before.json.gz` / `browser-edge.json.gz` / `browser-frontend.json.gz`：同会话每阶段冷/热各 5 次，完整资源/API 时间；汇总为 `performance-summary.json`。仅记录路径，不记录凭证、查询字符串、请求/响应正文或聊天内容。
- `browser-before-build-concurrent.json.gz`：受构建负载影响的早期试测，排除于正式对比，保留失败背景。
- `browser-mock.json`：独立合成账号/工作台 HTTP mock 浏览器结果；不与原生跨服务验收混同。
- `isolated-edge.json`、`nginx-test.txt`、`edge-rollout.json` 与 `public-*.json`：Nginx 1.18 隔离测试、实际 reload/长连接、成功/错误缓存与压缩变体。
- `candidate-restore.log`、`refreshed-restore.log`、`post-restore.log`、`cross-service.log`：真实备份副本及合成数据验证，未调用付费模型。
- `cutover.py` / `postflight-webui.py`：本次执行的固定版本切换/检查脚本；不是可直接用于任意后续版本的通用部署工具。

含秘密的渲染 Compose、配置和完整备份留在 SG `/root/webui-perf-20261005/` 的受控目录。锁引用未修改组件的既有证据，不把历史验证冒充本次重跑。生产没有触发回滚；旧镜像/匹配配置已保留，未恢复或覆盖正式研究数据。

原始测量以确定性 gzip 压缩，避免大体积 JSON 淹没代码审查；可用 `gzip -dc browser-frontend.json.gz` 查看。解压数据未改动，方法与压缩文件 hash 见 `measurement-method.json`，汇总 JSON 保持可直接阅读。
