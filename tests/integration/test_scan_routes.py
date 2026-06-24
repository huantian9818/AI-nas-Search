from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from nas_index.models import SyncRun
from nas_index.repositories.nas import NasRepository


@pytest.fixture
def running_scan(client):
    with Session(client.app.state.engine) as session:
        nas_id = NasRepository(session).create_server(
            name="Test NAS",
            base_url="http://nas.local",
            port=8080,
            use_https=False,
            enabled=True,
            sync_interval_minutes=30,
            username="indexer",
            password="secret",
        ).id
        session.add(
            SyncRun(
                nas_id=nas_id,
                scope="nas",
                share_path=None,
                generation=1,
                status="running",
                started_at=datetime.now(UTC),
                finished_at=None,
                processed_entries=12,
                current_path="/Public",
                error_summary=None,
            )
        )
        session.commit()
        return nas_id


def test_scan_start_requires_saved_configuration(admin_client):
    response = admin_client.post(
        "/scans",
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert "请先保存 NAS 设置" in response.text


def test_scan_status_partial_polls_while_running(
    client,
    running_scan,
):
    response = client.get(
        "/scans/status",
        params={"nas_id": running_scan},
    )

    assert response.status_code == 200
    assert (
        f'hx-get="/scans/status?nas_id={running_scan}"'
        in response.text
    )
    assert "正在扫描" in response.text


def test_scan_status_partial_shows_speed_and_process_usage(
    client,
    running_scan,
):
    with Session(client.app.state.engine) as session:
        scan = session.query(SyncRun).one()
        scan.started_at = datetime.now(UTC) - timedelta(
            seconds=10
        )
        scan.processed_entries = 120
        session.commit()

    response = client.get(
        "/scans/status",
        params={"nas_id": running_scan},
    )

    assert response.status_code == 200
    assert "耗时：" in response.text
    assert "速度：" in response.text
    assert "条/秒" in response.text
    assert "内存：" in response.text
    assert "CPU：" in response.text
