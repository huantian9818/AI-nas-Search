from datetime import UTC, datetime

from sqlalchemy.orm import Session

from nas_index.models import SyncRun
from nas_index.repositories.nas import NasRepository


def _grant_access(client, nas_id: int) -> None:
    token = client.app.state.access_store.create(
        nas_id=nas_id,
        username="alice",
        share_paths=("/Public",),
    )
    client.cookies.set("nas_access", token)


def _create_nas(session: Session, name: str) -> int:
    return NasRepository(session).create_server(
        name=name,
        base_url=f"http://{name.lower()}.local",
        port=8080,
        use_https=False,
        enabled=True,
        sync_interval_minutes=30,
        username="indexer",
        password="secret",
    ).id


def test_dashboard_displays_counts(
    client,
    web_seeded_entries,
):
    response = client.get("/")

    assert response.status_code == 200
    assert "文件" in response.text
    assert "文件夹" in response.text


def test_dashboard_displays_per_nas_scan_controls(admin_client):
    with Session(admin_client.app.state.engine) as session:
        office_id = _create_nas(session, "Office")
        lab_id = _create_nas(session, "Lab")
        session.commit()
    _grant_access(admin_client, office_id)

    response = admin_client.get("/")

    assert response.status_code == 200
    assert f'name="nas_id" value="{office_id}"' in response.text
    assert f'name="nas_id" value="{lab_id}"' in response.text
    assert "同步 Office" in response.text
    assert "同步 Lab" in response.text
    assert f"/scans/status?nas_id={office_id}" in response.text
    assert f"/scans/status?nas_id={lab_id}" in response.text


def test_dashboard_displays_last_successful_sync(client):
    finished = datetime(
        2026,
        6,
        12,
        8,
        30,
        tzinfo=UTC,
    )
    with Session(client.app.state.engine) as session:
        nas_id = _create_nas(session, "Office")
        session.add(
            SyncRun(
                nas_id=nas_id,
                scope="nas",
                share_path=None,
                generation=1,
                status="succeeded",
                started_at=finished,
                finished_at=finished,
                processed_entries=42,
                current_path="/Public",
                error_summary=None,
            )
        )
        session.commit()
    _grant_access(client, nas_id)

    response = client.get("/")

    assert "最后成功同步" in response.text
    assert "2026-06-12" in response.text
