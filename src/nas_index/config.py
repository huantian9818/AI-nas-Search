from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    database_url: str = "sqlite:///data/nas-index.db"
    log_dir: Path = Path("logs")
    scan_page_size: int = 500
    scan_batch_size: int = 100
    qnap_timeout_seconds: float = 20.0
    qnap_retry_attempts: int = 3

    model_config = SettingsConfigDict(
        env_prefix="NAS_INDEX_",
        env_file=".env",
        extra="ignore",
    )
