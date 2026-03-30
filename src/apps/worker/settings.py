from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    worker_max_concurrency: int = Field(default=4, ge=1)
    worker_poll_interval_seconds: float = Field(default=1.0, ge=0.0)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = WorkerSettings()
