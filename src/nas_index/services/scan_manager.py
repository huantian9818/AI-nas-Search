import asyncio
from collections.abc import Callable
from typing import Any


class ScanAlreadyRunning(RuntimeError):
    pass


class ScanManager:
    def __init__(
        self,
        scanner_factory: Callable[[], Any],
    ):
        self.scanner_factory = scanner_factory
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        return (
            self._task is not None
            and not self._task.done()
        )

    def start(self) -> None:
        if self.is_running or self._lock.locked():
            raise ScanAlreadyRunning()
        self._task = asyncio.create_task(
            self._run()
        )

    async def _run(self) -> None:
        async with self._lock:
            await self.scanner_factory().run()

    async def wait(self) -> None:
        if self._task:
            await self._task
