from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class NasConnection:
    base_url: str
    port: int
    use_https: bool
    username: str
    password: str

    @property
    def endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}:{self.port}"


@dataclass(frozen=True, slots=True)
class NasServerValue:
    id: int
    name: str
    base_url: str
    port: int
    use_https: bool
    enabled: bool
    sync_interval_minutes: int
    full_resync_interval_hours: int

    def to_connection(
        self,
        *,
        username: str,
        password: str,
    ) -> NasConnection:
        return NasConnection(
            base_url=self.base_url,
            port=self.port,
            use_https=self.use_https,
            username=username,
            password=password,
        )


@dataclass(frozen=True, slots=True)
class NasCredentialValue:
    nas_id: int
    username: str
    password: str


@dataclass(frozen=True, slots=True)
class IndexedItem:
    name: str
    full_path: str
    parent_path: str
    entry_type: str
    size_bytes: int | None
    modified_at: datetime | None
    share_path: str | None = None


@dataclass(frozen=True, slots=True)
class UserAccess:
    nas_id: int
    username: str
    share_paths: tuple[str, ...]
    expires_at: datetime
    qnap_sid: str | None = None
