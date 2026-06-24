from datetime import UTC, datetime

from sqlalchemy.orm import Session

from nas_index.repositories.entries import EntryRepository
from nas_index.repositories.nas import NasRepository
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


def _create_nas(session: Session, name: str) -> int:
    return NasRepository(session).create_server(
        name=name,
        base_url=f"http://{name.lower()}.local",
        port=8080,
        use_https=False,
        enabled=True,
        sync_interval_minutes=30,
        username="indexer",
        password="secret",
    ).id


def test_entries_with_same_path_do_not_collide_across_nas(database):
    with Session(database) as session:
        nas_one = _create_nas(session, "One")
        nas_two = _create_nas(session, "Two")
        repository = EntryRepository(session)
        shared_item = IndexedItem(
            name="report.txt",
            full_path="/Public/report.txt",
            parent_path="/Public",
            entry_type="file",
            size_bytes=10,
            modified_at=datetime(2026, 1, 1, tzinfo=UTC),
            share_path="/Public",
        )
        repository.upsert_batch(nas_one, [shared_item], generation=1)
        repository.upsert_batch(nas_two, [shared_item], generation=1)
        session.commit()

        one = repository.search(
            "report",
            nas_id=nas_one,
            allowed_share_paths=("/Public",),
            page=1,
            page_size=20,
        )
        two = repository.search(
            "report",
            nas_id=nas_two,
            allowed_share_paths=("/Public",),
            page=1,
            page_size=20,
        )

        assert one.total == 1
        assert two.total == 1
        assert one.items[0].nas_id == nas_one
        assert two.items[0].nas_id == nas_two


def test_share_filter_hides_entries_outside_allowed_roots(database):
    with Session(database) as session:
        nas_id = _create_nas(session, "Office")
        repository = EntryRepository(session)
        repository.upsert_batch(
            nas_id,
            [
                IndexedItem(
                    "public.txt",
                    "/Public/public.txt",
                    "/Public",
                    "file",
                    1,
                    None,
                    "/Public",
                ),
                IndexedItem(
                    "secret.txt",
                    "/Secret/secret.txt",
                    "/Secret",
                    "file",
                    1,
                    None,
                    "/Secret",
                ),
            ],
            generation=1,
        )
        session.commit()

        page = repository.search(
            "txt",
            nas_id=nas_id,
            allowed_share_paths=("/Public",),
            page=1,
            page_size=20,
        )

        assert [item.full_path for item in page.items] == [
            "/Public/public.txt"
        ]


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
