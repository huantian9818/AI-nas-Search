from datetime import UTC, datetime, timedelta

from nas_index.services.admin import AdminSessionStore


def test_admin_session_store_round_trips_session():
    store = AdminSessionStore(
        ttl_seconds=300,
        now=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )

    token = store.create()

    assert store.get(token) is True


def test_admin_session_store_expires_and_deletes_sessions():
    current = datetime(2026, 1, 1, tzinfo=UTC)
    store = AdminSessionStore(
        ttl_seconds=60,
        now=lambda: current,
    )
    token = store.create()

    store.now = lambda: current + timedelta(seconds=61)
    assert store.get(token) is False

    fresh_token = store.create()
    store.delete(fresh_token)
    assert store.get(fresh_token) is False
