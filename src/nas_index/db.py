from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from nas_index.models import Base


FTS_DDL = (
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS entry_search USING fts5(
        name,
        content='entries',
        content_rowid='id',
        tokenize='trigram'
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
      INSERT INTO entry_search(rowid, name) VALUES (new.id, new.name);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
      INSERT INTO entry_search(entry_search, rowid, name)
      VALUES ('delete', old.id, old.name);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE OF name ON entries
    WHEN old.name IS NOT new.name BEGIN
      INSERT INTO entry_search(entry_search, rowid, name)
      VALUES ('delete', old.id, old.name);
      INSERT INTO entry_search(rowid, name) VALUES (new.id, new.name);
    END
    """,
)


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
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        for statement in FTS_DDL:
            connection.exec_driver_sql(statement)
