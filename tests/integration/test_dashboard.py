from datetime import UTC, datetime

from sqlalchemy.orm import Session

from nas_index.models import ScanRun


def test_dashboard_displays_counts(
    client,
    web_seeded_entries,
):
    response = client.get("/")

    assert response.status_code == 200
    assert "文件" in response.text
    assert "文件夹" in response.text


def test_dashboard_displays_last_successful_scan(client):
    finished = datetime(
        2026,
        6,
        12,
        8,
        30,
        tzinfo=UTC,
    )
    with Session(client.app.state.engine) as session:
        session.add(
            ScanRun(
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

    response = client.get("/")

    assert "最后成功扫描" in response.text
    assert "2026-06-12" in response.text
