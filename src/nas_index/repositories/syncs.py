from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from nas_index.models import ShareSyncState, SyncError, SyncRun
from nas_index.time import now_beijing


class SyncRepository:
    def __init__(self, session: Session):
        self.session = session

    def create_run(
        self,
        *,
        nas_id: int,
        scope: str,
        share_path: str | None,
    ) -> SyncRun:
        generation = (
            self.session.scalar(
                select(func.max(SyncRun.generation)).where(
                    SyncRun.nas_id == nas_id
                )
            )
            or 0
        ) + 1
        run = SyncRun(
            nas_id=nas_id,
            scope=scope,
            share_path=share_path,
            generation=generation,
            status="running",
            started_at=now_beijing(),
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
        run = self._require_run(run_id)
        run.processed_entries = processed
        run.current_path = current_path

    def succeed(
        self,
        run_id: int,
        processed: int,
    ) -> None:
        run = self._require_run(run_id)
        run.status = "succeeded"
        run.processed_entries = processed
        run.finished_at = now_beijing()

    def fail(
        self,
        run_id: int,
        path: str,
        reason: str,
        processed: int,
    ) -> None:
        run = self._require_run(run_id)
        run.status = "failed"
        run.processed_entries = processed
        run.current_path = path
        run.error_summary = reason
        run.finished_at = now_beijing()
        self.session.add(
            SyncError(
                sync_run_id=run_id,
                path=path,
                reason=reason,
                created_at=now_beijing(),
            )
        )

    def ensure_share_state(
        self,
        *,
        nas_id: int,
        share_path: str,
        next_sync_at: datetime,
    ) -> ShareSyncState:
        state = self.get_share_state(nas_id, share_path)
        if state is None:
            state = ShareSyncState(
                nas_id=nas_id,
                share_path=share_path,
                last_synced_at=None,
                last_full_synced_at=None,
                next_sync_at=next_sync_at,
                last_generation=0,
                status="pending",
                last_error=None,
            )
            self.session.add(state)
            self.session.flush()
        return state

    def mark_share_running(
        self,
        nas_id: int,
        share_path: str,
    ) -> None:
        state = self.get_share_state(nas_id, share_path)
        if state is None:
            raise LookupError("share sync state not found")
        state.status = "running"
        state.last_error = None

    def mark_share_succeeded(
        self,
        *,
        nas_id: int,
        share_path: str,
        generation: int,
        next_sync_at: datetime,
        full: bool,
    ) -> None:
        state = self.get_share_state(nas_id, share_path)
        if state is None:
            raise LookupError("share sync state not found")
        now = now_beijing()
        state.status = "succeeded"
        state.last_synced_at = now
        if full:
            state.last_full_synced_at = now
        state.next_sync_at = next_sync_at
        state.last_generation = generation
        state.last_error = None

    def mark_share_failed(
        self,
        *,
        nas_id: int,
        share_path: str,
        error: str,
        next_sync_at: datetime,
    ) -> None:
        state = self.get_share_state(nas_id, share_path)
        if state is None:
            raise LookupError("share sync state not found")
        state.status = "failed"
        state.last_error = error
        state.next_sync_at = next_sync_at

    def mark_nas_failed(
        self,
        *,
        nas_id: int,
        error: str,
        next_sync_at: datetime,
    ) -> int:
        result = self.session.execute(
            update(ShareSyncState)
            .where(ShareSyncState.nas_id == nas_id)
            .values(
                status="failed",
                last_error=error,
                next_sync_at=next_sync_at,
            )
        )
        return result.rowcount or 0

    def get_share_state(
        self,
        nas_id: int,
        share_path: str,
    ) -> ShareSyncState | None:
        return self.session.get(
            ShareSyncState,
            (nas_id, share_path),
        )

    def due_share_states(
        self,
        now: datetime,
    ) -> list[ShareSyncState]:
        return list(
            self.session.scalars(
                select(ShareSyncState)
                .where(
                    ShareSyncState.next_sync_at <= now,
                    ShareSyncState.status != "running",
                )
                .order_by(ShareSyncState.next_sync_at)
            )
        )

    def latest_for_nas(
        self,
        nas_id: int,
    ) -> SyncRun | None:
        return self.session.scalar(
            select(SyncRun)
            .where(SyncRun.nas_id == nas_id)
            .order_by(SyncRun.id.desc())
            .limit(1)
        )

    def latest(self) -> SyncRun | None:
        return self.session.scalar(
            select(SyncRun)
            .order_by(SyncRun.id.desc())
            .limit(1)
        )

    def last_successful(self) -> SyncRun | None:
        return self.session.scalar(
            select(SyncRun)
            .where(SyncRun.status == "succeeded")
            .order_by(SyncRun.finished_at.desc())
            .limit(1)
        )

    def interrupt_running(self) -> int:
        result = self.session.execute(
            update(SyncRun)
            .where(SyncRun.status == "running")
            .values(
                status="interrupted",
                finished_at=now_beijing(),
            )
        )
        self.session.execute(
            update(ShareSyncState)
            .where(ShareSyncState.status == "running")
            .values(
                status="failed",
                last_error="同步任务被中断",
            )
        )
        return result.rowcount or 0

    def _require_run(
        self,
        run_id: int,
    ) -> SyncRun:
        run = self.session.get(SyncRun, run_id)
        if run is None:
            raise LookupError("sync run not found")
        return run
