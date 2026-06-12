from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nas_index.config import AppSettings
from nas_index.db import create_database_engine, init_database
from nas_index.web.app import create_app


@pytest.fixture
def settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        log_dir=tmp_path / "logs",
    )


@pytest.fixture
def client(settings: AppSettings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def database(tmp_path: Path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'unit.db'}")
    init_database(engine)
    yield engine
    engine.dispose()
