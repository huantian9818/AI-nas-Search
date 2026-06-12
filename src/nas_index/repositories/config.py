from datetime import UTC, datetime

from sqlalchemy.orm import Session

from nas_index.models import NasConfig
from nas_index.types import NasConnection


class ConfigRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self) -> NasConnection | None:
        row = self.session.get(NasConfig, 1)
        if row is None:
            return None
        return self._to_value(row)

    def save(self, value: NasConnection) -> NasConnection:
        row = self.session.get(NasConfig, 1)
        password = value.password or (row.password if row else "")
        if not password:
            raise ValueError("首次保存时必须输入密码")

        if row is None:
            row = NasConfig(id=1)
            self.session.add(row)

        row.base_url = value.base_url.rstrip("/")
        row.port = value.port
        row.use_https = value.use_https
        row.username = value.username.strip()
        row.password = password
        row.updated_at = datetime.now(UTC)
        return self._to_value(row)

    @staticmethod
    def _to_value(row: NasConfig) -> NasConnection:
        return NasConnection(
            base_url=row.base_url,
            port=row.port,
            use_https=row.use_https,
            username=row.username,
            password=row.password,
        )
