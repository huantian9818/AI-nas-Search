from sqlalchemy import text

from nas_index.db import create_database_engine, init_database
from nas_index.models import Base


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


def test_init_database_migrates_existing_utc_times_to_beijing_once(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'time.db'}")
    Base.metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO nas_servers (
                    id, name, base_url, port, use_https, enabled,
                    sync_interval_minutes, full_resync_interval_hours,
                    created_at, updated_at
                )
                VALUES (
                    1, 'Office', 'http://nas.local', 8080, 0, 1,
                    240, 24,
                    '2026-01-01 00:00:00',
                    '2026-01-01 00:00:00'
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO sync_runs (
                    id, nas_id, scope, share_path, generation, status,
                    started_at, finished_at, processed_entries,
                    current_path, error_summary
                )
                VALUES (
                    1, 1, 'nas', NULL, 1, 'succeeded',
                    '2026-01-01 00:00:00',
                    '2026-01-01 01:00:00',
                    10, '/', NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO share_sync_state (
                    nas_id, share_path, last_synced_at,
                    last_full_synced_at, next_sync_at,
                    last_generation, status, last_error
                )
                VALUES (
                    1, '/Public',
                    '2026-01-01 01:00:00',
                    '2026-01-01 01:00:00',
                    '2026-01-01 04:00:00',
                    1, 'succeeded', NULL
                )
                """
            )
        )

    init_database(engine)
    init_database(engine)

    with engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT started_at, finished_at
                FROM sync_runs
                WHERE id = 1
                """
            )
        ).mappings().one()
        state = connection.execute(
            text(
                """
                SELECT last_synced_at, next_sync_at
                FROM share_sync_state
                WHERE nas_id = 1 AND share_path = '/Public'
                """
            )
        ).mappings().one()
        marker = connection.execute(
            text(
                """
                SELECT value
                FROM app_metadata
                WHERE key = 'beijing_time_migration_applied'
                """
            )
        ).scalar_one()

    assert row["started_at"].startswith("2026-01-01 08:00:00")
    assert row["finished_at"].startswith("2026-01-01 09:00:00")
    assert state["last_synced_at"].startswith("2026-01-01 09:00:00")
    assert state["next_sync_at"].startswith("2026-01-01 12:00:00")
    assert marker == "1"
