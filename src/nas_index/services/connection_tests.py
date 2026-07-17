from collections.abc import Callable
from datetime import datetime, timedelta
from hashlib import sha256
import hmac
import json
from secrets import token_urlsafe

from nas_index.time import now_beijing
from nas_index.types import NasConnection


class ConnectionTestStore:
    def __init__(
        self,
        *,
        ttl_seconds: int,
        now: Callable[[], datetime] | None = None,
    ):
        self.ttl_seconds = ttl_seconds
        self.now = now or now_beijing
        self._tests: dict[str, tuple[NasConnection, str, datetime]] = {}

    def create(self, connection: NasConnection) -> str:
        token = token_urlsafe(32)
        expires_at = self.now() + timedelta(
            seconds=self.ttl_seconds
        )
        self._tests[token] = (
            connection,
            self._fingerprint(connection),
            expires_at,
        )
        return token

    def get(self, token: str | None) -> NasConnection | None:
        if not token:
            return None
        tested = self._tests.get(token)
        if tested is None:
            return None
        connection, _fingerprint, expires_at = tested
        if expires_at <= self.now():
            self._tests.pop(token, None)
            return None
        return connection

    def matches(
        self,
        token: str | None,
        connection: NasConnection,
    ) -> bool:
        if not token:
            return False
        tested = self._tests.get(token)
        if tested is None:
            return False
        _stored_connection, fingerprint, expires_at = tested
        if expires_at <= self.now():
            self._tests.pop(token, None)
            return False
        return hmac.compare_digest(
            fingerprint,
            self._fingerprint(connection),
        )

    def delete(self, token: str | None) -> None:
        if token:
            self._tests.pop(token, None)

    @staticmethod
    def _fingerprint(connection: NasConnection) -> str:
        payload = json.dumps(
            [
                connection.base_url.rstrip("/"),
                connection.port,
                connection.use_https,
                connection.username,
                connection.password,
                connection.skip_tls_verify,
            ],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        return sha256(payload.encode("utf-8")).hexdigest()
