# WebUI 与个人助手接线

当前链路：youwei-webui → trading-assistant 原生 Hermes gateway → Core HTTP / LiteLLM / 受限工具。

`configure_assistant.py` 管理持久连接、单所有者限制与后台普通模型分流，并停用旧 Pipe；保留账户和聊天。
平台维护跨服务验收，助手原生插件和测试由独立仓库维护。

操作见 [助手手册](../../docs/ops/hermes-personal-assistant.md)。验收必须显式传入两个候选镜像：

```bash
uv run --frozen python ops/verify_assistant_webui.py --assistant-image <image> --webui-image <image>
```

已删除旧 Pipe 实现和专属测试；[历史说明](../../docs/archive/openwebui-pipe.md)只供追溯。
