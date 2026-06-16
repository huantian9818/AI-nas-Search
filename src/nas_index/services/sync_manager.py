import asyncio
from collections.abc import Callable
from typing import Any


class NasSyncAlreadyRunning(RuntimeError):
    pass


class SyncManager:
    def __init__(
        self,
        scanner_factory: Callable[[int], Any],
    ):
        self.scanner_factory = scanner_factory
        self._tasks: dict[int, asyncio.Task] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    def is_running(
        self,
        nas_id: int,
    ) -> bool:
        task = self._tasks.get(nas_id)
        return task is not None and not task.done()

    def start_nas(
        self,
        nas_id: int,
    ) -> None:
        lock = self._locks.setdefault(
            nas_id,
            asyncio.Lock(),
        )
        if self.is_running(nas_id) or lock.locked():
            raise NasSyncAlreadyRunning()
        self._tasks[nas_id] = asyncio.create_task(
            self._run_nas(nas_id, lock)
        )

    async def _run_nas(
        self,
        nas_id: int,
        lock: asyncio.Lock,
    ) -> None:
        async with lock:
            await self.scanner_factory(nas_id).run()

    async def wait_all(self) -> None:
        tasks = [
            task for task in self._tasks.values() if not task.done()
        ]
        if tasks:
            await asyncio.gather(*tasks)
