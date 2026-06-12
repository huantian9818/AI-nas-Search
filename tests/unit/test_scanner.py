from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nas_index.models import Entry, ScanRun
from nas_index.qnap.errors import QnapPermissionError
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
async def test_successful_scan_indexes_tree_and_deletes_stale(database):
    now = datetime.now(UTC)
    with Session(database) as session:
        session.add(
            Entry(
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
    ).run()

    with Session(database) as session:
        paths = set(
            session.scalars(
                select(Entry.full_path)
            )
        )
        scan = session.scalar(
            select(ScanRun).order_by(
                ScanRun.id.desc()
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
        session.add(
            Entry(
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
            select(ScanRun).order_by(
                ScanRun.id.desc()
            )
        )
        assert scan.status == "failed"
