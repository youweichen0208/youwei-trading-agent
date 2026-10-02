# youwei 评估 Dashboard（S10a）

浏览器 → HTTPS Basic Auth → 同源只读代理 → Core。tenant key 只存在于代理进程环境，绝不出现在页面、URL 或浏览器存储；仅白名单 GET 路径可转发到 Core（写路径全部拒绝）。静态页为原生 ES modules，无构建链、无外部 CDN。

## 文件

- `proxy.py` — 只读代理：Basic Auth（常数时间 scrypt 校验）+ GET 白名单转发 + 静态文件服务；`python proxy.py hash-password` 生成密码 hash。
- `static/` — 页面（`app.js` hash 路由；`views/` 按视图拆分）。

## 环境变量（运行）

| 变量 | 说明 |
| --- | --- |
| `DASH_CORE_URL` | Core API 基址（如 `http://127.0.0.1:8000`） |
| `DASH_TENANT_KEY` | Core tenant API key（仅代理进程持有） |
| `DASH_USERNAME` | Basic Auth 用户名 |
| `DASH_PASSWORD_SCRYPT` | `scrypt:<salt hex>:<digest hex>`（`hash-password` 生成） |
| `DASH_CAMPAIGN_ID` | 默认 campaign（`/api/config` 返回） |
| `DASH_BIND` / `DASH_PORT` | 监听地址（默认 `127.0.0.1:8080`） |

## 测试

`tests/test_dashboard_proxy.py`（repo 根目录）：认证、白名单与零泄漏、静态/配置鉴权、常数时间校验、真 Core 端到端链。
`tests/test_dashboard_views.py` + `apps/dashboard/static/tests/*.test.mjs`：视图渲染行为（`node --test`，Node ≥ 18 内置 runner，零依赖；包装测试找不到 Node 时失败而非跳过）。单独运行：`node --test 'apps/dashboard/static/tests/*.test.mjs'`。

## 部署注意

- 必须经 HTTPS 暴露（Basic Auth 凭证不能明文过公网）；生产入口方式（ECS 中转 / SG 直连 / 仅内网）为所有者决策项。
- 代理运行环境需含 FastAPI/httpx（Core 同款镜像即可）；静态目录随代码分发。
- 图表库如需引入：固定版本 + 文件 SHA256 + 自托管，不使用 CDN。
