> 历史材料，旧 Pipe 已退出主链路；以下部署命令不再适用。源码可从基线提交 d5b1fba 查阅。

> 当前主要入口改为 [原生 Hermes 美股助手](../ops/hermes-personal-assistant.md)。下文保留旧 Pipe 的实现与历史接入说明；切换脚本只停用入口，不删除历史聊天。

> 2026-10-04：线上已切换 youwei-webui v0.11.4 + 原生 Hermes gateway，旧 Pipe 停用但保留代码与历史。本页以下 v0.6.36 Pipe 说明是历史实现；当前入口与操作见 [助手手册](../ops/hermes-personal-assistant.md) 和 [部署记录](../ops/three-repo-vm-rollout-20261004.md)。

# Open WebUI 研究入口（S12c）

把 Open WebUI（固定 v0.6.36，digest 见 `infra/compose/chat.json`）作为探索性研究的发起入口：
聊天消息 → 薄适配器（Pipe Function）→ Core `POST /v1/research` → 受控研究 →
聊天内摘要 + Dashboard 详情（`/#/research/{id}`）。

适配器**只做**提交/查询/取消/摘要渲染；任务状态、证据冻结、研究执行、报告保存全部在
Core（S12a）。刷新或断线不丢任务——重发同一消息返回该研究的当前状态（幂等键绑定
Open WebUI 用户 + 消息文本）。

## 实测依据（2026-10-03，sg-prod 运行容器，全部只读）

- **Pipe 语义（v0.6.36 源码核实）**：模块级 `Pipe` 类经 exec 加载，活跃函数出现在模型列表；
  `Valves`（pydantic）+ `self.valves` 由管理面编辑并持久化；运行时按签名注入 `body`（OpenAI
  chat form data）与 `__user__` 等；`async def pipe(...)` 返回 `str` 即成为聊天消息，异常渲染
  为错误 detail。frontmatter `requirements:` 支持自动安装——本适配器只用容器内已有的
  httpx 0.28.1，零安装。
- **出口连通性**：openwebui 容器（仅挂 `youwei-chat_chat` 网络）到 core-api **不通**
  （edge 网 IP 超时；host loopback 拒绝——core 端口只发布在宿主 127.0.0.1）。
- **网络拓扑**：`youwei-production_edge` 网络成员仅 core-api 与 dashboard——**不含**
  postgres、worker、Runner。openwebui 加入该外部网络即获得到受控 Core API 的最小通路。

## 部署（所有者执行；启用前置未满足时勿上线）

前置（未满足时研究任务会**如实失败**，不会伪造）：
1. 数据源 LLM 转发授权（把冻结证据发给 LLM 供应商）——所有者决策；
2. Core worker 的 research wiring（Runner 研究链路声明式部署，S12d）。

步骤：
1. **建 Core API key**（绑定租户；例如 `POST /v1/admin/api-keys`）。key 只进两处之一：
   Open WebUI 管理面板该函数的 Valves，或 chat 栈 secrets.env 的 `YOUWEI_CORE_KEY`
   （Valves 优先）。
2. **应用 compose 增量**（本仓库 `infra/compose/chat.json` 已含声明式变更）：
   openwebui 服务增加 `core_edge` 外部网络（`youwei-production_edge`）与可选
   `YOUWEI_CORE_KEY` env；`docker compose up -d openwebui` 重建生效。外部网络不存在时
   compose 显式报错（fail-closed）。
3. **上传函数**：管理面板 → 函数 → 导入 `youwei_research_pipe.py` 全文 → 启用；
   在 Valves 里配置 `core_url`（默认 `http://youwei-production-core-api-1:8000`）、
   `core_api_key`、`dashboard_base_url`。
4. **验证**：
   - 复核注册保持关闭：`ENABLE_SIGNUP=False` 已部署且 signup 403（2026-10-02 验证过）；
   - 模型列表出现 "youwei 研究助手"；
   - 容器内探测：`docker exec youwei-chat-openwebui-1 python3 -c "import urllib.request; print(urllib.request.urlopen('http://youwei-production-core-api-1:8000/healthz', timeout=5).status)"` → 200；
   - 聊天发送 `研究 SPY D20` → 返回任务号/摘要；Dashboard `/#/research/{id}` 可见详情。

回滚：管理面板禁用/删除函数；或 compose 回退（openwebui 移出 `core_edge` 网络）。

## 安全说明

- Core key 仅存于服务端（Valves 持久化于 Open WebUI 库 / 容器 env），不出现在页面或消息。
- openwebui 获得的网络面是 edge（core-api + dashboard），未接触含数据库与 worker 的
  `youwei-production_core` 内部网络。
- 入口用户 → Core 的授权映射 v1 为单服务 key（单所有者形态）；多用户接入前需按架构
  §5 落实身份映射与 RLS。
- 公网入口为已认证聊天入口（WEBUI_AUTH=True、注册关闭、ECS 中转 + rate limit）。

## 测试

`tests/test_openwebui_pipe.py`（repo 根目录，httpx MockTransport 离线覆盖）：消息解析、
幂等键绑定、成功/轮询/超时中态/失败/取消/状态、Core 错误映射、缺 key 配置错误、
重发幂等不重复。
