import asyncio

import pytest

from nas_index.services.scan_manager import (
    ScanAlreadyRunning,
    ScanManager,
)


@pytest.mark.asyncio
async def test_manager_rejects_second_scan_while_worker_is_active():
    started = asyncio.Event()
    release = asyncio.Event()

    class SlowScanner:
        async def run(self):
            started.set()
            await release.wait()

    manager = ScanManager(lambda: SlowScanner())
    manager.start()
    await started.wait()

    with pytest.raises(ScanAlreadyRunning):
        manager.start()

    release.set()
    await manager.wait()
