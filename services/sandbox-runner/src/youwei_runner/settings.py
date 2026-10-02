import re
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RunnerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="YOUWEI_RUNNER_", extra="ignore")

    secret: str = Field(min_length=32)
    development: bool = False
    image: str = "python:3.13-alpine"
    runtime: str = "runsc"
    spool_root: str = "/tmp/youwei-runner"
    memory: str = "256m"
    timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    max_parallel: int = Field(default=1, ge=1, le=4)
    max_records: int = Field(default=128, ge=1)
    max_cached_bytes: int = Field(default=32 * 1024 * 1024, ge=1024)
    max_request_bytes: int = Field(default=8 * 1024 * 1024, gt=0)

    # --- research link (S07m): agent-runtime container execution ---
    # The research container runs the fixed agent-runtime image (pinned by
    # digest in production) with stdin/stdout forwarding and egress limited to
    # the approved gateway. The Runner holds only the PUBLIC key (kid -> PEM)
    # to verify the Controller's Ed25519 research grant; it never signs.
    agent_runtime_image: str = ""  # e.g. "youwei-agent-runtime@sha256:..."
    agent_runtime_gateway_url: str = ""  # the ONLY allowed egress destination
    agent_runtime_gateway_host: str = ""  # host[:port] resolved from gateway_url
    agent_runtime_public_keys: dict[str, str] = Field(default_factory=dict)  # kid -> PEM
    agent_runtime_exec_config_version: str = "research-exec-v1"
    agent_runtime_timeout_seconds: float = Field(default=300.0, gt=0, le=3600)
    agent_runtime_memory: str = "768m"
    agent_runtime_cpus: str = "1.0"
    agent_runtime_pids_limit: int = 128
    agent_runtime_max_stdout_bytes: int = Field(default=1_000_000, gt=0)
    agent_runtime_max_stderr_bytes: int = Field(default=256_000, gt=0)

    # --- experiment tool surface (S08): persistent store for experiment
    # authorizations + computation receipts. Empty string DISABLES the
    # experiment endpoints (they answer 503); the Controller-side slices
    # configure a dedicated directory (must survive Runner restarts).
    experiment_store_dir: str = ""

    # --- experiment instance dispatch (S08c-2): the experiment container
    # runs the agent-runtime image on a dedicated network whose reachable
    # set is {approved gateway, this Runner's tool plane} (owner decision
    # D1=A). The tool base URL is how CONTAINERS reach this Runner — it is
    # injected server-side, never taken from a request.
    experiment_network: str = "youwei-experiment"
    experiment_tool_base_url: str = ""

    @model_validator(mode="after")
    def production_constraints(self):
        if not self.development:
            if not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", self.image):
                raise ValueError("production sandbox image must be pinned by digest")
            if self.runtime != "runsc":
                raise ValueError("production sandbox requires runsc")
        if self.agent_runtime_image:
            if not self.development and not re.fullmatch(
                r"[^\s]+@sha256:[0-9a-f]{64}", self.agent_runtime_image
            ):
                raise ValueError("production agent-runtime image must be pinned by digest")
            # Egress restriction must be actually configured, never defaulted
            # away: a research entrypoint without an approved gateway is refused.
            if not self.agent_runtime_gateway_url:
                raise ValueError("agent-runtime gateway URL is required when the research entry is enabled")
        if self.experiment_store_dir:
            # The experiment surface (tool plane + dispatch) is enabled: the
            # instance container must be able to reach this Runner's tool
            # endpoints, and that address is deployment config, not request
            # data. The agent-runtime image and gateway are already required
            # above when the research entry is enabled.
            if not self.experiment_tool_base_url:
                raise ValueError(
                    "experiment tool base URL is required when the experiment "
                    "surface is enabled"
                )
            if not self.agent_runtime_image:
                raise ValueError(
                    "agent-runtime image is required when the experiment "
                    "surface is enabled"
                )
        return self
