from pathlib import Path
from typing import Any
import os
import tomllib

from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    database_url: str = "sqlite:///data/nas-index.db"
    log_dir: Path = Path("logs")
    scan_page_size: int = 500
    scan_batch_size: int = 500
    scan_concurrency: int = 4
    scan_progress_interval_seconds: float = 2.0
    scan_skip_recycle: bool = True
    qnap_timeout_seconds: float = 20.0
    qnap_retry_attempts: int = 3
    user_access_ttl_seconds: int = 900
    sync_scheduler_poll_seconds: float = 10.0
    thumbnail_cache_dir: Path = Path("data/thumbnails")
    admin_password: str | None = None
    admin_session_ttl_seconds: int = 3600
    ai_api_key: str | None = None
    ai_base_url: str = "https://api.openai.com/v1"
    ai_model: str = "deepseek-v4"
    ai_timeout_seconds: float = 30.0
    ai_max_tokens: int = 700

    model_config = SettingsConfigDict(
        env_prefix="NAS_INDEX_",
        env_file=".env",
        extra="ignore",
    )


APP_CONFIG_FIELDS = {
    "database_url",
    "log_dir",
    "scan_page_size",
    "scan_batch_size",
    "scan_concurrency",
    "scan_progress_interval_seconds",
    "scan_skip_recycle",
    "qnap_timeout_seconds",
    "qnap_retry_attempts",
    "user_access_ttl_seconds",
    "sync_scheduler_poll_seconds",
    "thumbnail_cache_dir",
    "admin_password",
    "admin_session_ttl_seconds",
}

AI_CONFIG_FIELDS = {
    "api_key": "ai_api_key",
    "base_url": "ai_base_url",
    "model": "ai_model",
    "timeout_seconds": "ai_timeout_seconds",
    "max_tokens": "ai_max_tokens",
}


def load_settings(
    config_path: str | Path = "config.toml",
) -> AppSettings:
    values = _load_toml_values(Path(config_path))
    values.update(_environment_overrides())
    return AppSettings(**values)


def _load_toml_values(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}

    data = tomllib.loads(
        config_path.read_text(encoding="utf-8")
    )
    values: dict[str, Any] = {}
    app = data.get("app", {})
    if isinstance(app, dict):
        for key in APP_CONFIG_FIELDS:
            if key in app:
                values[key] = app[key]

    ai = data.get("ai", {})
    if isinstance(ai, dict):
        for config_key, settings_key in AI_CONFIG_FIELDS.items():
            if config_key in ai:
                values[settings_key] = ai[config_key]
    return values


def _environment_overrides() -> dict[str, str]:
    overrides: dict[str, str] = {}
    for field_name in AppSettings.model_fields:
        env_name = f"NAS_INDEX_{field_name.upper()}"
        if env_name in os.environ:
            overrides[field_name] = os.environ[env_name]
    return overrides
