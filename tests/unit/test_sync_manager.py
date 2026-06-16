import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from nas_index.repositories.nas import NasRepository
from nas_index.repositories.syncs import SyncRepository
from nas_index.services.sync_manager import (
    NasSyncAlreadyRunning,
    SyncManager,
)


class RecordingScanner:
    def __init__(
        self,
        calls: list[int],
        nas_id: int,
    ):
        self.calls = calls
        self.nas_id = nas_id

    async def run(self) -> int:
        self.calls.append(self.nas_id)
        await asyncio.sleep(0)
        return self.nas_id


class BlockingScanner:
    def __init__(
        self,
        calls: list[int],
        nas_id: int,
        release: asyncio.Event,
    ):
        self.calls = calls
        self.nas_id = nas_id
        self.release = release

    async def run(self) -> int:
        self.calls.append(self.nas_id)
        await self.release.wait()
        return self.nas_id


def _create_due_share(
    database,
    *,
    nas_name: str = "Office",
) -> int:
    with Session(database) as session:
        nas_id = NasRepository(session).create_server(
            name=nas_name,
            base_url=f"http://{nas_name.lower()}.local",
            port=8080,
            use_https=False,
            enabled=True,
            sync_interval_minutes=30,
            full_resync_interval_hours=24,
            username="indexer",
            password="secret",
        ).id
        SyncRepository(session).ensure_share_state(
            nas_id=nas_id,
            share_path="/Public",
            next_sync_at=(
                datetime.now(UTC) - timedelta(seconds=1)
            ),
        )
        session.commit()
        return nas_id


@pytest.mark.asyncio
async def test_sync_manager_allows_one_active_run_per_nas():
    calls: list[int] = []
    manager = SyncManager(
        scanner_factory=lambda nas_id: RecordingScanner(
            calls,
            nas_id,
        )
    )

    manager.start_nas(1)
    with pytest.raises(NasSyncAlreadyRunning):
        manager.start_nas(1)
    await manager.wait_all()

    assert calls == [1]


@pytest.mark.asyncio
async def test_sync_manager_runs_different_nas_ids():
    calls: list[int] = []
    manager = SyncManager(
        scanner_factory=lambda nas_id: RecordingScanner(
            calls,
            nas_id,
        )
    )

    manager.start_nas(1)
    manager.start_nas(2)
    await manager.wait_all()

    assert sorted(calls) == [1, 2]


@pytest.mark.asyncio
async def test_sync_manager_starts_due_nas_syncs(database):
    nas_id = _create_due_share(database)
    calls: list[int] = []
    manager = SyncManager(
        scanner_factory=lambda nas_id: RecordingScanner(
            calls,
            nas_id,
        ),
        session_factory=lambda: Session(database),
    )

    manager.start_due_syncs()
    await manager.wait_all()

    assert calls == [nas_id]


@pytest.mark.asyncio
async def test_sync_manager_skips_due_nas_that_is_already_running(
    database,
):
    nas_id = _create_due_share(database)
    calls: list[int] = []
    release = asyncio.Event()
    manager = SyncManager(
        scanner_factory=lambda nas_id: BlockingScanner(
            calls,
            nas_id,
            release,
        ),
        session_factory=lambda: Session(database),
    )

    manager.start_nas(nas_id)
    await asyncio.sleep(0)
    manager.start_due_syncs()
    release.set()
    await manager.wait_all()

    assert calls == [nas_id]
