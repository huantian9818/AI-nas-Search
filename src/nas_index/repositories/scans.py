from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from nas_index.models import ScanError, ScanRun


class ScanRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(self) -> ScanRun:
        generation = (
            self.session.scalar(
                select(
                    func.max(ScanRun.generation)
                )
            )
            or 0
        ) + 1
        run = ScanRun(
            generation=generation,
            status="running",
            started_at=datetime.now(UTC),
            finished_at=None,
            processed_entries=0,
            current_path=None,
            error_summary=None,
        )
        self.session.add(run)
        self.session.flush()
        return run

    def progress(
        self,
        run_id: int,
        processed: int,
        current_path: str,
    ) -> None:
        run = self.session.get(ScanRun, run_id)
        if run is None:
            raise LookupError("scan run not found")
        run.processed_entries = processed
        run.current_path = current_path

    def succeed(
        self,
        run_id: int,
        processed: int,
    ) -> None:
        run = self.session.get(ScanRun, run_id)
        if run is None:
            raise LookupError("scan run not found")
        run.status = "succeeded"
        run.processed_entries = processed
        run.finished_at = datetime.now(UTC)

    def fail(
        self,
        run_id: int,
        path: str,
        reason: str,
        processed: int,
    ) -> None:
        run = self.session.get(ScanRun, run_id)
        if run is None:
            raise LookupError("scan run not found")
        run.status = "failed"
        run.processed_entries = processed
        run.current_path = path
        run.error_summary = reason
        run.finished_at = datetime.now(UTC)
        self.session.add(
            ScanError(
                scan_run_id=run_id,
                path=path,
                reason=reason,
                created_at=datetime.now(UTC),
            )
        )

    def latest(self) -> ScanRun | None:
        return self.session.scalar(
            select(ScanRun)
            .order_by(ScanRun.id.desc())
            .limit(1)
        )

    def last_successful(self) -> ScanRun | None:
        return self.session.scalar(
            select(ScanRun)
            .where(
                ScanRun.status == "succeeded"
            )
            .order_by(
                ScanRun.finished_at.desc()
            )
            .limit(1)
        )

    def interrupt_running(self) -> int:
        result = self.session.execute(
            update(ScanRun)
            .where(
                ScanRun.status == "running"
            )
            .values(
                status="interrupted",
                finished_at=datetime.now(UTC),
            )
        )
        return result.rowcount or 0
