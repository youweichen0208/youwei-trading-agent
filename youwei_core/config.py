"""Runtime settings. Resource baselines follow the S02 decision:
4 vCPU / 7.8 GB host, API and Worker one process each, conservative pools."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "YOUWEI_"}

    database_url: str = "postgresql+asyncpg://youwei:youwei@localhost:5432/youwei"

    # API process pool (baseline: pool_size=2, max_overflow=0)
    api_pool_size: int = 2
    api_max_overflow: int = 0

    # Worker process pool (baseline: pool_size=2, max_overflow=0)
    worker_pool_size: int = 2
    worker_max_overflow: int = 0

    # Heavy compute concurrency starts at 1 (per S02 decision)
    heavy_concurrency: int = 1

    lease_ttl_seconds: float = 30.0
    heartbeat_interval_seconds: float = 10.0
    worker_poll_interval_seconds: float = 0.2
    event_publish_interval_seconds: float = 1.0
