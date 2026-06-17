from pathlib import Path

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
    admin_password: str | None = None
    admin_session_ttl_seconds: int = 3600

    model_config = SettingsConfigDict(
        env_prefix="NAS_INDEX_",
        env_file=".env",
        extra="ignore",
    )
