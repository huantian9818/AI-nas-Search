from datetime import UTC, datetime

from sqlalchemy.orm import Session

from nas_index.repositories.entries import EntryRepository
from nas_index.repositories.nas import NasRepository
from nas_index.types import IndexedItem


def _create_server(session: Session) -> int:
    server = NasRepository(session).create_server(
        name="Office NAS",
        base_url="http://nas.local",
        port=5000,
        use_https=False,
        enabled=True,
        sync_interval_minutes=15,
        username="indexer",
        password="secret",
    )
    return server.id


def test_download_redirects_allowed_file_to_qnap(client):
    with Session(client.app.state.engine) as session:
        nas_id = _create_server(session)
        EntryRepository(session).upsert_batch(
            nas_id,
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
                    "苹果 主图.jpg",
                    "/Public/设计图/苹果 主图.jpg",
                    "/Public/设计图",
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
            nas_id,
            "/Public/设计图/苹果 主图.jpg",
        ).id

    token = client.app.state.access_store.create(
        nas_id=nas_id,
        username="alice",
        share_paths=("/Public",),
        qnap_sid="user-sid",
    )
    client.cookies.set("nas_access", token)

    response = client.get(
        f"/downloads/{entry_id}",
        follow_redirects=False,
    )

    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith(
        "http://nas.local:5000/cgi-bin/filemanager/utilRequest.cgi?"
    )
    assert "func=download" in location
    assert "sid=user-sid" in location
    assert "isfolder=0" in location
    assert "source_path=%2FPublic%2F%E8%AE%BE%E8%AE%A1%E5%9B%BE" in location
    assert "source_file=%E8%8B%B9%E6%9E%9C%20%E4%B8%BB%E5%9B%BE.jpg" in location


def test_download_rejects_file_outside_user_shares(client):
    with Session(client.app.state.engine) as session:
        nas_id = _create_server(session)
        EntryRepository(session).upsert_batch(
            nas_id,
            [
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
                    "账单.pdf",
                    "/Finance/账单.pdf",
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
            nas_id,
            "/Finance/账单.pdf",
        ).id

    token = client.app.state.access_store.create(
        nas_id=nas_id,
        username="alice",
        share_paths=("/Public",),
        qnap_sid="user-sid",
    )
    client.cookies.set("nas_access", token)

    response = client.get(
        f"/downloads/{entry_id}",
        follow_redirects=False,
    )

    assert response.status_code == 404
    assert "nas.local" not in response.text


def test_browse_page_shows_download_link_for_files(
    client,
    web_seeded_entries,
):
    response = client.get(
        "/browse",
        params={"path": "/Public"},
    )

    assert response.status_code == 200
    assert "/downloads/" in response.text
    assert "下载" in response.text


def test_browse_page_shows_select_all_control_for_batch_download(
    client,
    web_seeded_entries,
):
    response = client.get(
        "/browse",
        params={"path": "/Public"},
    )

    assert response.status_code == 200
    assert 'data-batch-download-form' in response.text
    assert 'data-select-all' in response.text
    assert "全选" in response.text
    assert 'data-entry-select' in response.text
    assert 'data-batch-download-submit' in response.text


def test_batch_download_redirects_same_directory_files_to_qnap(
    client,
):
    with Session(client.app.state.engine) as session:
        nas_id = _create_server(session)
        EntryRepository(session).upsert_batch(
            nas_id,
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
                    "/Public/设计图/苹果.jpg",
                    "/Public/设计图",
                    "file",
                    42,
                    datetime(2026, 1, 1, tzinfo=UTC),
                    share_path="/Public",
                ),
                IndexedItem(
                    "香蕉.png",
                    "/Public/设计图/香蕉.png",
                    "/Public/设计图",
                    "file",
                    43,
                    datetime(2026, 1, 1, tzinfo=UTC),
                    share_path="/Public",
                ),
            ],
            generation=1,
        )
        session.commit()
        repository = EntryRepository(session)
        apple_id = repository.get_by_nas_path(
            nas_id,
            "/Public/设计图/苹果.jpg",
        ).id
        banana_id = repository.get_by_nas_path(
            nas_id,
            "/Public/设计图/香蕉.png",
        ).id

    token = client.app.state.access_store.create(
        nas_id=nas_id,
        username="alice",
        share_paths=("/Public",),
        qnap_sid="user-sid",
    )
    client.cookies.set("nas_access", token)

    response = client.post(
        "/downloads/batch",
        data={
            "entry_ids": [
                str(apple_id),
                str(banana_id),
            ],
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    location = response.headers["location"]
    assert "func=download" in location
    assert "sid=user-sid" in location
    assert "source_path=%2FPublic%2F%E8%AE%BE%E8%AE%A1%E5%9B%BE" in location
    assert "source_file=%E8%8B%B9%E6%9E%9C.jpg" in location
    assert "source_file=%E9%A6%99%E8%95%89.png" in location
    assert "source_total=2" in location


def test_batch_download_rejects_cross_directory_files(client):
    with Session(client.app.state.engine) as session:
        nas_id = _create_server(session)
        EntryRepository(session).upsert_batch(
            nas_id,
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
                    "/Public/设计图/苹果.jpg",
                    "/Public/设计图",
                    "file",
                    42,
                    datetime(2026, 1, 1, tzinfo=UTC),
                    share_path="/Public",
                ),
                IndexedItem(
                    "香蕉.png",
                    "/Public/其它/香蕉.png",
                    "/Public/其它",
                    "file",
                    43,
                    datetime(2026, 1, 1, tzinfo=UTC),
                    share_path="/Public",
                ),
            ],
            generation=1,
        )
        session.commit()
        repository = EntryRepository(session)
        apple_id = repository.get_by_nas_path(
            nas_id,
            "/Public/设计图/苹果.jpg",
        ).id
        banana_id = repository.get_by_nas_path(
            nas_id,
            "/Public/其它/香蕉.png",
        ).id

    token = client.app.state.access_store.create(
        nas_id=nas_id,
        username="alice",
        share_paths=("/Public",),
        qnap_sid="user-sid",
    )
    client.cookies.set("nas_access", token)

    response = client.post(
        "/downloads/batch",
        data={
            "entry_ids": [
                str(apple_id),
                str(banana_id),
            ],
        },
        follow_redirects=False,
    )

    assert response.status_code == 422


def test_browse_page_shows_batch_download_controls(
    client,
    web_seeded_entries,
):
    response = client.get(
        "/browse",
        params={"path": "/Public"},
    )

    assert response.status_code == 200
    assert 'action="/downloads/batch"' in response.text
    assert 'type="checkbox"' in response.text
    assert 'name="entry_ids"' in response.text
    assert "批量下载" in response.text
