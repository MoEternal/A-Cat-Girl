from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


FROZEN = bool(getattr(sys, "frozen", False))
APPLICATION_ROOT = (
    Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parents[3]
)
RESOURCE_ROOT = (
    Path(getattr(sys, "_MEIPASS")).resolve()
    if FROZEN and getattr(sys, "_MEIPASS", None)
    else APPLICATION_ROOT
)
PROJECT_ROOT = APPLICATION_ROOT


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=APPLICATION_ROOT / ".env",
        env_prefix="CATGIRL_",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8732, ge=1, le=65535)
    data_dir: Path = APPLICATION_ROOT / "data"
    log_level: str = "INFO"
    model_timeout_seconds: float = Field(default=120.0, ge=5.0, le=600.0)
    media_download_timeout_seconds: float = Field(default=30.0, ge=5.0, le=120.0)

    @property
    def database_path(self) -> Path:
        return self.data_dir / "catgirl.db"

    @property
    def secret_key_path(self) -> Path:
        return self.data_dir / "secret.key"

    @property
    def frontend_dist(self) -> Path:
        return RESOURCE_ROOT / "frontend" / "dist"

    @property
    def built_in_plugins_dir(self) -> Path:
        return RESOURCE_ROOT / "plugins"


@lru_cache
def get_settings() -> Settings:
    return Settings()
