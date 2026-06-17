from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from nas_index.config import AppSettings
from nas_index.repositories.nas import NasRepository
from nas_index.web.app import create_app


def _create_nas(client, name: str = "Office") -> int:
    with Session(client.app.state.engine) as session:
        nas_id = NasRepository(session).create_server(
            name=name,
            base_url=f"http://{name.lower()}.local",
            port=8080,
            use_https=False,
            enabled=True,
            sync_interval_minutes=30,
            full_resync_interval_hours=24,
            username="indexer",
            password="secret",
        ).id
        session.commit()
        return nas_id


def test_settings_redirects_to_admin_login(client):
    response = client.get(
        "/settings",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith(
        "/admin/login"
    )


def test_scan_start_redirects_to_admin_login(client):
    response = client.post(
        "/scans",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith(
        "/admin/login"
    )


def test_admin_login_allows_settings(admin_client):
    response = admin_client.get("/settings")

    assert response.status_code == 200
    assert "NAS 管理" in response.text


def test_dashboard_hides_sync_controls_from_non_admin(client):
    nas_id = _create_nas(client)

    response = client.get("/")

    assert response.status_code == 200
    assert f'name="nas_id" value="{nas_id}"' not in response.text
    assert "同步 Office" not in response.text
    assert 'href="/settings"' not in response.text


def test_dashboard_shows_sync_controls_to_admin(admin_client):
    nas_id = _create_nas(admin_client)

    response = admin_client.get("/")

    assert response.status_code == 200
    assert f'name="nas_id" value="{nas_id}"' in response.text
    assert "同步 Office" in response.text
    assert 'href="/settings"' in response.text


def test_admin_login_requires_configured_password(tmp_path: Path):
    settings = AppSettings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        log_dir=tmp_path / "logs",
        admin_password=None,
    )
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/admin/login",
            data={"password": "anything"},
            follow_redirects=False,
        )

    assert response.status_code == 503
    assert "未配置管理员密码" in response.text
