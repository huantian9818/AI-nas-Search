from datetime import UTC, datetime

from sqlalchemy.orm import Session

from nas_index.repositories.entries import EntryRepository
from nas_index.repositories.nas import NasRepository
from nas_index.types import IndexedItem


def _seed_nas_with_two_shares(client) -> int:
    with Session(client.app.state.engine) as session:
        nas = NasRepository(session).create_server(
            name="Office NAS",
            base_url="http://nas.local",
            port=8080,
            use_https=False,
            enabled=True,
            sync_interval_minutes=15,
            full_resync_interval_hours=24,
            username="indexer",
            password="secret",
        )
        EntryRepository(session).upsert_batch(
            nas.id,
            [
                IndexedItem(
                    "Public",
                    "/Public",
                    "/",
                    "directory",
                    None,
                    None,
                    share_path="/Public",
                ),
                IndexedItem(
                    "public-budget.xlsx",
                    "/Public/public-budget.xlsx",
                    "/Public",
                    "file",
                    16,
                    datetime(2026, 1, 1, tzinfo=UTC),
                    share_path="/Public",
                ),
                IndexedItem(
                    "Finance",
                    "/Finance",
                    "/",
                    "directory",
                    None,
                    None,
                    share_path="/Finance",
                ),
                IndexedItem(
                    "finance-budget.xlsx",
                    "/Finance/finance-budget.xlsx",
                    "/Finance",
                    "file",
                    32,
                    datetime(2026, 1, 1, tzinfo=UTC),
                    share_path="/Finance",
                ),
            ],
            generation=1,
        )
        session.commit()
        return nas.id


def test_browse_redirects_to_access_without_session(client):
    response = client.get("/browse", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/access"


def test_access_login_filters_browse_and_search_by_allowed_shares(
    client,
    monkeypatch,
):
    nas_id = _seed_nas_with_two_shares(client)

    async def fake_check_user_access(
        *,
        server,
        username,
        password,
        settings,
    ):
        assert server.id == nas_id
        assert username == "alice"
        assert password == "pw"
        assert settings is client.app.state.settings
        return ("/Public",)

    monkeypatch.setattr(
        client.app.state,
        "access_checker",
        fake_check_user_access,
        raising=False,
    )

    response = client.post(
        "/access",
        data={
            "nas_id": str(nas_id),
            "username": "alice",
            "password": "pw",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/browse"

    browse_response = client.get("/browse")
    assert browse_response.status_code == 200
    assert "Public" in browse_response.text
    assert "Finance" not in browse_response.text

    search_response = client.get(
        "/search",
        params={"q": "budget"},
    )
    assert search_response.status_code == 200
    assert "public-<mark>budget</mark>.xlsx" in search_response.text
    assert "finance-budget.xlsx" not in search_response.text
