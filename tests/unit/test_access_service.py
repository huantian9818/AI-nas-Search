from datetime import UTC, datetime, timedelta

from nas_index.services.access import AccessSessionStore
from nas_index.types import UserAccess


def test_access_session_store_round_trips_allowed_shares():
    store = AccessSessionStore(
        ttl_seconds=300,
        now=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )

    token = store.create(
        nas_id=12,
        username="alice",
        share_paths=("/Public", "/Team"),
    )
    access = store.get(token)

    assert isinstance(access, UserAccess)
    assert access.nas_id == 12
    assert access.username == "alice"
    assert access.share_paths == ("/Public", "/Team")
    assert access.expires_at == datetime(2026, 1, 1, 0, 5, tzinfo=UTC)


def test_access_session_store_expires_sessions():
    current = datetime(2026, 1, 1, tzinfo=UTC)
    store = AccessSessionStore(
        ttl_seconds=60,
        now=lambda: current,
    )
    token = store.create(
        nas_id=1,
        username="alice",
        share_paths=("/Public",),
    )

    store.now = lambda: current + timedelta(seconds=61)

    assert store.get(token) is None
