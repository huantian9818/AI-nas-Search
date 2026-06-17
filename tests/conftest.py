from pathlib import Path
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from nas_index.config import AppSettings
from nas_index.db import create_database_engine, init_database
from nas_index.repositories.entries import EntryRepository
from nas_index.types import IndexedItem
from nas_index.types import UserAccess
from nas_index.web.app import create_app


@pytest.fixture
def settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        log_dir=tmp_path / "logs",
        admin_password="admin-secret",
    )


@pytest.fixture
def client(settings: AppSettings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def admin_client(client):
    response = client.post(
        "/admin/login",
        data={"password": "admin-secret"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return client


@pytest.fixture
def database(tmp_path: Path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'unit.db'}")
    init_database(engine)
    yield engine
    engine.dispose()


def seed_entries(engine) -> None:
    with Session(engine) as session:
        EntryRepository(session).upsert_batch(
            [
                IndexedItem(
                    "Public",
                    "/Public",
                    "/",
                    "directory",
                    None,
                    None,
                ),
                IndexedItem(
                    "年度项目计划.docx",
                    "/Public/年度项目计划.docx",
                    "/Public",
                    "file",
                    128,
                    datetime(
                        2026,
                        1,
                        1,
                        tzinfo=UTC,
                    ),
                ),
                IndexedItem(
                    "资料",
                    "/Public/资料",
                    "/Public",
                    "directory",
                    None,
                    None,
                ),
                IndexedItem(
                    "nested-only.txt",
                    "/Public/资料/nested-only.txt",
                    "/Public/资料",
                    "file",
                    8,
                    datetime(
                        2026,
                        1,
                        1,
                        tzinfo=UTC,
                    ),
                ),
            ],
            generation=1,
        )
        session.commit()


@pytest.fixture
def seeded_entries(database):
    seed_entries(database)


@pytest.fixture
def web_public_access(client):
    token = client.app.state.access_store.create(
        nas_id=1,
        username="alice",
        share_paths=("/Public",),
    )
    client.cookies.set("nas_access", token)
    return UserAccess(
        nas_id=1,
        username="alice",
        share_paths=("/Public",),
        expires_at=client.app.state.access_store.get(token).expires_at,
    )


@pytest.fixture
def web_seeded_entries(client, web_public_access):
    seed_entries(client.app.state.engine)
