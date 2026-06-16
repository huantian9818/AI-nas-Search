from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe

from nas_index.types import UserAccess


class AccessSessionStore:
    def __init__(
        self,
        *,
        ttl_seconds: int,
        now: Callable[[], datetime] | None = None,
    ):
        self.ttl_seconds = ttl_seconds
        self.now = now or (lambda: datetime.now(UTC))
        self._sessions: dict[str, UserAccess] = {}

    def create(
        self,
        *,
        nas_id: int,
        username: str,
        share_paths: tuple[str, ...],
    ) -> str:
        token = token_urlsafe(32)
        expires_at = self.now() + timedelta(
            seconds=self.ttl_seconds
        )
        self._sessions[token] = UserAccess(
            nas_id=nas_id,
            username=username,
            share_paths=tuple(sorted(set(share_paths))),
            expires_at=expires_at,
        )
        return token

    def get(self, token: str | None) -> UserAccess | None:
        if not token:
            return None
        access = self._sessions.get(token)
        if access is None:
            return None
        if access.expires_at <= self.now():
            self._sessions.pop(token, None)
            return None
        return access

    def delete(self, token: str | None) -> None:
        if token:
            self._sessions.pop(token, None)
