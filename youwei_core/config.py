"""Runtime settings. Resource baselines follow the S02 decision:
4 vCPU / 7.8 GB host, API and Worker one process each, conservative pools."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {
        "env_prefix": "YOUWEI_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

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
    scheduler_interval_seconds: float = 60.0

    # S02b: admin bootstrap key (operator-configured). When set, this
    # bearer token manages tenants/api-keys and nothing else.
    admin_api_key: str = ""

    # S02b: HMAC secret for per-job capability tokens. Configure per
    # deployment; a stable value keeps issued tokens valid across restarts.
    capability_secret: str = "dev-insecure-capability-secret"

    # S02b closeout: ops alert thresholds (architecture section 11).
    # Age-based signals compare against these; count-based signals
    # (expired leases, overdue runs) alert on any occurrence.
    alert_queue_backlog_age_seconds: float = 300.0
    alert_unpublished_events_age_seconds: float = 60.0
    alert_pending_reconciliation_age_seconds: float = 3600.0
    alert_wal_archive_stale_seconds: float = 1800.0

    # S04: Tiingo collection (token lives in the gitignored .env).
    # The evaluation tier's exact quota is not exposed via headers;
    # 1 req/s is the conservative default until measured.
    tiingo_token: str = ""
    tiingo_base_url: str = "https://api.tiingo.com/tiingo"
    tiingo_min_request_interval_seconds: float = 1.0

    # time-protocol §5: sealing must stop when the app clock drifts
    # from the database clock beyond this threshold (final value to be
    # fixed before the first campaign; measured on the target host).
    max_clock_skew_seconds: float = 5.0

    # Container policy and Docker permissions belong to the remote Runner.
    runner_url: str = ""
    runner_secret: str = ""

    # S07: agent-runtime subprocess (Hermes research). The command is the
    # full argv for `youwei-agent-runtime research-once` (venv python + main
    # module + subcommand), split on whitespace. PYTHONPATH carries the pinned
    # Hermes checkout so the adapter can import it. Disabled by default; only
    # a Phase 1B campaign with a non-zero LLM budget enables it.
    agent_runtime_enabled: bool = False
    agent_runtime_command: str = ""  # e.g. ".../python -m youwei_agent_runtime.main research-once"
    agent_runtime_pythonpath: str = ""  # colon-separated; Hermes checkout dir
    agent_runtime_timeout_seconds: float = 300.0
    # gateway/model wiring for the research turn (reached by Hermes inside
    # the agent-runtime process, NOT through Core's GatewayClient)
    agent_llm_base_url: str = ""
    agent_llm_api_key: str = ""
    agent_llm_model: str = ""
    # Conservative per-turn reservation for the agent-runtime research turn
    # (micro-USD). 0 disables turn-level budget accounting; a non-zero value
    # reserves before spawning the subprocess and settles the priced usage
    # after (S07k). The Controller cannot see Hermes's internal calls, so
    # this is a configured upper bound, not a per-call estimate.
    agent_turn_reserve_micros: int = 0
