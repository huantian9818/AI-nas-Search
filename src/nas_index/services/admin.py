from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe


class AdminSessionStore:
    def __init__(
        self,
        *,
        ttl_seconds: int,
        now: Callable[[], datetime] | None = None,
    ):
        self.ttl_seconds = ttl_seconds
        self.now = now or (lambda: datetime.now(UTC))
        self._sessions: dict[str, datetime] = {}

    def create(self) -> str:
        token = token_urlsafe(32)
        self._sessions[token] = self.now() + timedelta(
            seconds=self.ttl_seconds
        )
        return token

    def get(self, token: str | None) -> bool:
        if not token:
            return False
        expires_at = self._sessions.get(token)
        if expires_at is None:
            return False
        if expires_at <= self.now():
            self._sessions.pop(token, None)
            return False
        return True

    def delete(self, token: str | None) -> None:
        if token:
            self._sessions.pop(token, None)
