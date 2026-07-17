from datetime import UTC, datetime

from sqlalchemy import inspect, text

from nas_index.db import create_database_engine, init_database


def test_init_database_creates_multi_nas_tables(tmp_path):
    engine = create_database_engine(
        f"sqlite:///{tmp_path / 'schema.db'}"
    )
    try:
        init_database(engine)
        inspector = inspect(engine)

        assert "nas_servers" in inspector.get_table_names()
        assert "nas_credentials" in inspector.get_table_names()
        assert "share_sync_state" in inspector.get_table_names()
        assert "sync_runs" in inspector.get_table_names()
        assert "sync_errors" in inspector.get_table_names()
        server_columns = {
            column["name"]
            for column in inspector.get_columns("nas_servers")
        }

        entry_columns = {
            column["name"]
            for column in inspector.get_columns("entries")
        }
        entry_indexes = {
            index["name"]
            for index in inspector.get_indexes("entries")
        }
        assert "nas_id" in entry_columns
        assert "share_path" in entry_columns
        assert "ix_entries_nas_share" in entry_indexes
        assert "ix_entries_nas_parent_path" in entry_indexes
        assert "ix_entries_nas_entry_type" in entry_indexes
        assert "ix_entries_nas_generation" in entry_indexes
        assert "skip_tls_verify" in server_columns
    finally:
        engine.dispose()


def test_init_database_migrates_single_nas_config_and_entries(
    tmp_path,
):
    database_path = tmp_path / "legacy.db"
    engine = create_database_engine(f"sqlite:///{database_path}")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                """
                CREATE TABLE nas_config (
                    id INTEGER PRIMARY KEY,
                    base_url VARCHAR(2048) NOT NULL,
                    port INTEGER NOT NULL,
                    use_https BOOLEAN NOT NULL,
                    username VARCHAR(255) NOT NULL,
                    password TEXT NOT NULL,
                    updated_at DATETIME NOT NULL,
                    CONSTRAINT single_nas_config CHECK (id = 1)
                )
                """
            )
            connection.execute(
                text(
                    """
                    INSERT INTO nas_config (
                        id, base_url, port, use_https,
                        username, password, updated_at
                    )
                    VALUES (
                        1, 'http://nas.local', 8080, 0,
                        'indexer', 'secret', :updated_at
                    )
                    """
                ),
                {"updated_at": datetime(2026, 1, 1, tzinfo=UTC)},
            )
            connection.exec_driver_sql(
                """
                CREATE TABLE entries (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    full_path TEXT NOT NULL,
                    parent_path TEXT NOT NULL,
                    entry_type VARCHAR(16) NOT NULL,
                    size_bytes INTEGER,
                    modified_at DATETIME,
                    scan_generation INTEGER NOT NULL,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    CONSTRAINT uq_entries_full_path UNIQUE (full_path)
                )
                """
            )
            connection.execute(
                text(
                    """
                    INSERT INTO entries (
                        name, full_path, parent_path, entry_type,
                        size_bytes, modified_at, scan_generation,
                        created_at, updated_at
                    )
                    VALUES (
                        'Public', '/Public', '/', 'directory',
                        NULL, NULL, 1, :created_at, :updated_at
                    )
                    """
                ),
                {
                    "created_at": datetime(2026, 1, 1, tzinfo=UTC),
                    "updated_at": datetime(2026, 1, 1, tzinfo=UTC),
                },
            )

        init_database(engine)

        with engine.connect() as connection:
            inspector = inspect(connection)
            server = connection.execute(
                text(
                    """
                    SELECT id, name, base_url, port, skip_tls_verify
                    FROM nas_servers
                    """
                )
            ).mappings().one()
            credential = connection.execute(
                text(
                    """
                    SELECT nas_id, username, password
                    FROM nas_credentials
                    """
                )
            ).mappings().one()
            entry = connection.execute(
                text(
                    """
                    SELECT nas_id, share_path, full_path
                    FROM entries
                    WHERE full_path = '/Public'
                    """
                )
            ).mappings().one()
            entry_indexes = {
                index["name"]
                for index in inspector.get_indexes("entries")
            }

        assert server["name"] == "nas.local"
        assert server["base_url"] == "http://nas.local"
        assert server["port"] == 8080
        assert server["skip_tls_verify"] == 0
        assert credential["nas_id"] == server["id"]
        assert credential["username"] == "indexer"
        assert credential["password"] == "secret"
        assert entry["nas_id"] == server["id"]
        assert entry["share_path"] == "/Public"
        assert "ix_entries_nas_share" in entry_indexes
        assert "ix_entries_nas_parent_path" in entry_indexes
        assert "ix_entries_nas_entry_type" in entry_indexes
        assert "ix_entries_nas_generation" in entry_indexes
    finally:
        engine.dispose()
