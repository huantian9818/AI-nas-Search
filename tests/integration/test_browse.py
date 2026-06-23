from datetime import UTC, datetime

from sqlalchemy.orm import Session

from nas_index.repositories.entries import EntryRepository
from nas_index.repositories.nas import NasRepository
from nas_index.services.thumbnails import ThumbnailResult
from nas_index.types import IndexedItem


def test_browse_page_lists_direct_children(
    client,
    web_seeded_entries,
):
    response = client.get(
        "/browse",
        params={"path": "/Public"},
    )

    assert response.status_code == 200
    assert "年度项目计划.docx" in response.text
    assert "nested-only.txt" not in response.text


def test_browse_tree_keeps_current_branch_open_without_expand_button(
    client,
    web_seeded_entries,
):
    response = client.get(
        "/browse",
        params={"path": "/Public/资料"},
    )

    assert response.status_code == 200
    assert 'href="/browse?path=/Public"' in response.text
    assert (
        'href="/browse?path=/Public/%E8%B5%84%E6%96%99"'
        in response.text
    )
    assert "tree-list-nested" in response.text
    assert "tree-link is-ancestor" in response.text
    assert "tree-link is-current" in response.text
    assert "nested-only.txt" in response.text
    assert "展开" not in response.text


def test_browse_page_uses_icon_grid_with_thumbnail_urls(
    client,
    web_seeded_entries,
):
    response = client.get(
        "/browse",
        params={"path": "/Public"},
    )

    assert response.status_code == 200
    assert "browse-grid" in response.text
    assert "browse-tile" in response.text
    assert "table-wrap" not in response.text
    assert "年度项目计划.docx" in response.text


def test_browse_grid_links_image_files_to_thumbnail_endpoint(
    client,
    web_public_access,
):
    with Session(client.app.state.engine) as session:
        EntryRepository(session).upsert_batch(
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
                    "苹果主图.jpg",
                    "/Public/苹果主图.jpg",
                    "/Public",
                    "file",
                    42,
                    datetime(2026, 1, 1, tzinfo=UTC),
                    share_path="/Public",
                ),
            ],
            generation=1,
        )
        session.commit()
        entry_id = EntryRepository(session).get_by_nas_path(
            1,
            "/Public/苹果主图.jpg",
        ).id

    response = client.get(
        "/browse",
        params={"path": "/Public"},
    )

    assert response.status_code == 200
    assert f'src="/thumbnails/{entry_id}"' in response.text
    assert 'loading="lazy"' in response.text
    assert "data-thumbnail-fallback" in response.text
    assert "苹果主图.jpg" in response.text


def test_browse_grid_hides_dotfiles(
    client,
    web_public_access,
):
    with Session(client.app.state.engine) as session:
        EntryRepository(session).upsert_batch(
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
                    ".DS_Store",
                    "/Public/.DS_Store",
                    "/Public",
                    "file",
                    6148,
                    datetime(2026, 1, 1, tzinfo=UTC),
                    share_path="/Public",
                ),
                IndexedItem(
                    "正常图片.jpg",
                    "/Public/正常图片.jpg",
                    "/Public",
                    "file",
                    42,
                    datetime(2026, 1, 1, tzinfo=UTC),
                    share_path="/Public",
                ),
            ],
            generation=1,
        )
        session.commit()

    response = client.get(
        "/browse",
        params={"path": "/Public"},
    )

    assert response.status_code == 200
    assert "正常图片.jpg" in response.text
    assert ".DS_Store" not in response.text


def test_thumbnail_route_rejects_entries_outside_access(client):
    with Session(client.app.state.engine) as session:
        nas = NasRepository(session).create_server(
            name="Office NAS",
            base_url="http://nas.local",
            port=5000,
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
                    "Finance",
                    "/Finance",
                    "/",
                    "directory",
                    None,
                    None,
                    share_path="/Finance",
                ),
                IndexedItem(
                    "财务图.jpg",
                    "/Finance/财务图.jpg",
                    "/Finance",
                    "file",
                    42,
                    datetime(2026, 1, 1, tzinfo=UTC),
                    share_path="/Finance",
                ),
            ],
            generation=1,
        )
        session.commit()
        entry_id = EntryRepository(session).get_by_nas_path(
            nas.id,
            "/Finance/财务图.jpg",
        ).id

    token = client.app.state.access_store.create(
        nas_id=nas.id,
        username="alice",
        share_paths=("/Public",),
    )
    client.cookies.set("nas_access", token)

    response = client.get(f"/thumbnails/{entry_id}")

    assert response.status_code == 404


def test_thumbnail_route_returns_allowed_cached_image(
    client,
    web_public_access,
    tmp_path,
):
    with Session(client.app.state.engine) as session:
        nas = NasRepository(session).create_server(
            name="Office NAS",
            base_url="http://nas.local",
            port=5000,
            use_https=False,
            enabled=True,
            sync_interval_minutes=15,
            full_resync_interval_hours=24,
            username="indexer",
            password="secret",
        )
        assert nas.id == web_public_access.nas_id
        EntryRepository(session).upsert_batch(
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
                    "苹果.jpg",
                    "/Public/苹果.jpg",
                    "/Public",
                    "file",
                    42,
                    datetime(2026, 1, 1, tzinfo=UTC),
                    share_path="/Public",
                ),
            ],
            generation=1,
        )
        session.commit()
        entry_id = EntryRepository(session).get_by_nas_path(
            1,
            "/Public/苹果.jpg",
        ).id

    image_path = tmp_path / "thumb.jpg"
    image_path.write_bytes(b"jpeg")

    class FakeThumbnailService:
        async def get(self, entry, connection):
            assert entry.id == entry_id
            assert connection.username
            return ThumbnailResult(
                path=image_path,
                media_type="image/jpeg",
            )

    client.app.state.thumbnail_service = FakeThumbnailService()

    response = client.get(f"/thumbnails/{entry_id}")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")
    assert response.content == b"jpeg"


def test_thumbnail_route_releases_database_session_before_thumbnail_fetch(
    client,
    web_public_access,
    tmp_path,
):
    with Session(client.app.state.engine) as session:
        nas = NasRepository(session).create_server(
            name="Office NAS",
            base_url="http://nas.local",
            port=5000,
            use_https=False,
            enabled=True,
            sync_interval_minutes=15,
            full_resync_interval_hours=24,
            username="indexer",
            password="secret",
        )
        assert nas.id == web_public_access.nas_id
        EntryRepository(session).upsert_batch(
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
                    "苹果.jpg",
                    "/Public/苹果.jpg",
                    "/Public",
                    "file",
                    42,
                    datetime(2026, 1, 1, tzinfo=UTC),
                    share_path="/Public",
                ),
            ],
            generation=1,
        )
        session.commit()
        entry_id = EntryRepository(session).get_by_nas_path(
            web_public_access.nas_id,
            "/Public/苹果.jpg",
        ).id

    image_path = tmp_path / "thumb.jpg"
    image_path.write_bytes(b"jpeg")
    real_session_factory = client.app.state.session_factory
    state = {"closed": False}

    class TrackingSession:
        def __enter__(self):
            state["closed"] = False
            self._session = real_session_factory()
            return self._session.__enter__()

        def __exit__(self, *exc_info):
            try:
                return self._session.__exit__(*exc_info)
            finally:
                state["closed"] = True

    def tracking_session_factory():
        return TrackingSession()

    class FakeThumbnailService:
        async def get(self, entry, connection):
            assert entry.id == entry_id
            assert state["closed"] is True
            return ThumbnailResult(
                path=image_path,
                media_type="image/jpeg",
            )

    client.app.state.session_factory = tracking_session_factory
    client.app.state.thumbnail_service = FakeThumbnailService()

    response = client.get(f"/thumbnails/{entry_id}")

    assert response.status_code == 200
