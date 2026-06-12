from collections import deque
from collections.abc import Callable
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from nas_index.qnap.errors import QnapError
from nas_index.repositories.entries import EntryRepository
from nas_index.repositories.scans import ScanRepository
from nas_index.types import IndexedItem


class Scanner:
    def __init__(
        self,
        engine: Engine,
        client_factory: Callable[[], Any],
        page_size: int,
        batch_size: int,
    ):
        self.engine = engine
        self.client_factory = client_factory
        self.page_size = page_size
        self.batch_size = batch_size

    async def run(self) -> int:
        with Session(self.engine) as session:
            run = ScanRepository(session).create()
            run_id = run.id
            generation = run.generation
            session.commit()

        processed = 0
        current_path = "/"
        batch: list[IndexedItem] = []
        try:
            async with self.client_factory() as client:
                shares = await client.list_shares()
                self._write_batch(
                    shares,
                    generation,
                )
                processed += len(shares)
                queue = deque(
                    item.full_path
                    for item in shares
                )

                while queue:
                    current_path = queue.popleft()
                    async for item in client.iter_children(
                        current_path,
                        page_size=self.page_size,
                    ):
                        batch.append(item)
                        processed += 1
                        if item.entry_type == "directory":
                            queue.append(
                                item.full_path
                            )
                        if len(batch) >= self.batch_size:
                            self._write_batch(
                                batch,
                                generation,
                            )
                            batch.clear()
                            self._progress(
                                run_id,
                                processed,
                                current_path,
                            )

                self._write_batch(
                    batch,
                    generation,
                )

            with Session(self.engine) as session:
                EntryRepository(
                    session
                ).delete_stale(generation)
                ScanRepository(session).succeed(
                    run_id,
                    processed,
                )
                session.commit()
            return run_id
        except Exception as exc:
            reason = (
                str(exc)
                if isinstance(exc, QnapError)
                else "扫描任务异常中断"
            )
            with Session(self.engine) as session:
                ScanRepository(session).fail(
                    run_id,
                    current_path,
                    reason,
                    processed,
                )
                session.commit()
            return run_id

    def _write_batch(
        self,
        batch: list[IndexedItem],
        generation: int,
    ) -> None:
        if not batch:
            return
        with Session(self.engine) as session:
            EntryRepository(session).upsert_batch(
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
            ScanRepository(session).progress(
                run_id,
                processed,
                path,
            )
            session.commit()
