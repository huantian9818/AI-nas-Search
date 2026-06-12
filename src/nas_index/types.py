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
class IndexedItem:
    name: str
    full_path: str
    parent_path: str
    entry_type: str
    size_bytes: int | None
    modified_at: datetime | None
