from datetime import UTC, datetime

from sqlalchemy import event
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


def test_fts_search_forces_fts_before_permission_filter(database):
    seed_entries(database)
    statements: list[str] = []

    @event.listens_for(database, "before_cursor_execute")
    def capture_statement(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ):
        if "entry_search" in statement:
            statements.append(statement)

    try:
        with Session(database) as session:
            EntryRepository(session).search(
                "年度项目",
                nas_id=1,
                allowed_share_paths=("/Public",),
                page=1,
                page_size=20,
            )
    finally:
        event.remove(
            database,
            "before_cursor_execute",
            capture_statement,
        )

    search_statements = [
        statement
        for statement in statements
        if "JOIN entries AS e" in statement
    ]
    assert search_statements
    assert all(
        "CROSS JOIN entries AS e" in statement
        for statement in search_statements
    )
