from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from nas_index.models import Base


FTS_DDL = (
    "DROP TRIGGER IF EXISTS entries_ai",
    "DROP TRIGGER IF EXISTS entries_ad",
    "DROP TRIGGER IF EXISTS entries_au",
    "DROP TABLE IF EXISTS entry_search",
    """
    CREATE VIRTUAL TABLE entry_search USING fts5(
        name,
        content='entries',
        content_rowid='id',
        tokenize='trigram'
    )
    """,
    """
    CREATE TRIGGER entries_ai AFTER INSERT ON entries BEGIN
      INSERT INTO entry_search(rowid, name) VALUES (new.id, new.name);
    END
    """,
    """
    CREATE TRIGGER entries_ad AFTER DELETE ON entries BEGIN
      INSERT INTO entry_search(entry_search, rowid, name)
      VALUES ('delete', old.id, old.name);
    END
    """,
    """
    CREATE TRIGGER entries_au AFTER UPDATE OF name ON entries
    WHEN old.name IS NOT new.name BEGIN
      INSERT INTO entry_search(entry_search, rowid, name)
      VALUES ('delete', old.id, old.name);
      INSERT INTO entry_search(rowid, name) VALUES (new.id, new.name);
    END
    """,
    """
    INSERT INTO entry_search(rowid, name)
    SELECT id, name FROM entries
    """,
)


BEIJING_TIME_MIGRATION_KEY = "beijing_time_migration_applied"


DATETIME_COLUMNS = {
    "nas_config": ("updated_at",),
    "nas_servers": ("created_at", "updated_at"),
    "nas_credentials": ("updated_at",),
    "entries": ("modified_at", "created_at", "updated_at"),
    "share_sync_state": (
        "last_synced_at",
        "last_full_synced_at",
        "next_sync_at",
    ),
    "sync_runs": ("started_at", "finished_at"),
    "sync_errors": ("created_at",),
    "scan_runs": ("started_at", "finished_at"),
    "scan_errors": ("created_at",),
}


def create_database_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite:///"):
        path = Path(database_url.removeprefix("sqlite:///"))
        path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_database(engine: Engine) -> None:
    _migrate_legacy_schema(engine)
    Base.metadata.create_all(engine)
    _migrate_legacy_schema(engine)
    _migrate_utc_times_to_beijing(engine)
    with engine.begin() as connection:
        for statement in FTS_DDL:
            connection.exec_driver_sql(statement)


def _migrate_legacy_schema(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return
    table_names = set(inspect(engine).get_table_names())
    if "entries" in table_names:
        _migrate_entries_table(engine)
    Base.metadata.create_all(engine)
    table_names = set(inspect(engine).get_table_names())
    if "nas_config" in table_names:
        _migrate_single_nas_config(engine)
    if "entries" in table_names:
        _backfill_entry_scope(engine)


def _migrate_utc_times_to_beijing(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS app_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        marker = connection.execute(
            text(
                """
                SELECT value
                FROM app_metadata
                WHERE key = :key
                """
            ),
            {"key": BEIJING_TIME_MIGRATION_KEY},
        ).scalar()
        if marker == "1":
            return

        table_names = {
            row["name"]
            for row in connection.execute(
                text(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table'
                    """
                )
            ).mappings()
        }
        for table_name, column_names in DATETIME_COLUMNS.items():
            if table_name not in table_names:
                continue
            existing_columns = {
                row["name"]
                for row in connection.execute(
                    text(f"PRAGMA table_info({table_name})")
                ).mappings()
            }
            for column_name in column_names:
                if column_name not in existing_columns:
                    continue
                connection.exec_driver_sql(
                    f"""
                    UPDATE {table_name}
                    SET {column_name} = datetime({column_name}, '+8 hours')
                    WHERE {column_name} IS NOT NULL
                    """
                )

        connection.execute(
            text(
                """
                INSERT OR REPLACE INTO app_metadata (key, value)
                VALUES (:key, '1')
                """
            ),
            {"key": BEIJING_TIME_MIGRATION_KEY},
        )


def _migrate_entries_table(engine: Engine) -> None:
    with engine.begin() as connection:
        columns = {
            row["name"]
            for row in connection.execute(
                text("PRAGMA table_info(entries)")
            ).mappings()
        }
        if "nas_id" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE entries ADD COLUMN nas_id INTEGER NOT NULL DEFAULT 1"
            )
        if "share_path" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE entries ADD COLUMN share_path TEXT NOT NULL DEFAULT '/'"
            )
        connection.exec_driver_sql(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_entries_nas_full_path
            ON entries (nas_id, full_path)
            """
        )
        connection.exec_driver_sql(
            """
            CREATE INDEX IF NOT EXISTS ix_entries_nas_share
            ON entries (nas_id, share_path)
            """
        )
        connection.exec_driver_sql(
            """
            CREATE INDEX IF NOT EXISTS ix_entries_nas_parent_path
            ON entries (nas_id, parent_path)
            """
        )
        connection.exec_driver_sql(
            """
            CREATE INDEX IF NOT EXISTS ix_entries_nas_entry_type
            ON entries (nas_id, entry_type)
            """
        )
        connection.exec_driver_sql(
            """
            CREATE INDEX IF NOT EXISTS ix_entries_nas_generation
            ON entries (nas_id, scan_generation)
            """
        )


def _migrate_single_nas_config(engine: Engine) -> None:
    with engine.begin() as connection:
        existing_server_id = connection.execute(
            text("SELECT id FROM nas_servers ORDER BY id LIMIT 1")
        ).scalar()
        legacy = connection.execute(
            text(
                """
                SELECT base_url, port, use_https, username, password, updated_at
                FROM nas_config
                WHERE id = 1
                """
            )
        ).mappings().first()
        if legacy is None or existing_server_id is not None:
            return

        parsed = urlsplit(str(legacy["base_url"]))
        name = (
            parsed.hostname
            or str(legacy["base_url"])
            .removeprefix("http://")
            .removeprefix("https://")
        )
        connection.execute(
            text(
                """
                INSERT INTO nas_servers (
                    name, base_url, port, use_https, enabled,
                    sync_interval_minutes, full_resync_interval_hours,
                    created_at, updated_at
                )
                VALUES (
                    :name, :base_url, :port, :use_https, 1,
                    30, 24, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            ),
            {
                "name": name,
                "base_url": legacy["base_url"],
                "port": legacy["port"],
                "use_https": legacy["use_https"],
            },
        )
        nas_id = connection.execute(
            text("SELECT id FROM nas_servers ORDER BY id LIMIT 1")
        ).scalar_one()
        connection.execute(
            text(
                """
                INSERT INTO nas_credentials (
                    nas_id, username, password, updated_at
                )
                VALUES (
                    :nas_id, :username, :password, :updated_at
                )
                """
            ),
            {
                "nas_id": nas_id,
                "username": legacy["username"],
                "password": legacy["password"],
                "updated_at": legacy["updated_at"],
            },
        )
        connection.execute(
            text("UPDATE entries SET nas_id = :nas_id WHERE nas_id = 1"),
            {"nas_id": nas_id},
        )


def _backfill_entry_scope(engine: Engine) -> None:
    with engine.begin() as connection:
        rows = connection.execute(
            text(
                """
                SELECT id, full_path
                FROM entries
                WHERE share_path = '/' OR share_path IS NULL
                """
            )
        ).mappings()
        for row in rows:
            connection.execute(
                text(
                    """
                    UPDATE entries
                    SET share_path = :share_path
                    WHERE id = :id
                    """
                ),
                {
                    "share_path": _share_path_from_full_path(
                        row["full_path"]
                    ),
                    "id": row["id"],
                },
            )


def _share_path_from_full_path(full_path: str) -> str:
    parts = [part for part in full_path.replace("\\", "/").split("/") if part]
    if not parts:
        return "/"
    return f"/{parts[0]}"
