from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from yta_alpha.models import DataMode


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="YTA_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )
    env: Literal["development", "test", "staging", "production"] = "development"
    data_mode: DataMode = DataMode.REPLAY
    experimental_models: Literal["DISABLED", "ENABLED"] = "DISABLED"
    jobs_dispatch: Literal["OPEN", "PAUSED"] = "OPEN"
    alpha_token: SecretStr = Field(min_length=32)
    alpha_tools_token: SecretStr | None = Field(default=None, min_length=32)
    database_url: SecretStr | None = None
    git_sha: str = Field(default="0" * 40, pattern=r"^[a-f0-9]{40}$")
    otlp_traces_url: str | None = None

    @model_validator(mode="after")
    def production_requirements(self) -> "Settings":
        if self.otlp_traces_url is not None:
            url = urlsplit(self.otlp_traces_url)
            if (
                url.scheme not in ("http", "https")
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
                or url.path != "/v1/traces"
            ):
                raise ValueError(
                    "Tracing requires an HTTP(S) /v1/traces endpoint without credentials or query"
                )
        if self.alpha_tools_token is not None:
            token = self.alpha_tools_token.get_secret_value()
            if (
                token == self.alpha_token.get_secret_value()
                or token.startswith("replace-with")
                or not token.isascii()
                or any(c.isspace() for c in token)
            ):
                raise ValueError("Tools require a distinct, non-placeholder ASCII token")
        if self.env in ("production", "staging"):
            if self.data_mode != DataMode.LIVE_RESEARCH:
                raise ValueError("Deployed environments require LIVE_RESEARCH mode")
            if self.git_sha == "0" * 40 or self.database_url is None:
                raise ValueError("Deployment needs a real git SHA and database configuration")
        if self.data_mode == DataMode.LIVE_RESEARCH and self.git_sha == "0" * 40:
            raise ValueError("Real research needs a traceable git SHA")
        if self.alpha_token.get_secret_value().startswith("replace-with"):
            raise ValueError("Replace the example service token before starting")
        return self
