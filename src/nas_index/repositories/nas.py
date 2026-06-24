from sqlalchemy import select
from sqlalchemy.orm import Session

from nas_index.models import NasCredential, NasServer
from nas_index.time import now_beijing
from nas_index.types import (
    NasCredentialValue,
    NasConnection,
    NasServerValue,
)


class NasRepository:
    def __init__(self, session: Session):
        self.session = session

    def create_server(
        self,
        *,
        name: str,
        base_url: str,
        port: int,
        use_https: bool,
        enabled: bool,
        sync_interval_minutes: int,
        username: str,
        password: str,
    ) -> NasServerValue:
        if not password:
            raise ValueError("首次保存时必须输入索引账号密码")

        now = now_beijing()
        row = NasServer(
            name=name.strip(),
            base_url=base_url.rstrip("/"),
            port=port,
            use_https=use_https,
            enabled=enabled,
            sync_interval_minutes=sync_interval_minutes,
            created_at=now,
            updated_at=now,
        )
        row.credential = NasCredential(
            username=username.strip(),
            password=password,
            updated_at=now,
        )
        self.session.add(row)
        self.session.flush()
        return self._server_value(row)

    def update_server(
        self,
        nas_id: int,
        *,
        name: str,
        base_url: str,
        port: int,
        use_https: bool,
        enabled: bool,
        sync_interval_minutes: int,
        username: str,
        password: str,
    ) -> NasServerValue:
        row = self.session.get(NasServer, nas_id)
        if row is None:
            raise LookupError("NAS 不存在")

        now = now_beijing()
        row.name = name.strip()
        row.base_url = base_url.rstrip("/")
        row.port = port
        row.use_https = use_https
        row.enabled = enabled
        row.sync_interval_minutes = sync_interval_minutes
        row.updated_at = now

        credential = row.credential
        if credential is None:
            if not password:
                raise ValueError("首次保存时必须输入索引账号密码")
            credential = NasCredential(nas_id=nas_id)
            row.credential = credential
            self.session.add(credential)
        credential.username = username.strip()
        if password:
            credential.password = password
        credential.updated_at = now
        self.session.flush()
        return self._server_value(row)

    def list_servers(self) -> list[NasServerValue]:
        return [
            self._server_value(row)
            for row in self.session.scalars(
                select(NasServer).order_by(
                    NasServer.name,
                    NasServer.id,
                )
            )
        ]

    def list_enabled_servers(self) -> list[NasServerValue]:
        return [
            self._server_value(row)
            for row in self.session.scalars(
                select(NasServer)
                .where(NasServer.enabled.is_(True))
                .order_by(NasServer.name, NasServer.id)
            )
        ]

    def get_server(self, nas_id: int) -> NasServerValue | None:
        row = self.session.get(NasServer, nas_id)
        if row is None:
            return None
        return self._server_value(row)

    def get_credential(
        self,
        nas_id: int,
    ) -> NasCredentialValue | None:
        row = self.session.get(NasCredential, nas_id)
        if row is None:
            return None
        return NasCredentialValue(
            nas_id=row.nas_id,
            username=row.username,
            password=row.password,
        )

    def connection_for_indexer(
        self,
        nas_id: int,
    ) -> NasConnection | None:
        server = self.get_server(nas_id)
        credential = self.get_credential(nas_id)
        if server is None or credential is None:
            return None
        return server.to_connection(
            username=credential.username,
            password=credential.password,
        )

    @staticmethod
    def _server_value(row: NasServer) -> NasServerValue:
        return NasServerValue(
            id=row.id,
            name=row.name,
            base_url=row.base_url,
            port=row.port,
            use_https=row.use_https,
            enabled=row.enabled,
            sync_interval_minutes=row.sync_interval_minutes,
        )
