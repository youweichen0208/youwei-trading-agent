# youwei-trading-agent

美股研究与前向预测评估平台。Core 管任务、身份、PIT、Ledger 与评估；独立 Runner 执行受限计算，研究/实验 Hermes 返回提案。个人助手和聊天界面分别位于 trading-assistant 与 youwei-webui；独立 trading_core 提供免费日线、指标与 SEC 查询。

默认协作分支为 `develop`，变更通过 PR 合入；删除原 `main` 分支，其提交历史由 `develop` 保留。分支合并不等于部署。

四仓职责见 [仓库边界](docs/REPOSITORY.md)；平台仍承载正式研究和部署登记，金融能力拆包不替代本仓库。开发见 [开发指南](docs/DEVELOPMENT.md)，运维见 [运维入口](docs/OPERATIONS.md)，当前交付见 [状态](docs/STATUS.md)。Agent 修改前阅读 [AGENTS.md](AGENTS.md)。

从 [文档索引](docs/README.md)、[仓库边界](docs/REPOSITORY.md)和[实施计划](docs/IMPLEMENTATION_PLAN.md)开始。

```bash
uv sync --frozen --group dev --python 3.13
uv run --frozen pytest -q
python3 infra/validate_upstreams.py --mode catalog
```

完整测试需要本机 Docker，使用一次性 PostgreSQL 与受限容器。开发测试不连接生产库；正式评估遵循登记协议和批准的 ResearchRelease。
