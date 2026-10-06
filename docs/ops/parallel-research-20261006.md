# 个人助手并行检索与单次行情分析（2026-10-06）

用户希望减少 AAPL 走势分析中重复指标请求及串行网页查询，并允许使用子代理。此次只改变个人助手查询编排和卡片展示，不修改正式 ResearchRelease、Controller、数据库或评估协议。

## 实现与边界

- 金融包 [PR #3](https://github.com/youweichen0208/trading_core/pull/3)：`get_analysis` / CLI `analysis` 一次取得历史，查询区间保留价格、收益、回撤与波动率；SMA/RSI 使用至少400个日历日的历史，单独标注 `indicator_history`。不足或缺失仍返回原因，不补价。
- 助手 [PR #4](https://github.com/youweichen0208/trading-assistant/pull/4)：`trading_analysis` 为走势首选；`web_search_batch` 最多三个去重查询并行，HTML提取最多三个并发、五页，保留连接时 SSRF 防护。去掉 nav/footer/aside 中的导航噪声；同批重复 URL 只抓一次，保持原生结果顺序和长度。
- 多步骤独立事件可一次启动最多两个原生 leaf 子代理。固定 Hermes 忽略 task.toolsets，因此使用官方 LLM 请求与执行中间件双重限制：子代理只看到且只能执行 web_search/web_extract，最多两次搜索、五页，不能提交平台任务、修改知识或递归委派。最多六次迭代、60秒无活动超时，90秒或主请求停止时向本会话子代理发起中断。网络读取受现有超时控制，不宣称任意阻塞操作瞬间退出。
- WebUI [PR #6](https://github.com/youweichen0208/youwei-webui/pull/6)：组合卡片同时显示行情和指标，区分请求窗口与预热历史，拒绝额外或格式不合法的指标。原有卡片、所有者鉴权与历史恢复方式保留。
- 当前 WebUI `store=false` 没有后续结果消费机制。采用同轮并行汇总，不能承诺结束本轮后再后台补答。简单查询优先批量工具，子代理会增加模型调用开销，不保证任意问题更快。

## 验证

- 金融包26项、助手78项、WebUI金融组件11项、工作台Python26项、平台相关契约33项通过。关键回归先观察失败，再实现通过。初版前端 Vite 构建通过，最终源码由完整镜像构建验证。
- 固定 Hermes 原生 mock 验证两个子代理的 barrier 并发、网页工具发现和越权平台提交拒绝；原有会话、鉴权、流、知识、备份恢复、MCP 验收保持通过。模型及 MCP 均为隔离 mock，无真实付费模型请求。
- 合成固定100ms的三个查询、五轮：串行中位0.314秒，并行0.106秒，详见 benchmark.json。不是实际网络或完整模型回复耗时。
- SG真实免费来源：截至10月2日窗口64条，组合接口一次返回有效 SMA200=288.79897071838377。截至10月5日窗口65条，但本轮Yahoo的10月5日 close/adjusted_close为空，指标保留缺失原因；直接旧价格接口也出现同一缺失，不归因于新接口。没有拿前收盘填补。
- 全仓既有类型检查问题按历史记录保留；未重新宣称全仓类型检查通过。真实付费模型的工具选择、归因质量与端到端回复延时未验证，不计正式前向评估。

## 发布状态

2026-10-06 已部署到 sg-prod，仅更新 WebUI、助手和技能三个服务；另外13个容器 ID/StartedAt/镜像不变。固定身份见 [image.json](../../infra/releases/20261006-parallel-research/image.json)，实际渲染配置通过部署锁/manifest校验。

- 助手源码 `5b7631b291139c4d8ddb9ce218c99453e35dce9f`，镜像 `ghcr.io/youweichen0208/trading-assistant-candidates@sha256:4c2c8798568775a3795465dd0d409de63e2465055a2e781f05c59c9cc0726c88`；金融源码 `d7a464e`，wheel hash 固定在助手锁中。[构建与镜像验收](https://github.com/youweichen0208/trading-assistant/actions/runs/37391484575)。
- WebUI源码 `e689ef4acec1b0e6686650c1a950c163d2a214fa`，镜像 `ghcr.io/youweichen0208/youwei-webui@sha256:2c5ffcdf5648ebae62c968115fdf1447b8134efa917ffd7951f63b7f0a6166fd`。[最终构建](https://github.com/youweichen0208/youwei-webui/actions/runs/37392453050)。较早两版候选未部署，最终版本补充了旧行情夹带异常指标及未知指标键的拒绝测试。
- 上线前标准备份与隔离恢复 ALL PASS；新助手对既有备份恢复通过。新 WebUI 对真实备份在无网络环境启动/重启，8个聊天逐字节、账户/密码保留；跨服务聊天、完整历史、SSE、知识、重启、备份恢复及模型发现失败分流通过。
- 独立 Responses store=false 原生验收证明并行子代理在本轮返回，发现及执行权限生效；该隔离验收不含真实付费模型。第一次测试源挂载权限不足已修正，仅开放非敏感测试源码的只读权限。
- 上线后真实免费行情复核通过，最新日缺失仍原样保留；再次标准备份与隔离恢复 ALL PASS。
- 上线健康/鉴权/聊天/工作台接口通过；匿名工作台401、原始Responses旁路403。公网浏览器读到新源码版本、h2、旧AAPL三张金融卡片和可编辑输入框；工作台四类接口200。没有生成新付费模型对话，旧回答不会自动重算。

部署私密备份及回滚副本保存在 `/root/parallel-research-20261006/`。如需回滚，恢复该目录 `rollback/compose.json` 至 `/opt/youwei/chat/compose.json`，保留现有数据卷和 secrets.env，再执行：

```bash
docker compose -p youwei-chat --project-directory /opt/youwei/chat \
  -f /opt/youwei/chat/compose.json --env-file /opt/youwei/chat/secrets.env \
  up -d --no-deps hermes-assistant hermes-workbench-skills openwebui
```

回滚目标为日期修复助手 digest `73e28618…ad7` 和既有 WebUI digest `203a484b…e345`。本次未触发生产回滚；没有数据库迁移，不恢复或覆盖正式研究数据。源码以PR交付，部署不代表这些PR已合并。
