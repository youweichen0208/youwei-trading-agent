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

    @model_validator(mode="after")
    def production_constraints(self):
        if not self.development:
            if not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", self.image):
                raise ValueError("production sandbox image must be pinned by digest")
            if self.runtime != "runsc":
                raise ValueError("production sandbox requires runsc")
        return self
