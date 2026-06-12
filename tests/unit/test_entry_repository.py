from datetime import UTC, datetime

from sqlalchemy.orm import Session

from nas_index.repositories.entries import EntryRepository
from nas_index.types import IndexedItem


def item(
    name,
    path,
    parent,
    kind="file",
    size=1,
):
    return IndexedItem(
        name,
        path,
        parent,
        kind,
        size,
        datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_upsert_updates_metadata_and_generation(database):
    with Session(database) as session:
        repository = EntryRepository(session)
        repository.upsert_batch(
            [
                item(
                    "a.txt",
                    "/Public/a.txt",
                    "/Public",
                    size=1,
                )
            ],
            1,
        )
        session.commit()
        repository.upsert_batch(
            [
                item(
                    "a.txt",
                    "/Public/a.txt",
                    "/Public",
                    size=9,
                )
            ],
            2,
        )
        session.commit()
        saved = repository.get_by_path("/Public/a.txt")

    assert saved.size_bytes == 9
    assert saved.scan_generation == 2


def test_list_children_sorts_directories_first_and_paginates(database):
    with Session(database) as session:
        repository = EntryRepository(session)
        repository.upsert_batch(
            [
                item("z.txt", "/Public/z.txt", "/Public"),
                item(
                    "docs",
                    "/Public/docs",
                    "/Public",
                    "directory",
                    None,
                ),
                item("a.txt", "/Public/a.txt", "/Public"),
            ],
            1,
        )
        session.commit()
        page = repository.list_children(
            "/Public",
            page=1,
            page_size=2,
        )

    assert [entry.name for entry in page.items] == [
        "docs",
        "a.txt",
    ]
    assert page.total == 3


def test_delete_stale_only_removes_older_generation(database):
    with Session(database) as session:
        repository = EntryRepository(session)
        repository.upsert_batch(
            [
                item("old", "/Public/old", "/Public"),
                item("new", "/Public/new", "/Public"),
            ],
            1,
        )
        session.commit()
        repository.upsert_batch(
            [item("new", "/Public/new", "/Public")],
            2,
        )
        removed = repository.delete_stale(2)
        session.commit()

    assert removed == 1


def test_page_for_entry_locates_selected_row(database):
    with Session(database) as session:
        repository = EntryRepository(session)
        repository.upsert_batch(
            [
                item(
                    f"{number:03}.txt",
                    f"/Public/{number:03}.txt",
                    "/Public",
                )
                for number in range(125)
            ],
            1,
        )
        session.commit()
        selected = repository.get_by_path("/Public/120.txt")

        assert (
            repository.page_for_entry(
                selected.id,
                page_size=50,
            )
            == 3
        )
