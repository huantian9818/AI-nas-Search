from dataclasses import replace
from datetime import UTC, datetime, timedelta

from nas_index.services.connection_tests import ConnectionTestStore
from nas_index.types import NasConnection


NOW = datetime(2026, 6, 24, 10, 0, tzinfo=UTC)
CONNECTION = NasConnection(
    base_url="http://nas.local",
    port=5000,
    use_https=False,
    username="indexer",
    password="secret",
)


def test_connection_test_token_matches_only_original_connection():
    store = ConnectionTestStore(
        ttl_seconds=300,
        now=lambda: NOW,
    )

    token = store.create(CONNECTION)

    assert store.matches(token, CONNECTION) is True
    assert store.matches(
        token,
        replace(CONNECTION, port=5001),
    ) is False
    assert store.matches(
        token,
        replace(CONNECTION, skip_tls_verify=True),
    ) is False


def test_connection_test_token_expires():
    current = NOW
    store = ConnectionTestStore(
        ttl_seconds=300,
        now=lambda: current,
    )
    token = store.create(CONNECTION)

    current += timedelta(seconds=301)

    assert store.matches(token, CONNECTION) is False


def test_connection_test_token_can_be_deleted():
    store = ConnectionTestStore(
        ttl_seconds=300,
        now=lambda: NOW,
    )
    token = store.create(CONNECTION)

    store.delete(token)

    assert store.matches(token, CONNECTION) is False
