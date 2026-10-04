# youwei-trading-agent

美股研究与前向预测评估平台。Core 管任务、身份、PIT、Ledger 与评估；独立 Runner 执行受限计算，研究/实验 Hermes 返回提案。个人助手和聊天界面分别位于 trading-assistant 与 youwei-webui。

从 [文档索引](docs/README.md)、[仓库边界](docs/REPOSITORY.md)和[实施计划](docs/IMPLEMENTATION_PLAN.md)开始。

```bash
uv sync --frozen --group dev --python 3.13
uv run --frozen pytest -q
python3 infra/validate_upstreams.py --mode catalog
```

完整测试需要本机 Docker，使用一次性 PostgreSQL 与受限容器。开发测试不连接生产库；正式评估遵循登记协议和批准的 ResearchRelease。
