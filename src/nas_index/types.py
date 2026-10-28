from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class NasConnection:
    base_url: str
    port: int
    use_https: bool
    username: str
    password: str
    skip_tls_verify: bool = False

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
    skip_tls_verify: bool
    enabled: bool
    sync_interval_minutes: int
    scan_transport: str = "file_station"
    webdav_port: int | None = None
    webdav_use_https: bool = False

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
            skip_tls_verify=self.skip_tls_verify,
        )

    def to_webdav_connection(
        self,
        *,
        username: str,
        password: str,
    ) -> NasConnection | None:
        if self.webdav_port is None:
            return None
        host = urlsplit(self.base_url).hostname
        if not host:
            return None
        scheme = "https" if self.webdav_use_https else "http"
        return NasConnection(
            base_url=f"{scheme}://{host}",
            port=self.webdav_port,
            use_https=self.webdav_use_https,
            username=username,
            password=password,
            skip_tls_verify=self.skip_tls_verify,
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
