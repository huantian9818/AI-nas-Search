import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from nas_index.repositories.syncs import SyncRepository


class NasSyncAlreadyRunning(RuntimeError):
    pass


class SyncManager:
    def __init__(
        self,
        scanner_factory: Callable[[int], Any],
        *,
        session_factory: Callable[[], Session] | None = None,
        poll_seconds: float = 10.0,
    ):
        self.scanner_factory = scanner_factory
        self.session_factory = session_factory
        self.poll_seconds = poll_seconds
        self._tasks: dict[int, asyncio.Task] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._scheduler_task: asyncio.Task | None = None

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

    def start_scheduler(self) -> None:
        if self.session_factory is None:
            raise RuntimeError(
                "session_factory is required for scheduling"
            )
        if (
            self._scheduler_task is None
            or self._scheduler_task.done()
        ):
            self._scheduler_task = asyncio.create_task(
                self._scheduler_loop()
            )

    async def stop_scheduler(self) -> None:
        if self._scheduler_task is None:
            return
        self._scheduler_task.cancel()
        await asyncio.gather(
            self._scheduler_task,
            return_exceptions=True,
        )
        self._scheduler_task = None

    async def _scheduler_loop(self) -> None:
        while True:
            self.start_due_syncs()
            await asyncio.sleep(self.poll_seconds)

    def start_due_syncs(self) -> None:
        if self.session_factory is None:
            return
        with self.session_factory() as session:
            due = SyncRepository(session).due_share_states(
                datetime.now(UTC)
            )
        for state in due:
            if not self.is_running(state.nas_id):
                self.start_nas(state.nas_id)

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
