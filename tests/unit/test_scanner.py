import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nas_index.models import Entry, SyncRun
from nas_index.qnap.errors import (
    QnapPermissionError,
    QnapProtocolError,
)
from nas_index.repositories.nas import NasRepository
from nas_index.repositories.entries import EntryRepository
from nas_index.repositories.syncs import SyncRepository
from nas_index.services.scanner import Scanner
from nas_index.types import IndexedItem


def directory(name, path, parent):
    return IndexedItem(
        name,
        path,
        parent,
        "directory",
        None,
        datetime.now(UTC),
    )


def file(name, path, parent):
    return IndexedItem(
        name,
        path,
        parent,
        "file",
        4,
        datetime.now(UTC),
    )


def create_nas(session: Session) -> int:
    nas_id = NasRepository(session).create_server(
        name="Test NAS",
        base_url="http://nas.local",
        port=8080,
        use_https=False,
        enabled=True,
        sync_interval_minutes=30,
        full_resync_interval_hours=24,
        username="indexer",
        password="secret",
    ).id
    session.commit()
    return nas_id


class FakeQnap:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def list_shares(self):
        return [
            directory(
                "Public",
                "/Public",
                "/",
            )
        ]

    async def iter_children(self, path, *, page_size):
        rows = {
            "/Public": [
                directory(
                    "docs",
                    "/Public/docs",
                    "/Public",
                )
            ],
            "/Public/docs": [
                file(
                    "a.txt",
                    "/Public/docs/a.txt",
                    "/Public/docs",
                )
            ],
        }[path]
        for row in rows:
            yield row


@pytest.mark.asyncio
async def test_failed_scan_delays_due_share_retry(database):
    class FailingQnap:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def list_shares(self):
            raise QnapPermissionError()

    with Session(database) as session:
        nas_id = create_nas(session)
        SyncRepository(session).ensure_share_state(
            nas_id=nas_id,
            share_path="/Public",
            next_sync_at=datetime.now(UTC)
            - timedelta(minutes=1),
        )
        session.commit()

    before = datetime.now(UTC).replace(tzinfo=None)

    await Scanner(
        database,
        lambda: FailingQnap(),
        page_size=100,
        batch_size=2,
        nas_id=nas_id,
    ).run()

    with Session(database) as session:
        state = SyncRepository(session).get_share_state(
            nas_id,
            "/Public",
        )
        scan = session.scalar(
            select(SyncRun).order_by(SyncRun.id.desc())
        )

    assert scan.status == "failed"
    assert state.status == "failed"
    assert state.next_sync_at >= before + timedelta(minutes=29)
    assert state.last_error == "NAS 账号没有读取该目录的权限"


@pytest.mark.asyncio
async def test_successful_scan_schedules_next_share_sync(database):
    with Session(database) as session:
        nas_id = create_nas(session)
    before = datetime.now(UTC).replace(tzinfo=None)

    await Scanner(
        database,
        lambda: FakeQnap(),
        page_size=100,
        batch_size=2,
        nas_id=nas_id,
    ).run()

    with Session(database) as session:
        state = SyncRepository(session).get_share_state(
            nas_id,
            "/Public",
        )
        scan = session.scalar(
            select(SyncRun).order_by(
                SyncRun.id.desc()
            )
        )

    assert state is not None
    assert scan is not None
    assert state.status == "succeeded"
    assert state.last_generation == scan.generation
    assert state.next_sync_at >= before + timedelta(minutes=29)


@pytest.mark.asyncio
async def test_successful_directory_sync_deletes_missing_direct_children(
    database,
):
    with Session(database) as session:
        nas_id = create_nas(session)
        EntryRepository(session).upsert_batch(
            nas_id,
            [
                IndexedItem(
                    "old.txt",
                    "/Public/old.txt",
                    "/Public",
                    "file",
                    1,
                    None,
                    "/Public",
                ),
            ],
            generation=1,
        )
        session.commit()

    class ChangedQnap(FakeQnap):
        async def iter_children(self, path, *, page_size):
            rows = {
                "/Public": [
                    IndexedItem(
                        "new.txt",
                        "/Public/new.txt",
                        "/Public",
                        "file",
                        1,
                        None,
                        "/Public",
                    ),
                ],
            }[path]
            for row in rows:
                yield row

    await Scanner(
        database,
        lambda: ChangedQnap(),
        page_size=100,
        batch_size=100,
        nas_id=nas_id,
    ).run()

    with Session(database) as session:
        paths = [
            entry.full_path
            for entry in EntryRepository(session)
            .list_children(
                nas_id,
                "/Public",
                allowed_share_paths=("/Public",),
                page=1,
                page_size=100,
            )
            .items
        ]

    assert paths == ["/Public/new.txt"]


@pytest.mark.asyncio
async def test_successful_scan_indexes_tree_and_deletes_stale(database):
    now = datetime.now(UTC)
    with Session(database) as session:
        nas_id = create_nas(session)
        session.add(
            Entry(
                nas_id=nas_id,
                name="stale.txt",
                full_path="/Public/stale.txt",
                parent_path="/Public",
                entry_type="file",
                size_bytes=1,
                modified_at=None,
                scan_generation=0,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()

    await Scanner(
        database,
        lambda: FakeQnap(),
        page_size=100,
        batch_size=2,
        nas_id=nas_id,
    ).run()

    with Session(database) as session:
        paths = set(
            session.scalars(
                select(Entry.full_path)
            )
        )
        scan = session.scalar(
            select(SyncRun).order_by(
                SyncRun.id.desc()
            )
        )

    assert paths == {
        "/Public",
        "/Public/docs",
        "/Public/docs/a.txt",
    }
    assert scan.status == "succeeded"
    assert scan.processed_entries == 3


@pytest.mark.asyncio
async def test_failed_directory_preserves_stale_rows(database):
    class FailingQnap(FakeQnap):
        async def iter_children(self, path, *, page_size):
            if path == "/Public":
                raise QnapPermissionError()
            yield

    now = datetime.now(UTC)
    with Session(database) as session:
        nas_id = create_nas(session)
        session.add(
            Entry(
                nas_id=nas_id,
                name="old.txt",
                full_path="/Public/old.txt",
                parent_path="/Public",
                entry_type="file",
                size_bytes=1,
                modified_at=None,
                scan_generation=0,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()

    await Scanner(
        database,
        lambda: FailingQnap(),
        page_size=100,
        batch_size=100,
        nas_id=nas_id,
    ).run()

    with Session(database) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(Entry)
            )
            == 2
        )
        scan = session.scalar(
            select(SyncRun).order_by(
                SyncRun.id.desc()
            )
        )
        assert scan.status == "failed"


@pytest.mark.asyncio
async def test_failed_directory_records_unknown_status_message(database):
    class FailingQnap(FakeQnap):
        async def iter_children(self, path, *, page_size):
            if path == "/Public":
                raise QnapProtocolError(status=5)
            yield

    with Session(database) as session:
        nas_id = create_nas(session)

    await Scanner(
        database,
        lambda: FailingQnap(),
        page_size=100,
        batch_size=100,
        nas_id=nas_id,
    ).run()

    with Session(database) as session:
        scan = session.scalar(
            select(SyncRun).order_by(
                SyncRun.id.desc()
            )
        )

    assert scan.status == "failed"
    assert scan.error_summary == "NAS 返回了未识别的状态码 5"


@pytest.mark.asyncio
async def test_failed_scan_keeps_latest_recorded_progress(database):
    with Session(database) as session:
        nas_id = create_nas(session)

    class FailingAfterFirstDirectoryScanner(Scanner):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.replace_calls = 0

        def _replace_children(
            self,
            parent_path,
            observed_full_paths,
        ):
            self.replace_calls += 1
            if self.replace_calls > 1:
                raise RuntimeError("database write failed")
            super()._replace_children(
                parent_path,
                observed_full_paths,
            )

    await FailingAfterFirstDirectoryScanner(
        database,
        lambda: FakeQnap(),
        page_size=100,
        batch_size=2,
        nas_id=nas_id,
        progress_interval_seconds=0,
    ).run()

    with Session(database) as session:
        scan = session.scalar(
            select(SyncRun).order_by(
                SyncRun.id.desc()
            )
        )

    assert scan.status == "failed"
    assert scan.processed_entries == 2


@pytest.mark.asyncio
async def test_scan_skips_recycle_directory(database):
    class RecycleQnap(FakeQnap):
        def __init__(self):
            self.visited_paths: list[str] = []

        async def iter_children(self, path, *, page_size):
            self.visited_paths.append(path)
            rows = {
                "/Public": [
                    directory(
                        "@Recycle",
                        "/Public/@Recycle",
                        "/Public",
                    ),
                    directory(
                        "docs",
                        "/Public/docs",
                        "/Public",
                    ),
                ],
                "/Public/docs": [
                    file(
                        "a.txt",
                        "/Public/docs/a.txt",
                        "/Public/docs",
                    )
                ],
            }[path]
            for row in rows:
                yield row

    qnap = RecycleQnap()
    with Session(database) as session:
        nas_id = create_nas(session)

    await Scanner(
        database,
        lambda: qnap,
        page_size=100,
        batch_size=2,
        skip_recycle=True,
        nas_id=nas_id,
    ).run()

    with Session(database) as session:
        paths = set(
            session.scalars(
                select(Entry.full_path)
            )
        )

    assert paths == {
        "/Public",
        "/Public/docs",
        "/Public/docs/a.txt",
    }
    assert qnap.visited_paths == [
        "/Public",
        "/Public/docs",
    ]


@pytest.mark.asyncio
async def test_scan_fetches_directories_concurrently(database):
    class ConcurrentQnap(FakeQnap):
        def __init__(self):
            self.in_flight = 0
            self.max_in_flight = 0

        async def iter_children(self, path, *, page_size):
            self.in_flight += 1
            self.max_in_flight = max(
                self.max_in_flight,
                self.in_flight,
            )
            try:
                if path == "/Public":
                    rows = [
                        directory(
                            "alpha",
                            "/Public/alpha",
                            "/Public",
                        ),
                        directory(
                            "beta",
                            "/Public/beta",
                            "/Public",
                        ),
                    ]
                elif path == "/Public/alpha":
                    await asyncio.sleep(0.02)
                    rows = [
                        file(
                            "a.txt",
                            "/Public/alpha/a.txt",
                            "/Public/alpha",
                        )
                    ]
                else:
                    await asyncio.sleep(0.02)
                    rows = [
                        file(
                            "b.txt",
                            "/Public/beta/b.txt",
                            "/Public/beta",
                        )
                    ]
                for row in rows:
                    yield row
            finally:
                self.in_flight -= 1

    qnap = ConcurrentQnap()
    with Session(database) as session:
        nas_id = create_nas(session)

    await Scanner(
        database,
        lambda: qnap,
        page_size=100,
        batch_size=10,
        concurrency=2,
        nas_id=nas_id,
    ).run()

    assert qnap.max_in_flight >= 2
