from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from nas_index.models import ScanRun
from nas_index.web.app import create_app


def test_startup_marks_orphaned_running_scan_interrupted(
    settings,
):
    app = create_app(settings)
    with Session(app.state.engine) as session:
        session.add(
            ScanRun(
                generation=1,
                status="running",
                started_at=datetime.now(UTC),
                finished_at=None,
                processed_entries=10,
                current_path="/Public",
                error_summary=None,
            )
        )
        session.commit()

    with TestClient(app):
        pass

    with Session(app.state.engine) as session:
        assert (
            session.query(ScanRun).one().status
            == "interrupted"
        )
