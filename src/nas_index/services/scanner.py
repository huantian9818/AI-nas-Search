import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import logging
from pathlib import PurePosixPath
from time import monotonic
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from nas_index.qnap.errors import QnapError
from nas_index.repositories.entries import DEFAULT_NAS_ID
from nas_index.repositories.entries import EntryRepository
from nas_index.repositories.syncs import SyncRepository
from nas_index.types import IndexedItem


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class DirectoryItems:
    path: str
    items: list[IndexedItem]


@dataclass(frozen=True)
class DirectoryFailure:
    path: str
    error: Exception


class Scanner:
    def __init__(
        self,
        engine: Engine,
        client_factory: Callable[[], Any],
        page_size: int,
        batch_size: int,
        *,
        nas_id: int = DEFAULT_NAS_ID,
        concurrency: int = 1,
        progress_interval_seconds: float = 0.0,
        skip_recycle: bool = False,
    ):
        self.engine = engine
        self.nas_id = nas_id
        self.client_factory = client_factory
        self.page_size = page_size
        self.batch_size = batch_size
        self.concurrency = max(1, concurrency)
        self.progress_interval_seconds = max(
            0.0,
            progress_interval_seconds,
        )
        self.skip_recycle = skip_recycle

    async def run(self) -> int:
        with Session(self.engine) as session:
            run = SyncRepository(session).create_run(
                nas_id=self.nas_id,
                scope="nas",
                share_path=None,
            )
            run_id = run.id
            generation = run.generation
            session.commit()

        processed = 0
        current_path = "/"
        LOGGER.info(
            "Starting scan run_id=%s generation=%s "
            "concurrency=%s batch_size=%s skip_recycle=%s",
            run_id,
            generation,
            self.concurrency,
            self.batch_size,
            self.skip_recycle,
        )
        try:
            async with self.client_factory() as client:
                shares = [
                    item
                    for item in await client.list_shares()
                    if not self._should_skip(item)
                ]
                self._write_batch(
                    shares,
                    generation,
                )
                processed += len(shares)

                processed, current_path = (
                    await self._scan_directories(
                        client=client,
                        run_id=run_id,
                        generation=generation,
                        processed=processed,
                        current_path=current_path,
                        initial_directories=[
                            item.full_path
                            for item in shares
                        ],
                    )
                )

            with Session(self.engine) as session:
                EntryRepository(
                    session
                ).delete_stale(self.nas_id, generation)
                SyncRepository(session).succeed(
                    run_id,
                    processed,
                )
                session.commit()
            LOGGER.info(
                "Completed scan run_id=%s processed=%s",
                run_id,
                processed,
            )
            return run_id
        except Exception as exc:
            reason = (
                str(exc)
                if isinstance(exc, QnapError)
                else "扫描任务异常中断"
            )
            if isinstance(exc, QnapError):
                LOGGER.warning(
                    "Scan failed run_id=%s path=%s "
                    "processed=%s reason=%s",
                    run_id,
                    current_path,
                    processed,
                    reason,
                )
            else:
                LOGGER.exception(
                    "Scan crashed run_id=%s path=%s "
                    "processed=%s",
                    run_id,
                    current_path,
                    processed,
                )
            with Session(self.engine) as session:
                SyncRepository(session).fail(
                    run_id,
                    current_path,
                    reason,
                    processed,
                )
                session.commit()
            return run_id

    async def _scan_directories(
        self,
        *,
        client: Any,
        run_id: int,
        generation: int,
        processed: int,
        current_path: str,
        initial_directories: list[str],
    ) -> tuple[int, str]:
        if not initial_directories:
            self._progress(
                run_id,
                processed,
                current_path,
            )
            return processed, current_path

        path_queue: asyncio.Queue[str | None] = (
            asyncio.Queue()
        )
        result_queue: asyncio.Queue[
            DirectoryItems | DirectoryFailure
        ] = asyncio.Queue()
        pending_directories = 0
        batch: list[IndexedItem] = []

        for path in initial_directories:
            path_queue.put_nowait(path)
            pending_directories += 1

        workers = [
            asyncio.create_task(
                self._directory_worker(
                    client,
                    path_queue,
                    result_queue,
                )
            )
            for _ in range(self.concurrency)
        ]
        next_progress_at = self._next_progress_deadline()
        cancel_workers = True
        try:
            while pending_directories:
                result = await result_queue.get()
                current_path = result.path
                pending_directories -= 1
                if isinstance(result, DirectoryFailure):
                    raise result.error

                if result.items:
                    batch.extend(result.items)
                    processed += len(result.items)
                    for item in result.items:
                        if item.entry_type == "directory":
                            path_queue.put_nowait(
                                item.full_path
                            )
                            pending_directories += 1

                if len(batch) >= self.batch_size:
                    self._write_batch(
                        batch,
                        generation,
                    )
                    batch.clear()

                if self._should_record_progress(
                    next_progress_at,
                    pending_directories,
                ):
                    self._progress(
                        run_id,
                        processed,
                        current_path,
                    )
                    next_progress_at = (
                        self._next_progress_deadline()
                    )

            if batch:
                self._write_batch(
                    batch,
                    generation,
                )

            self._progress(
                run_id,
                processed,
                current_path,
            )
            cancel_workers = False
            return processed, current_path
        finally:
            if cancel_workers:
                for worker in workers:
                    worker.cancel()
            else:
                for _ in workers:
                    path_queue.put_nowait(None)
            await asyncio.gather(
                *workers,
                return_exceptions=True,
            )

    async def _directory_worker(
        self,
        client: Any,
        path_queue: asyncio.Queue[str | None],
        result_queue: asyncio.Queue[
            DirectoryItems | DirectoryFailure
        ],
    ) -> None:
        while True:
            path = await path_queue.get()
            try:
                if path is None:
                    return
                items: list[IndexedItem] = []
                async for item in client.iter_children(
                    path,
                    page_size=self.page_size,
                ):
                    if self._should_skip(item):
                        continue
                    items.append(item)
                await result_queue.put(
                    DirectoryItems(path, items)
                )
            except Exception as exc:
                await result_queue.put(
                    DirectoryFailure(path, exc)
                )
            finally:
                path_queue.task_done()

    def _write_batch(
        self,
        batch: list[IndexedItem],
        generation: int,
    ) -> None:
        if not batch:
            return
        with Session(self.engine) as session:
            EntryRepository(session).upsert_batch(
                self.nas_id,
                batch,
                generation,
            )
            session.commit()

    def _progress(
        self,
        run_id: int,
        processed: int,
        path: str,
    ) -> None:
        with Session(self.engine) as session:
            SyncRepository(session).progress(
                run_id,
                processed,
                path,
            )
            session.commit()

    def _should_skip(
        self,
        item: IndexedItem,
    ) -> bool:
        if not self.skip_recycle:
            return False
        return (
            "@Recycle"
            in PurePosixPath(item.full_path).parts
        )

    def _next_progress_deadline(self) -> float:
        if self.progress_interval_seconds <= 0:
            return 0.0
        return monotonic() + self.progress_interval_seconds

    def _should_record_progress(
        self,
        next_progress_at: float,
        pending_directories: int,
    ) -> bool:
        if self.progress_interval_seconds <= 0:
            return True
        return (
            pending_directories == 0
            or monotonic() >= next_progress_at
        )
