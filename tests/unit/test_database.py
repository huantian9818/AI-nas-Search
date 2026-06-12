from sqlalchemy import text

from nas_index.db import create_database_engine, init_database


def test_database_enables_wal_and_creates_fts(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'schema.db'}")
    init_database(engine)

    with engine.begin() as connection:
        mode = connection.execute(text("PRAGMA journal_mode")).scalar_one()
        tables = {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type IN ('table', 'view')"
                )
            )
        }

    assert mode == "wal"
    assert {
        "nas_config",
        "entries",
        "scan_runs",
        "scan_errors",
        "entry_search",
    } <= tables


def test_entry_search_tracks_insert_update_and_delete(database):
    with database.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO entries
                    (name, full_path, parent_path, entry_type, size_bytes,
                     modified_at, scan_generation, created_at, updated_at)
                VALUES
                    ('report.pdf', '/Public/report.pdf', '/Public', 'file', 10,
                     NULL, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
            )
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM entry_search "
                    "WHERE entry_search MATCH 'report'"
                )
            ).scalar_one()
            == 1
        )
        connection.execute(
            text(
                "UPDATE entries SET name = 'budget.pdf' "
                "WHERE full_path = '/Public/report.pdf'"
            )
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM entry_search "
                    "WHERE entry_search MATCH 'budget'"
                )
            ).scalar_one()
            == 1
        )
        connection.execute(text("DELETE FROM entries"))
        assert (
            connection.execute(
                text("SELECT count(*) FROM entry_search")
            ).scalar_one()
            == 0
        )
