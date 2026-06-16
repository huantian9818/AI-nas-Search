from datetime import UTC, datetime

from sqlalchemy.orm import Session

from nas_index.models import NasConfig
from nas_index.repositories.nas import NasRepository
from nas_index.types import NasConnection


class ConfigRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self) -> NasConnection | None:
        repository = NasRepository(self.session)
        servers = repository.list_servers()
        if servers:
            return repository.connection_for_indexer(
                servers[0].id
            )

        row = self.session.get(NasConfig, 1)
        if row is None:
            return None
        return self._to_value(row)

    def save(self, value: NasConnection) -> NasConnection:
        repository = NasRepository(self.session)
        servers = repository.list_servers()
        name = (
            value.base_url.removeprefix("http://")
            .removeprefix("https://")
            .strip("/")
        )
        if servers:
            repository.update_server(
                servers[0].id,
                name=name,
                base_url=value.base_url.rstrip("/"),
                port=value.port,
                use_https=value.use_https,
                enabled=True,
                sync_interval_minutes=30,
                full_resync_interval_hours=24,
                username=value.username,
                password=value.password,
            )
            connection = repository.connection_for_indexer(
                servers[0].id
            )
            if connection is None:
                raise ValueError("NAS 配置保存失败")
            return connection

        row = self.session.get(NasConfig, 1)
        password = value.password or (row.password if row else "")
        if not password:
            raise ValueError("首次保存时必须输入密码")

        if row is not None:
            self.session.delete(row)
            self.session.flush()

        server = repository.create_server(
            name=name,
            base_url=value.base_url.rstrip("/"),
            port=value.port,
            use_https=value.use_https,
            enabled=True,
            sync_interval_minutes=30,
            full_resync_interval_hours=24,
            username=value.username,
            password=password,
        )
        return server.to_connection(
            username=value.username,
            password=password,
        )

    @staticmethod
    def _to_value(row: NasConfig) -> NasConnection:
        return NasConnection(
            base_url=row.base_url,
            port=row.port,
            use_https=row.use_https,
            username=row.username,
            password=row.password,
        )
