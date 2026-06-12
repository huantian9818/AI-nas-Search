from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from nas_index.models import ScanRun


@pytest.fixture
def running_scan(client):
    with Session(client.app.state.engine) as session:
        session.add(
            ScanRun(
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


def test_scan_start_requires_saved_configuration(client):
    response = client.post(
        "/scans",
        follow_redirects=False,
    )

    assert response.status_code == 409
    assert "请先保存 NAS 设置" in response.text


def test_scan_status_partial_polls_while_running(
    client,
    running_scan,
):
    response = client.get("/scans/status")

    assert response.status_code == 200
    assert 'hx-get="/scans/status"' in response.text
    assert "正在扫描" in response.text
