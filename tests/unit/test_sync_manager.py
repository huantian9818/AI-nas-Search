import asyncio

import pytest

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
