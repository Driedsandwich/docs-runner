from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Blank values copied from .env.example fall back to the defaults below.
    model_config = SettingsConfigDict(env_file=None, extra="ignore", env_ignore_empty=True)
    vultr_inference_base_url: str = "https://api.vultrinference.com/v1"
    vultr_inference_key: SecretStr = SecretStr("")
    docsrunner_model_plan: str = ""
    docsrunner_model_extract: str = ""
    docsrunner_model_guard: str = ""
    docsrunner_public_origin: str = "http://localhost:8000"
    docsrunner_ui_fixtures: bool = False
    docsrunner_daily_llm_limit: int = Field(1000, ge=1)
    docsrunner_rate_per_hour: int = Field(10, ge=1)
    docsrunner_db: str = "data/budgets.sqlite3"
    docsrunner_sandbox_image: str = "docsrunner-sandbox:local"
    docsrunner_proxy_image: str = "docsrunner-proxy:local"
    docsrunner_egress_network: str = "docsrunner-egress"
    docsrunner_runtime: Literal["auto", "runc", "runsc"] = "auto"
    docsrunner_job_seconds: float = Field(300, gt=0, le=300)
    docsrunner_step_seconds: float = Field(60, gt=0, le=60)
    docsrunner_max_steps: int = Field(15, ge=1, le=15)
    docsrunner_max_llm_calls: int = Field(20, ge=1, le=20)

    @field_validator("docsrunner_public_origin")
    @classmethod
    def public_origin(cls, value: str) -> str:
        from urllib.parse import urlsplit

        from app.fetch import FetchError, validate_url

        value = value.rstrip("/")
        # Local UI development is allowed; live fetches still require public IPs.
        if value == "http://localhost:8000":
            return value
        try:
            normalized = validate_url(value)
        except FetchError as exc:
            raise ValueError(str(exc)) from None
        if urlsplit(normalized).path != "/":
            raise ValueError("Public origin must not contain a path")
        return normalized.rstrip("/")

    @field_validator("vultr_inference_base_url")
    @classmethod
    def vultr_only(cls, value: str) -> str:
        # Every production model call must stay on the competition endpoint.
        if value.rstrip("/") != "https://api.vultrinference.com/v1":
            raise ValueError("Only the Vultr Serverless Inference endpoint is supported")
        return value.rstrip("/")

