from datetime import UTC, datetime

from sqlalchemy.orm import Session

from nas_index.repositories.entries import EntryRepository
from nas_index.types import IndexedItem


def seed_entries(database):
    with Session(database) as session:
        EntryRepository(session).upsert_batch(
            [
                IndexedItem(
                    "年度项目计划.docx",
                    "/Public/年度项目计划.docx",
                    "/Public",
                    "file",
                    128,
                    datetime(2026, 1, 1, tzinfo=UTC),
                ),
                IndexedItem(
                    "资料",
                    "/Public/资料",
                    "/Public",
                    "directory",
                    None,
                    None,
                ),
            ],
            generation=1,
        )
        session.commit()


def test_search_matches_unicode_substring_and_returns_full_path(database):
    seed_entries(database)
    with Session(database) as session:
        result = EntryRepository(session).search(
            "年度项目",
            page=1,
            page_size=20,
        )

    assert result.total == 1
    assert (
        result.items[0].full_path
        == "/Public/年度项目计划.docx"
    )


def test_short_search_falls_back_to_safe_like(database):
    seed_entries(database)
    with Session(database) as session:
        result = EntryRepository(session).search(
            "项",
            page=1,
            page_size=20,
        )

    assert result.total == 1


def test_empty_search_returns_no_rows(database):
    seed_entries(database)
    with Session(database) as session:
        result = EntryRepository(session).search(
            "   ",
            page=1,
            page_size=20,
        )

    assert result.total == 0
