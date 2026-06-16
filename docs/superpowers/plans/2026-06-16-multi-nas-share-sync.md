# Multi-NAS Share Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build multi-NAS indexing with administrator-managed sync credentials, short-lived user share authorization, NAS-scoped browse/search, and scheduled incremental synchronization.

**Architecture:** Keep the existing FastAPI, SQLAlchemy, SQLite, server-rendered HTML shape, but split the current single-NAS configuration and full-scan path into NAS-scoped repositories and services. Admin credentials populate a local index; end-user credentials only discover allowed share roots and query-time filters keep browse/search results inside those roots. An in-process scheduler starts due NAS sync jobs, while manual sync remains available from the UI.

**Tech Stack:** Python 3.12+, FastAPI, Jinja2, SQLAlchemy 2, SQLite/FTS5, HTTPX, Pydantic Settings, Pytest.

---

## Scope Check

The approved spec spans schema, synchronization, authorization, and UI. These are tightly coupled for a usable first pass: NAS-scoped storage must exist before authorization filters, and the scheduler must call the NAS-scoped scanner. This plan keeps the work in one implementation plan but splits it into independently testable tasks with commits after each task.

## File Structure

- Modify `src/nas_index/models.py` for `NasServer`, `NasCredential`, `ShareSyncState`, `SyncRun`, `SyncError`, and NAS-scoped `Entry`.
- Modify `src/nas_index/types.py` for NAS value objects, `IndexedItem.share_path`, and user access values.
- Modify `src/nas_index/db.py` for in-place SQLite migrations, NAS-aware FTS triggers, and legacy single-NAS migration.
- Create `src/nas_index/repositories/nas.py` for NAS server and credential CRUD.
- Modify `src/nas_index/repositories/entries.py` for NAS-scoped upserts, browse, counts, search, and direct-child replacement.
- Create `src/nas_index/repositories/syncs.py` for NAS-aware sync runs and share sync state.
- Keep `src/nas_index/repositories/config.py` as a compatibility shim during migration, then update callers away from it.
- Modify `src/nas_index/qnap/client.py` only where needed to keep share paths canonical and reusable for user access checks.
- Modify `src/nas_index/services/scanner.py` into a NAS-aware scanner that writes `nas_id` and `share_path`.
- Create `src/nas_index/services/sync_manager.py` and route callers to it.
- Create `src/nas_index/services/access.py` for short-lived in-memory user access sessions.
- Modify `src/nas_index/config.py` for scheduler and access-session settings.
- Modify `src/nas_index/web/app.py` to initialize the new repositories, access store, and sync manager.
- Modify `src/nas_index/web/routes/settings.py` for admin multi-NAS management.
- Create `src/nas_index/web/routes/access.py` for user NAS access login/logout.
- Modify `src/nas_index/web/routes/browse.py`, `search.py`, `dashboard.py`, and `scans.py` for NAS-aware behavior.
- Modify templates in `src/nas_index/web/templates/` for admin NAS settings, access login, NAS-aware dashboard, browse, search, and scan status.
- Modify `README.md` for multi-NAS setup, sync behavior, and user access behavior.
- Add or update tests under `tests/unit/` and `tests/integration/`.

---

## Task 1: Add Multi-NAS Schema and Migration

**Files:**
- Modify: `src/nas_index/models.py`
- Modify: `src/nas_index/db.py`
- Modify: `src/nas_index/types.py`
- Test: `tests/unit/test_multi_nas_schema.py`

- [ ] **Step 1: Write failing schema and migration tests**

Create `tests/unit/test_multi_nas_schema.py`:

```python
from datetime import UTC, datetime

from sqlalchemy import inspect, text

from nas_index.db import create_database_engine, init_database


def test_init_database_creates_multi_nas_tables(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'schema.db'}")
    try:
        init_database(engine)
        inspector = inspect(engine)

        assert "nas_servers" in inspector.get_table_names()
        assert "nas_credentials" in inspector.get_table_names()
        assert "share_sync_state" in inspector.get_table_names()
        assert "sync_runs" in inspector.get_table_names()
        assert "sync_errors" in inspector.get_table_names()

        entry_columns = {
            column["name"]
            for column in inspector.get_columns("entries")
        }
        assert "nas_id" in entry_columns
        assert "share_path" in entry_columns
    finally:
        engine.dispose()


def test_init_database_migrates_single_nas_config_and_entries(tmp_path):
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
            server = connection.execute(
                text("SELECT id, name, base_url, port FROM nas_servers")
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

        assert server["name"] == "nas.local"
        assert server["base_url"] == "http://nas.local"
        assert server["port"] == 8080
        assert credential["nas_id"] == server["id"]
        assert credential["username"] == "indexer"
        assert credential["password"] == "secret"
        assert entry["nas_id"] == server["id"]
        assert entry["share_path"] == "/Public"
    finally:
        engine.dispose()
```

- [ ] **Step 2: Run schema tests to verify they fail**

Run:

```bash
uv run pytest tests/unit/test_multi_nas_schema.py -q
```

Expected: FAIL because `nas_servers`, `nas_credentials`, `share_sync_state`, `sync_runs`, `sync_errors`, `entries.nas_id`, and `entries.share_path` do not exist.

- [ ] **Step 3: Add value objects**

Modify `src/nas_index/types.py` to contain these definitions:

```python
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class NasConnection:
    base_url: str
    port: int
    use_https: bool
    username: str
    password: str

    @property
    def endpoint(self) -> str:
        return f"{self.base_url.rstrip('/')}:{self.port}"


@dataclass(frozen=True, slots=True)
class NasServerValue:
    id: int
    name: str
    base_url: str
    port: int
    use_https: bool
    enabled: bool
    sync_interval_minutes: int
    full_resync_interval_hours: int

    def to_connection(
        self,
        *,
        username: str,
        password: str,
    ) -> NasConnection:
        return NasConnection(
            base_url=self.base_url,
            port=self.port,
            use_https=self.use_https,
            username=username,
            password=password,
        )


@dataclass(frozen=True, slots=True)
class NasCredentialValue:
    nas_id: int
    username: str
    password: str


@dataclass(frozen=True, slots=True)
class IndexedItem:
    name: str
    full_path: str
    parent_path: str
    entry_type: str
    size_bytes: int | None
    modified_at: datetime | None
    share_path: str | None = None


@dataclass(frozen=True, slots=True)
class UserAccess:
    nas_id: int
    username: str
    share_paths: tuple[str, ...]
    expires_at: datetime
```

- [ ] **Step 4: Add SQLAlchemy models**

Modify `src/nas_index/models.py` so it contains the existing `Base` plus these model classes. Keep imports sorted with the existing style.

```python
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class NasConfig(Base):
    __tablename__ = "nas_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    base_url: Mapped[str] = mapped_column(String(2048))
    port: Mapped[int] = mapped_column(Integer)
    use_https: Mapped[bool] = mapped_column(Boolean)
    username: Mapped[str] = mapped_column(String(255))
    password: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (CheckConstraint("id = 1", name="single_nas_config"),)


class NasServer(Base):
    __tablename__ = "nas_servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    base_url: Mapped[str] = mapped_column(String(2048))
    port: Mapped[int] = mapped_column(Integer)
    use_https: Mapped[bool] = mapped_column(Boolean)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_interval_minutes: Mapped[int] = mapped_column(Integer, default=30)
    full_resync_interval_hours: Mapped[int] = mapped_column(Integer, default=24)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    credential: Mapped["NasCredential | None"] = relationship(
        back_populates="server",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("name", name="uq_nas_servers_name"),
        CheckConstraint("port BETWEEN 1 AND 65535", name="nas_server_port_range"),
        CheckConstraint(
            "sync_interval_minutes >= 1",
            name="nas_server_sync_interval_positive",
        ),
        CheckConstraint(
            "full_resync_interval_hours >= 1",
            name="nas_server_full_resync_interval_positive",
        ),
    )


class NasCredential(Base):
    __tablename__ = "nas_credentials"

    nas_id: Mapped[int] = mapped_column(
        ForeignKey("nas_servers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    username: Mapped[str] = mapped_column(String(255))
    password: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    server: Mapped[NasServer] = relationship(back_populates="credential")


class Entry(Base):
    __tablename__ = "entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nas_id: Mapped[int] = mapped_column(
        ForeignKey("nas_servers.id", ondelete="CASCADE"),
        default=1,
    )
    share_path: Mapped[str] = mapped_column(Text, default="/")
    name: Mapped[str] = mapped_column(Text)
    full_path: Mapped[str] = mapped_column(Text)
    parent_path: Mapped[str] = mapped_column(Text)
    entry_type: Mapped[str] = mapped_column(String(16))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scan_generation: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("nas_id", "full_path", name="uq_entries_nas_full_path"),
        CheckConstraint(
            "entry_type IN ('file', 'directory')",
            name="entry_type_values",
        ),
        Index("ix_entries_nas_share", "nas_id", "share_path"),
        Index("ix_entries_nas_parent_path", "nas_id", "parent_path"),
        Index("ix_entries_nas_entry_type", "nas_id", "entry_type"),
        Index("ix_entries_nas_generation", "nas_id", "scan_generation"),
    )


class ShareSyncState(Base):
    __tablename__ = "share_sync_state"

    nas_id: Mapped[int] = mapped_column(
        ForeignKey("nas_servers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    share_path: Mapped[str] = mapped_column(Text, primary_key=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_full_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_sync_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_generation: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')",
            name="share_sync_status_values",
        ),
        Index("ix_share_sync_due", "next_sync_at", "status"),
    )


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nas_id: Mapped[int] = mapped_column(
        ForeignKey("nas_servers.id", ondelete="CASCADE")
    )
    scope: Mapped[str] = mapped_column(String(16))
    share_path: Mapped[str | None] = mapped_column(Text)
    generation: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_entries: Mapped[int] = mapped_column(Integer, default=0)
    current_path: Mapped[str | None] = mapped_column(Text)
    error_summary: Mapped[str | None] = mapped_column(Text)
    errors: Mapped[list["SyncError"]] = relationship(
        back_populates="sync_run",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            "scope IN ('nas', 'share', 'directory')",
            name="sync_scope_values",
        ),
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'interrupted')",
            name="sync_status_values",
        ),
        Index("ix_sync_runs_nas_id", "nas_id"),
        Index("ix_sync_runs_generation", "generation"),
    )


class SyncError(Base):
    __tablename__ = "sync_errors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sync_run_id: Mapped[int] = mapped_column(
        ForeignKey("sync_runs.id", ondelete="CASCADE")
    )
    path: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sync_run: Mapped[SyncRun] = relationship(back_populates="errors")


class ScanRun(Base):
    __tablename__ = "scan_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    generation: Mapped[int] = mapped_column(Integer, unique=True)
    status: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_entries: Mapped[int] = mapped_column(Integer, default=0)
    current_path: Mapped[str | None] = mapped_column(Text)
    error_summary: Mapped[str | None] = mapped_column(Text)
    errors: Mapped[list["ScanError"]] = relationship(
        back_populates="scan_run",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'interrupted')",
            name="scan_status_values",
        ),
    )


class ScanError(Base):
    __tablename__ = "scan_errors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scan_run_id: Mapped[int] = mapped_column(
        ForeignKey("scan_runs.id", ondelete="CASCADE")
    )
    path: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    scan_run: Mapped[ScanRun] = relationship(back_populates="errors")
```

- [ ] **Step 5: Add migrations and NAS-aware FTS triggers**

Modify `src/nas_index/db.py` to call migration helpers before `Base.metadata.create_all(engine)`.

```python
from datetime import UTC, datetime
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
    with engine.begin() as connection:
        for statement in FTS_DDL:
            connection.exec_driver_sql(statement)


def _migrate_legacy_schema(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "entries" in table_names:
        _migrate_entries_table(engine)
    Base.metadata.create_all(engine)
    table_names = set(inspect(engine).get_table_names())
    if "nas_config" in table_names:
        _migrate_single_nas_config(engine)
    if "entries" in table_names:
        _backfill_entry_scope(engine)


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
        name = parsed.hostname or str(legacy["base_url"]).removeprefix("http://").removeprefix("https://")
        now = datetime.now(UTC)
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
                    30, 24, :created_at, :updated_at
                )
                """
            ),
            {
                "name": name,
                "base_url": legacy["base_url"],
                "port": legacy["port"],
                "use_https": legacy["use_https"],
                "created_at": now,
                "updated_at": now,
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
            share_path = _share_path_from_full_path(row["full_path"])
            connection.execute(
                text(
                    """
                    UPDATE entries
                    SET share_path = :share_path
                    WHERE id = :id
                    """
                ),
                {"share_path": share_path, "id": row["id"]},
            )


def _share_path_from_full_path(full_path: str) -> str:
    parts = [part for part in full_path.split("/") if part]
    if not parts:
        return "/"
    return f"/{parts[0]}"
```

- [ ] **Step 6: Run schema tests to verify they pass**

Run:

```bash
uv run pytest tests/unit/test_multi_nas_schema.py -q
```

Expected: PASS.

- [ ] **Step 7: Run full tests and commit**

Run:

```bash
uv run pytest
```

Expected: PASS with the existing Starlette deprecation warning only.

Commit:

```bash
git add src/nas_index/models.py src/nas_index/db.py src/nas_index/types.py tests/unit/test_multi_nas_schema.py
git commit -m "feat: add multi-nas schema"
```

---

## Task 2: Add NAS Repository and Migrate Settings Access

**Files:**
- Create: `src/nas_index/repositories/nas.py`
- Modify: `src/nas_index/repositories/config.py`
- Test: `tests/unit/test_nas_repository.py`

- [ ] **Step 1: Write failing repository tests**

Create `tests/unit/test_nas_repository.py`:

```python
from sqlalchemy.orm import Session

from nas_index.repositories.nas import NasRepository


def test_create_and_update_nas_server(database):
    with Session(database) as session:
        repository = NasRepository(session)
        server = repository.create_server(
            name="Office NAS",
            base_url="http://10.0.0.2",
            port=8080,
            use_https=False,
            enabled=True,
            sync_interval_minutes=15,
            full_resync_interval_hours=12,
            username="indexer",
            password="secret",
        )
        session.commit()

    with Session(database) as session:
        repository = NasRepository(session)
        loaded = repository.get_server(server.id)
        credential = repository.get_credential(server.id)

        assert loaded is not None
        assert loaded.name == "Office NAS"
        assert loaded.sync_interval_minutes == 15
        assert credential is not None
        assert credential.username == "indexer"
        assert credential.password == "secret"

        updated = repository.update_server(
            server.id,
            name="Office NAS Renamed",
            base_url="https://nas.example.com",
            port=443,
            use_https=True,
            enabled=False,
            sync_interval_minutes=60,
            full_resync_interval_hours=48,
            username="new-indexer",
            password="",
        )
        session.commit()

        credential = repository.get_credential(server.id)
        assert updated.name == "Office NAS Renamed"
        assert updated.enabled is False
        assert credential is not None
        assert credential.username == "new-indexer"
        assert credential.password == "secret"


def test_list_enabled_servers(database):
    with Session(database) as session:
        repository = NasRepository(session)
        repository.create_server(
            name="Enabled",
            base_url="http://enabled.local",
            port=8080,
            use_https=False,
            enabled=True,
            sync_interval_minutes=30,
            full_resync_interval_hours=24,
            username="indexer",
            password="secret",
        )
        repository.create_server(
            name="Disabled",
            base_url="http://disabled.local",
            port=8080,
            use_https=False,
            enabled=False,
            sync_interval_minutes=30,
            full_resync_interval_hours=24,
            username="indexer",
            password="secret",
        )
        session.commit()

    with Session(database) as session:
        names = [
            server.name
            for server in NasRepository(session).list_enabled_servers()
        ]

    assert names == ["Enabled"]
```

- [ ] **Step 2: Run repository tests to verify they fail**

Run:

```bash
uv run pytest tests/unit/test_nas_repository.py -q
```

Expected: FAIL because `nas_index.repositories.nas` does not exist.

- [ ] **Step 3: Implement `NasRepository`**

Create `src/nas_index/repositories/nas.py`:

```python
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from nas_index.models import NasCredential, NasServer
from nas_index.types import NasCredentialValue, NasConnection, NasServerValue


class NasRepository:
    def __init__(self, session: Session):
        self.session = session

    def create_server(
        self,
        *,
        name: str,
        base_url: str,
        port: int,
        use_https: bool,
        enabled: bool,
        sync_interval_minutes: int,
        full_resync_interval_hours: int,
        username: str,
        password: str,
    ) -> NasServerValue:
        if not password:
            raise ValueError("首次保存时必须输入索引账号密码")
        now = datetime.now(UTC)
        row = NasServer(
            name=name.strip(),
            base_url=base_url.rstrip("/"),
            port=port,
            use_https=use_https,
            enabled=enabled,
            sync_interval_minutes=sync_interval_minutes,
            full_resync_interval_hours=full_resync_interval_hours,
            created_at=now,
            updated_at=now,
        )
        row.credential = NasCredential(
            username=username.strip(),
            password=password,
            updated_at=now,
        )
        self.session.add(row)
        self.session.flush()
        return self._server_value(row)

    def update_server(
        self,
        nas_id: int,
        *,
        name: str,
        base_url: str,
        port: int,
        use_https: bool,
        enabled: bool,
        sync_interval_minutes: int,
        full_resync_interval_hours: int,
        username: str,
        password: str,
    ) -> NasServerValue:
        row = self.session.get(NasServer, nas_id)
        if row is None:
            raise LookupError("NAS 不存在")

        now = datetime.now(UTC)
        row.name = name.strip()
        row.base_url = base_url.rstrip("/")
        row.port = port
        row.use_https = use_https
        row.enabled = enabled
        row.sync_interval_minutes = sync_interval_minutes
        row.full_resync_interval_hours = full_resync_interval_hours
        row.updated_at = now

        credential = row.credential
        if credential is None:
            if not password:
                raise ValueError("首次保存时必须输入索引账号密码")
            credential = NasCredential(nas_id=nas_id)
            self.session.add(credential)
        credential.username = username.strip()
        if password:
            credential.password = password
        credential.updated_at = now
        self.session.flush()
        return self._server_value(row)

    def list_servers(self) -> list[NasServerValue]:
        return [
            self._server_value(row)
            for row in self.session.scalars(
                select(NasServer).order_by(NasServer.name, NasServer.id)
            )
        ]

    def list_enabled_servers(self) -> list[NasServerValue]:
        return [
            self._server_value(row)
            for row in self.session.scalars(
                select(NasServer)
                .where(NasServer.enabled.is_(True))
                .order_by(NasServer.name, NasServer.id)
            )
        ]

    def get_server(self, nas_id: int) -> NasServerValue | None:
        row = self.session.get(NasServer, nas_id)
        if row is None:
            return None
        return self._server_value(row)

    def get_credential(self, nas_id: int) -> NasCredentialValue | None:
        row = self.session.get(NasCredential, nas_id)
        if row is None:
            return None
        return NasCredentialValue(
            nas_id=row.nas_id,
            username=row.username,
            password=row.password,
        )

    def connection_for_indexer(self, nas_id: int) -> NasConnection | None:
        server = self.get_server(nas_id)
        credential = self.get_credential(nas_id)
        if server is None or credential is None:
            return None
        return server.to_connection(
            username=credential.username,
            password=credential.password,
        )

    @staticmethod
    def _server_value(row: NasServer) -> NasServerValue:
        return NasServerValue(
            id=row.id,
            name=row.name,
            base_url=row.base_url,
            port=row.port,
            use_https=row.use_https,
            enabled=row.enabled,
            sync_interval_minutes=row.sync_interval_minutes,
            full_resync_interval_hours=row.full_resync_interval_hours,
        )
```

- [ ] **Step 4: Keep legacy config repository compatible**

Modify `src/nas_index/repositories/config.py` so existing callers still work until later tasks update them.

```python
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from nas_index.models import NasConfig
from nas_index.repositories.nas import NasRepository
from nas_index.types import NasConnection


class ConfigRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self) -> NasConnection | None:
        repository = NasRepository(self.session)
        servers = repository.list_servers()
        if servers:
            return repository.connection_for_indexer(servers[0].id)

        row = self.session.get(NasConfig, 1)
        if row is None:
            return None
        return self._to_value(row)

    def save(self, value: NasConnection) -> NasConnection:
        repository = NasRepository(self.session)
        servers = repository.list_servers()
        if servers:
            repository.update_server(
                servers[0].id,
                name=value.base_url.removeprefix("http://").removeprefix("https://"),
                base_url=value.base_url.rstrip("/"),
                port=value.port,
                use_https=value.use_https,
                enabled=True,
                sync_interval_minutes=30,
                full_resync_interval_hours=24,
                username=value.username,
                password=value.password,
            )
            connection = repository.connection_for_indexer(servers[0].id)
            if connection is None:
                raise ValueError("NAS 配置保存失败")
            return connection

        row = self.session.get(NasConfig, 1)
        password = value.password or (row.password if row else "")
        if not password:
            raise ValueError("首次保存时必须输入密码")

        if row is None:
            row = NasConfig(id=1)
            self.session.add(row)

        row.base_url = value.base_url.rstrip("/")
        row.port = value.port
        row.use_https = value.use_https
        row.username = value.username.strip()
        row.password = password
        row.updated_at = datetime.now(UTC)
        return self._to_value(row)

    @staticmethod
    def _to_value(row: NasConfig) -> NasConnection:
        return NasConnection(
            base_url=row.base_url,
            port=row.port,
            use_https=row.use_https,
            username=row.username,
            password=row.password,
        )
```

- [ ] **Step 5: Run repository tests to verify they pass**

Run:

```bash
uv run pytest tests/unit/test_nas_repository.py -q
```

Expected: PASS.

- [ ] **Step 6: Run full tests and commit**

Run:

```bash
uv run pytest
```

Expected: PASS.

Commit:

```bash
git add src/nas_index/repositories/nas.py src/nas_index/repositories/config.py tests/unit/test_nas_repository.py
git commit -m "feat: add NAS repository"
```

---

## Task 3: Make Entries NAS-Scoped and Share-Filtered

**Files:**
- Modify: `src/nas_index/repositories/entries.py`
- Modify: `src/nas_index/web/routes/browse.py`
- Modify: `src/nas_index/web/routes/search.py`
- Modify: `tests/conftest.py`
- Test: `tests/unit/test_entry_repository.py`
- Test: `tests/unit/test_search_repository.py`
- Test: `tests/integration/test_browse.py`
- Test: `tests/integration/test_search.py`

- [ ] **Step 1: Write failing NAS-scoped entry tests**

Add these tests to `tests/unit/test_entry_repository.py`:

```python
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from nas_index.repositories.entries import EntryRepository
from nas_index.repositories.nas import NasRepository
from nas_index.types import IndexedItem


def _create_nas(session: Session, name: str) -> int:
    return NasRepository(session).create_server(
        name=name,
        base_url=f"http://{name.lower()}.local",
        port=8080,
        use_https=False,
        enabled=True,
        sync_interval_minutes=30,
        full_resync_interval_hours=24,
        username="indexer",
        password="secret",
    ).id


def test_entries_with_same_path_do_not_collide_across_nas(database):
    with Session(database) as session:
        nas_one = _create_nas(session, "One")
        nas_two = _create_nas(session, "Two")
        repository = EntryRepository(session)
        item = IndexedItem(
            name="report.txt",
            full_path="/Public/report.txt",
            parent_path="/Public",
            entry_type="file",
            size_bytes=10,
            modified_at=datetime(2026, 1, 1, tzinfo=UTC),
            share_path="/Public",
        )
        repository.upsert_batch(nas_one, [item], generation=1)
        repository.upsert_batch(nas_two, [item], generation=1)
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
```

- [ ] **Step 2: Run entry tests to verify they fail**

Run:

```bash
uv run pytest tests/unit/test_entry_repository.py::test_entries_with_same_path_do_not_collide_across_nas tests/unit/test_entry_repository.py::test_share_filter_hides_entries_outside_allowed_roots -q
```

Expected: FAIL because `EntryRepository.upsert_batch()` does not accept `nas_id`, and search does not accept `nas_id` or `allowed_share_paths`.

- [ ] **Step 3: Implement share path helper and scoped predicates**

Modify `src/nas_index/repositories/entries.py` with these helpers near the top:

```python
def share_path_from_full_path(full_path: str) -> str:
    parts = [part for part in full_path.replace("\\", "/").split("/") if part]
    if not parts:
        return "/"
    return f"/{parts[0]}"


def _allowed_share_predicate(
    *,
    nas_id: int,
    allowed_share_paths: tuple[str, ...],
):
    return (
        Entry.nas_id == nas_id,
        Entry.share_path.in_(allowed_share_paths),
    )
```

- [ ] **Step 4: Change `EntryRepository` methods to accept NAS scope**

Update these method signatures and bodies in `src/nas_index/repositories/entries.py`:

```python
def upsert_batch(
    self,
    nas_id: int,
    items: list[IndexedItem],
    generation: int,
) -> None:
    if not items:
        return
    now = datetime.now(UTC)
    values = [
        {
            "nas_id": nas_id,
            "share_path": item.share_path
            or share_path_from_full_path(item.full_path),
            "name": item.name,
            "full_path": item.full_path,
            "parent_path": item.parent_path,
            "entry_type": item.entry_type,
            "size_bytes": item.size_bytes,
            "modified_at": item.modified_at,
            "scan_generation": generation,
            "created_at": now,
            "updated_at": now,
        }
        for item in items
    ]
    statement = insert(Entry).values(values)
    self.session.execute(
        statement.on_conflict_do_update(
            index_elements=[Entry.nas_id, Entry.full_path],
            set_={
                "share_path": statement.excluded.share_path,
                "name": statement.excluded.name,
                "parent_path": statement.excluded.parent_path,
                "entry_type": statement.excluded.entry_type,
                "size_bytes": statement.excluded.size_bytes,
                "modified_at": statement.excluded.modified_at,
                "scan_generation": statement.excluded.scan_generation,
                "updated_at": statement.excluded.updated_at,
            },
        )
    )


def get_by_path(self, nas_id: int, full_path: str) -> Entry | None:
    return self.session.scalar(
        select(Entry).where(
            Entry.nas_id == nas_id,
            Entry.full_path == full_path,
        )
    )


def list_children(
    self,
    nas_id: int,
    parent_path: str,
    *,
    allowed_share_paths: tuple[str, ...],
    page: int,
    page_size: int,
) -> Page[Entry]:
    if not allowed_share_paths:
        return Page([], 0, page, page_size)
    predicate = (
        Entry.nas_id == nas_id,
        Entry.parent_path == parent_path,
        Entry.share_path.in_(allowed_share_paths),
    )
    total = (
        self.session.scalar(
            select(func.count())
            .select_from(Entry)
            .where(*predicate)
        )
        or 0
    )
    rows = list(
        self.session.scalars(
            select(Entry)
            .where(*predicate)
            .order_by(
                case((Entry.entry_type == "directory", 0), else_=1),
                func.lower(Entry.name),
                Entry.id,
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return Page(rows, total, page, page_size)


def list_child_directories(
    self,
    nas_id: int,
    parent_path: str,
    *,
    allowed_share_paths: tuple[str, ...],
) -> list[Entry]:
    if not allowed_share_paths:
        return []
    return list(
        self.session.scalars(
            select(Entry)
            .where(
                Entry.nas_id == nas_id,
                Entry.parent_path == parent_path,
                Entry.share_path.in_(allowed_share_paths),
                Entry.entry_type == "directory",
            )
            .order_by(func.lower(Entry.name), Entry.id)
        )
    )
```

- [ ] **Step 5: Change delete, count, and search methods**

Continue modifying `src/nas_index/repositories/entries.py`:

```python
def delete_stale(self, nas_id: int, generation: int) -> int:
    result = self.session.execute(
        delete(Entry).where(
            Entry.nas_id == nas_id,
            Entry.scan_generation < generation,
        )
    )
    return result.rowcount or 0


def replace_children(
    self,
    nas_id: int,
    parent_path: str,
    observed_full_paths: set[str],
) -> int:
    predicate = [
        Entry.nas_id == nas_id,
        Entry.parent_path == parent_path,
    ]
    if observed_full_paths:
        predicate.append(Entry.full_path.not_in(observed_full_paths))
    result = self.session.execute(delete(Entry).where(*predicate))
    return result.rowcount or 0


def counts(
    self,
    *,
    nas_id: int | None = None,
    allowed_share_paths: tuple[str, ...] | None = None,
) -> tuple[int, int]:
    predicates = []
    if nas_id is not None:
        predicates.append(Entry.nas_id == nas_id)
    if allowed_share_paths is not None:
        if not allowed_share_paths:
            return (0, 0)
        predicates.append(Entry.share_path.in_(allowed_share_paths))
    rows = {
        entry_type: count
        for entry_type, count in self.session.execute(
            select(Entry.entry_type, func.count())
            .where(*predicates)
            .group_by(Entry.entry_type)
        )
    }
    return (int(rows.get("file", 0)), int(rows.get("directory", 0)))


def list_share_paths(self, nas_id: int) -> tuple[str, ...]:
    return tuple(
        self.session.scalars(
            select(Entry.share_path)
            .where(Entry.nas_id == nas_id)
            .distinct()
            .order_by(Entry.share_path)
        )
    )


def search(
    self,
    query: str,
    *,
    nas_id: int,
    allowed_share_paths: tuple[str, ...],
    page: int,
    page_size: int,
) -> Page[Entry]:
    query = query.strip()
    if not query or not allowed_share_paths:
        return Page([], 0, page, page_size)

    offset = (page - 1) * page_size
    if len(query) < 3:
        escaped = (
            query.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        predicate = (
            Entry.nas_id == nas_id,
            Entry.share_path.in_(allowed_share_paths),
            Entry.name.ilike(f"%{escaped}%", escape="\\"),
        )
        total = (
            self.session.scalar(
                select(func.count()).select_from(Entry).where(*predicate)
            )
            or 0
        )
        rows = list(
            self.session.scalars(
                select(Entry)
                .where(*predicate)
                .order_by(
                    case((Entry.entry_type == "directory", 0), else_=1),
                    func.lower(Entry.name),
                    Entry.id,
                )
                .offset(offset)
                .limit(page_size)
            )
        )
        return Page(rows, total, page, page_size)

    match_query = '"' + query.replace('"', '""') + '"'
    count_sql = text(
        """
        SELECT count(*)
        FROM entry_search
        JOIN entries AS e ON e.id = entry_search.rowid
        WHERE entry_search MATCH :query
          AND e.nas_id = :nas_id
          AND e.share_path IN :share_paths
        """
    ).bindparams(bindparam("share_paths", expanding=True))
    rows_sql = text(
        """
        SELECT e.*
        FROM entry_search AS search_index
        JOIN entries AS e ON e.id = search_index.rowid
        WHERE entry_search MATCH :query
          AND e.nas_id = :nas_id
          AND e.share_path IN :share_paths
        ORDER BY bm25(entry_search),
                 CASE WHEN e.entry_type = 'directory' THEN 0 ELSE 1 END,
                 lower(e.name),
                 e.id
        LIMIT :limit OFFSET :offset
        """
    ).bindparams(bindparam("share_paths", expanding=True))
    params = {
        "query": match_query,
        "nas_id": nas_id,
        "share_paths": list(allowed_share_paths),
    }
    total = int(self.session.execute(count_sql, params).scalar_one())
    rows = list(
        self.session.scalars(
            select(Entry).from_statement(rows_sql),
            {
                **params,
                "limit": page_size,
                "offset": offset,
            },
        )
    )
    return Page(rows, total, page, page_size)
```

Add `bindparam` to the SQLAlchemy imports:

```python
from sqlalchemy import bindparam, case, delete, func, select, text
```

- [ ] **Step 6: Update `tests/conftest.py` seed helpers**

Modify `tests/conftest.py` so `seed_entries` creates a NAS and passes `nas_id`:

```python
def seed_entries(engine) -> int:
    with Session(engine) as session:
        nas_id = NasRepository(session).create_server(
            name="Test NAS",
            base_url="http://nas.local",
            port=8080,
            use_https=False,
            enabled=True,
            sync_interval_minutes=30,
            full_resync_interval_hours=24,
            username="indexer",
            password="secret",
        ).id
        EntryRepository(session).upsert_batch(
            nas_id,
            [
                IndexedItem("Public", "/Public", "/", "directory", None, None, "/Public"),
                IndexedItem(
                    "年度项目计划.docx",
                    "/Public/年度项目计划.docx",
                    "/Public",
                    "file",
                    128,
                    datetime(2026, 1, 1, tzinfo=UTC),
                    "/Public",
                ),
                IndexedItem("资料", "/Public/资料", "/Public", "directory", None, None, "/Public"),
                IndexedItem(
                    "nested-only.txt",
                    "/Public/资料/nested-only.txt",
                    "/Public/资料",
                    "file",
                    8,
                    datetime(2026, 1, 1, tzinfo=UTC),
                    "/Public",
                ),
            ],
            generation=1,
        )
        session.commit()
        return nas_id
```

Add the import:

```python
from nas_index.repositories.nas import NasRepository
```

- [ ] **Step 7: Keep current browse and search routes working before user access exists**

In `src/nas_index/web/routes/browse.py`, add:

```python
from nas_index.repositories.nas import NasRepository
```

Update the entries import to include `Page`:

```python
from nas_index.repositories.entries import EntryRepository, Page
```

Add this helper:

```python
def _default_scope(
    session: Session,
    repository: EntryRepository,
) -> tuple[int, tuple[str, ...]] | None:
    servers = NasRepository(session).list_servers()
    if not servers:
        return None
    nas_id = servers[0].id
    return nas_id, repository.list_share_paths(nas_id)
```

In `browse()`, before listing children:

```python
scope = _default_scope(session, repository)
if scope is None:
    listing = Page([], 0, page, 100)
    tree_nodes = []
else:
    nas_id, allowed_share_paths = scope
    listing = repository.list_children(
        nas_id,
        path,
        allowed_share_paths=allowed_share_paths,
        page=page,
        page_size=100,
    )
    tree_nodes = _build_tree(
        repository,
        nas_id=nas_id,
        parent_path="/",
        current_path=path,
        expanded_paths=_expanded_paths(path),
        allowed_share_paths=allowed_share_paths,
    )
```

Update `_build_tree()` to accept `nas_id` and `allowed_share_paths`.

In `src/nas_index/web/routes/search.py`, add the same default-scope pattern so the route calls:

```python
results = repository.search(
    query,
    nas_id=nas_id,
    allowed_share_paths=allowed_share_paths,
    page=page,
    page_size=50,
)
```

Update `_build_search_tree()` and `_tree_context()` to pass `nas_id` and `allowed_share_paths` into `list_child_directories()`. Task 7 will replace this default-scope behavior with real user access sessions.

- [ ] **Step 8: Run targeted repository and route tests**

Run:

```bash
uv run pytest tests/unit/test_entry_repository.py tests/unit/test_search_repository.py tests/integration/test_browse.py tests/integration/test_search.py -q
```

Expected: PASS after updating existing test call sites to pass `nas_id` and `allowed_share_paths`, and after route tests use the seeded default NAS from `tests/conftest.py`.

- [ ] **Step 9: Run full tests and commit**

Run:

```bash
uv run pytest
```

Expected: PASS.

Commit:

```bash
git add src/nas_index/repositories/entries.py src/nas_index/web/routes/browse.py src/nas_index/web/routes/search.py tests/conftest.py tests/unit/test_entry_repository.py tests/unit/test_search_repository.py tests/integration/test_browse.py tests/integration/test_search.py
git commit -m "feat: scope entries by NAS"
```

---

## Task 4: Add User Access Session Service

**Files:**
- Create: `src/nas_index/services/access.py`
- Modify: `src/nas_index/config.py`
- Test: `tests/unit/test_access_service.py`

- [ ] **Step 1: Write failing access service tests**

Create `tests/unit/test_access_service.py`:

```python
from datetime import UTC, datetime, timedelta

from nas_index.services.access import AccessSessionStore
from nas_index.types import UserAccess


def test_access_session_store_round_trips_allowed_shares():
    store = AccessSessionStore(
        ttl_seconds=300,
        now=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )

    token = store.create(
        nas_id=12,
        username="alice",
        share_paths=("/Public", "/Team"),
    )
    access = store.get(token)

    assert isinstance(access, UserAccess)
    assert access.nas_id == 12
    assert access.username == "alice"
    assert access.share_paths == ("/Public", "/Team")
    assert access.expires_at == datetime(2026, 1, 1, 0, 5, tzinfo=UTC)


def test_access_session_store_expires_sessions():
    current = datetime(2026, 1, 1, tzinfo=UTC)
    store = AccessSessionStore(ttl_seconds=60, now=lambda: current)
    token = store.create(
        nas_id=1,
        username="alice",
        share_paths=("/Public",),
    )

    store.now = lambda: current + timedelta(seconds=61)

    assert store.get(token) is None
```

- [ ] **Step 2: Run access tests to verify they fail**

Run:

```bash
uv run pytest tests/unit/test_access_service.py -q
```

Expected: FAIL because `nas_index.services.access` does not exist.

- [ ] **Step 3: Add config setting**

Modify `src/nas_index/config.py`:

```python
class AppSettings(BaseSettings):
    database_url: str = "sqlite:///data/nas-index.db"
    log_dir: Path = Path("logs")
    scan_page_size: int = 500
    scan_batch_size: int = 500
    scan_concurrency: int = 4
    scan_progress_interval_seconds: float = 2.0
    scan_skip_recycle: bool = True
    qnap_timeout_seconds: float = 20.0
    qnap_retry_attempts: int = 3
    user_access_ttl_seconds: int = 900
    sync_scheduler_poll_seconds: float = 10.0

    model_config = SettingsConfigDict(
        env_prefix="NAS_INDEX_",
        env_file=".env",
        extra="ignore",
    )
```

- [ ] **Step 4: Implement access store**

Create `src/nas_index/services/access.py`:

```python
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe

from nas_index.types import UserAccess


class AccessSessionStore:
    def __init__(
        self,
        *,
        ttl_seconds: int,
        now: Callable[[], datetime] | None = None,
    ):
        self.ttl_seconds = ttl_seconds
        self.now = now or (lambda: datetime.now(UTC))
        self._sessions: dict[str, UserAccess] = {}

    def create(
        self,
        *,
        nas_id: int,
        username: str,
        share_paths: tuple[str, ...],
    ) -> str:
        token = token_urlsafe(32)
        expires_at = self.now() + timedelta(seconds=self.ttl_seconds)
        self._sessions[token] = UserAccess(
            nas_id=nas_id,
            username=username,
            share_paths=tuple(sorted(set(share_paths))),
            expires_at=expires_at,
        )
        return token

    def get(self, token: str | None) -> UserAccess | None:
        if not token:
            return None
        access = self._sessions.get(token)
        if access is None:
            return None
        if access.expires_at <= self.now():
            self._sessions.pop(token, None)
            return None
        return access

    def delete(self, token: str | None) -> None:
        if token:
            self._sessions.pop(token, None)
```

- [ ] **Step 5: Run access tests to verify they pass**

Run:

```bash
uv run pytest tests/unit/test_access_service.py -q
```

Expected: PASS.

- [ ] **Step 6: Run full tests and commit**

Run:

```bash
uv run pytest
```

Expected: PASS.

Commit:

```bash
git add src/nas_index/config.py src/nas_index/services/access.py tests/unit/test_access_service.py
git commit -m "feat: add user access sessions"
```

---

## Task 5: Make Scanner and Sync Runs NAS-Aware

**Files:**
- Create: `src/nas_index/repositories/syncs.py`
- Modify: `src/nas_index/services/scanner.py`
- Modify: `src/nas_index/qnap/client.py`
- Test: `tests/unit/test_sync_repository.py`
- Test: `tests/unit/test_scanner.py`

- [ ] **Step 1: Write failing sync repository tests**

Create `tests/unit/test_sync_repository.py`:

```python
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from nas_index.repositories.nas import NasRepository
from nas_index.repositories.syncs import SyncRepository


def _create_nas(session: Session) -> int:
    return NasRepository(session).create_server(
        name="Office",
        base_url="http://nas.local",
        port=8080,
        use_https=False,
        enabled=True,
        sync_interval_minutes=30,
        full_resync_interval_hours=24,
        username="indexer",
        password="secret",
    ).id


def test_sync_repository_tracks_run_and_share_state(database):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    with Session(database) as session:
        nas_id = _create_nas(session)
        repository = SyncRepository(session)
        run = repository.create_run(
            nas_id=nas_id,
            scope="share",
            share_path="/Public",
        )
        repository.ensure_share_state(
            nas_id=nas_id,
            share_path="/Public",
            next_sync_at=now,
        )
        repository.progress(run.id, processed=5, current_path="/Public")
        repository.succeed(run.id, processed=10)
        repository.mark_share_succeeded(
            nas_id=nas_id,
            share_path="/Public",
            generation=run.generation,
            next_sync_at=now + timedelta(minutes=30),
            full=True,
        )
        session.commit()

        latest = repository.latest_for_nas(nas_id)
        state = repository.get_share_state(nas_id, "/Public")

        assert latest is not None
        assert latest.status == "succeeded"
        assert latest.processed_entries == 10
        assert state is not None
        assert state.status == "succeeded"
        assert state.last_generation == run.generation
        assert state.next_sync_at == now + timedelta(minutes=30)
```

- [ ] **Step 2: Run sync tests to verify they fail**

Run:

```bash
uv run pytest tests/unit/test_sync_repository.py -q
```

Expected: FAIL because `nas_index.repositories.syncs` does not exist.

- [ ] **Step 3: Implement `SyncRepository`**

Create `src/nas_index/repositories/syncs.py`:

```python
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from nas_index.models import ShareSyncState, SyncError, SyncRun


class SyncRepository:
    def __init__(self, session: Session):
        self.session = session

    def create_run(
        self,
        *,
        nas_id: int,
        scope: str,
        share_path: str | None,
    ) -> SyncRun:
        generation = (
            self.session.scalar(
                select(func.max(SyncRun.generation)).where(
                    SyncRun.nas_id == nas_id
                )
            )
            or 0
        ) + 1
        run = SyncRun(
            nas_id=nas_id,
            scope=scope,
            share_path=share_path,
            generation=generation,
            status="running",
            started_at=datetime.now(UTC),
            finished_at=None,
            processed_entries=0,
            current_path=None,
            error_summary=None,
        )
        self.session.add(run)
        self.session.flush()
        return run

    def progress(self, run_id: int, processed: int, current_path: str) -> None:
        run = self._require_run(run_id)
        run.processed_entries = processed
        run.current_path = current_path

    def succeed(self, run_id: int, processed: int) -> None:
        run = self._require_run(run_id)
        run.status = "succeeded"
        run.processed_entries = processed
        run.finished_at = datetime.now(UTC)

    def fail(
        self,
        run_id: int,
        path: str,
        reason: str,
        processed: int,
    ) -> None:
        run = self._require_run(run_id)
        run.status = "failed"
        run.processed_entries = processed
        run.current_path = path
        run.error_summary = reason
        run.finished_at = datetime.now(UTC)
        self.session.add(
            SyncError(
                sync_run_id=run_id,
                path=path,
                reason=reason,
                created_at=datetime.now(UTC),
            )
        )

    def ensure_share_state(
        self,
        *,
        nas_id: int,
        share_path: str,
        next_sync_at: datetime,
    ) -> ShareSyncState:
        state = self.get_share_state(nas_id, share_path)
        if state is None:
            state = ShareSyncState(
                nas_id=nas_id,
                share_path=share_path,
                last_synced_at=None,
                last_full_synced_at=None,
                next_sync_at=next_sync_at,
                last_generation=0,
                status="pending",
                last_error=None,
            )
            self.session.add(state)
            self.session.flush()
        return state

    def mark_share_running(self, nas_id: int, share_path: str) -> None:
        state = self.get_share_state(nas_id, share_path)
        if state is None:
            raise LookupError("share sync state not found")
        state.status = "running"
        state.last_error = None

    def mark_share_succeeded(
        self,
        *,
        nas_id: int,
        share_path: str,
        generation: int,
        next_sync_at: datetime,
        full: bool,
    ) -> None:
        state = self.get_share_state(nas_id, share_path)
        if state is None:
            raise LookupError("share sync state not found")
        now = datetime.now(UTC)
        state.status = "succeeded"
        state.last_synced_at = now
        if full:
            state.last_full_synced_at = now
        state.next_sync_at = next_sync_at
        state.last_generation = generation
        state.last_error = None

    def mark_share_failed(
        self,
        *,
        nas_id: int,
        share_path: str,
        error: str,
        next_sync_at: datetime,
    ) -> None:
        state = self.get_share_state(nas_id, share_path)
        if state is None:
            raise LookupError("share sync state not found")
        state.status = "failed"
        state.last_error = error
        state.next_sync_at = next_sync_at

    def get_share_state(
        self,
        nas_id: int,
        share_path: str,
    ) -> ShareSyncState | None:
        return self.session.get(ShareSyncState, (nas_id, share_path))

    def due_share_states(self, now: datetime) -> list[ShareSyncState]:
        return list(
            self.session.scalars(
                select(ShareSyncState)
                .where(
                    ShareSyncState.next_sync_at <= now,
                    ShareSyncState.status != "running",
                )
                .order_by(ShareSyncState.next_sync_at)
            )
        )

    def latest_for_nas(self, nas_id: int) -> SyncRun | None:
        return self.session.scalar(
            select(SyncRun)
            .where(SyncRun.nas_id == nas_id)
            .order_by(SyncRun.id.desc())
            .limit(1)
        )

    def interrupt_running(self) -> int:
        result = self.session.execute(
            update(SyncRun)
            .where(SyncRun.status == "running")
            .values(status="interrupted", finished_at=datetime.now(UTC))
        )
        self.session.execute(
            update(ShareSyncState)
            .where(ShareSyncState.status == "running")
            .values(status="failed", last_error="同步任务被中断")
        )
        return result.rowcount or 0

    def _require_run(self, run_id: int) -> SyncRun:
        run = self.session.get(SyncRun, run_id)
        if run is None:
            raise LookupError("sync run not found")
        return run
```

- [ ] **Step 4: Update QNAP client share paths**

In `src/nas_index/qnap/client.py`, ensure `list_shares()` sets `share_path`:

```python
path = canonical_path(str(row["id"]))
shares.append(
    IndexedItem(
        name=str(row["text"]),
        full_path=path,
        parent_path="/",
        entry_type="directory",
        size_bytes=None,
        modified_at=None,
        share_path=path,
    )
)
```

In `iter_children()`, compute the share root:

```python
current_path = canonical_path(path)
share_path = canonical_path("/" + PurePosixPath(current_path).parts[1]) if current_path != "/" else "/"
```

Then pass `share_path=share_path` when yielding `IndexedItem`.

- [ ] **Step 5: Update scanner constructor and writes**

Modify `src/nas_index/services/scanner.py`:

```python
class Scanner:
    def __init__(
        self,
        engine: Engine,
        nas_id: int,
        client_factory: Callable[[], Any],
        page_size: int,
        batch_size: int,
        *,
        concurrency: int = 1,
        progress_interval_seconds: float = 0.0,
        skip_recycle: bool = False,
    ):
        self.engine = engine
        self.nas_id = nas_id
        self.client_factory = client_factory
        self.page_size = page_size
        self.batch_size = batch_size
        self.concurrency = max(1, concurrency)
        self.progress_interval_seconds = max(0.0, progress_interval_seconds)
        self.skip_recycle = skip_recycle
```

Replace `ScanRepository` usage with `SyncRepository`:

```python
with Session(self.engine) as session:
    run = SyncRepository(session).create_run(
        nas_id=self.nas_id,
        scope="nas",
        share_path=None,
    )
    run_id = run.id
    generation = run.generation
    session.commit()
```

Update `_write_batch()`:

```python
def _write_batch(self, batch: list[IndexedItem], generation: int) -> None:
    if not batch:
        return
    with Session(self.engine) as session:
        EntryRepository(session).upsert_batch(
            self.nas_id,
            batch,
            generation,
        )
        session.commit()
```

Update success cleanup:

```python
EntryRepository(session).delete_stale(self.nas_id, generation)
SyncRepository(session).succeed(run_id, processed)
```

Update progress and failure to use `SyncRepository`.

- [ ] **Step 6: Run targeted sync and scanner tests**

Run:

```bash
uv run pytest tests/unit/test_sync_repository.py tests/unit/test_scanner.py -q
```

Expected: PASS after updating scanner tests to pass a `nas_id` and to assert entries are written with that `nas_id`.

- [ ] **Step 7: Run full tests and commit**

Run:

```bash
uv run pytest
```

Expected: PASS after updating remaining test call sites for `Scanner`.

Commit:

```bash
git add src/nas_index/repositories/syncs.py src/nas_index/services/scanner.py src/nas_index/qnap/client.py tests/unit/test_sync_repository.py tests/unit/test_scanner.py tests/unit/test_qnap_listing.py
git commit -m "feat: make scanner NAS aware"
```

---

## Task 6: Add NAS-Aware Sync Manager and Scheduler

**Files:**
- Create: `src/nas_index/services/sync_manager.py`
- Modify: `src/nas_index/web/app.py`
- Modify: `src/nas_index/web/routes/scans.py`
- Test: `tests/unit/test_sync_manager.py`
- Test: `tests/integration/test_scan_routes.py`
- Test: `tests/integration/test_recovery.py`

- [ ] **Step 1: Write failing sync manager tests**

Create `tests/unit/test_sync_manager.py`:

```python
import asyncio

import pytest

from nas_index.services.sync_manager import NasSyncAlreadyRunning, SyncManager


class RecordingScanner:
    def __init__(self, calls: list[int], nas_id: int):
        self.calls = calls
        self.nas_id = nas_id

    async def run(self) -> int:
        self.calls.append(self.nas_id)
        await asyncio.sleep(0)
        return self.nas_id


@pytest.mark.asyncio
async def test_sync_manager_allows_one_active_run_per_nas():
    calls: list[int] = []
    manager = SyncManager(
        scanner_factory=lambda nas_id: RecordingScanner(calls, nas_id)
    )

    manager.start_nas(1)
    with pytest.raises(NasSyncAlreadyRunning):
        manager.start_nas(1)
    await manager.wait_all()

    assert calls == [1]


@pytest.mark.asyncio
async def test_sync_manager_runs_different_nas_ids():
    calls: list[int] = []
    manager = SyncManager(
        scanner_factory=lambda nas_id: RecordingScanner(calls, nas_id)
    )

    manager.start_nas(1)
    manager.start_nas(2)
    await manager.wait_all()

    assert sorted(calls) == [1, 2]
```

- [ ] **Step 2: Run sync manager tests to verify they fail**

Run:

```bash
uv run pytest tests/unit/test_sync_manager.py -q
```

Expected: FAIL because `nas_index.services.sync_manager` does not exist.

- [ ] **Step 3: Implement sync manager**

Create `src/nas_index/services/sync_manager.py`:

```python
import asyncio
from collections.abc import Callable
from typing import Any


class NasSyncAlreadyRunning(RuntimeError):
    pass


class SyncManager:
    def __init__(
        self,
        scanner_factory: Callable[[int], Any],
    ):
        self.scanner_factory = scanner_factory
        self._tasks: dict[int, asyncio.Task] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    def is_running(self, nas_id: int) -> bool:
        task = self._tasks.get(nas_id)
        return task is not None and not task.done()

    def start_nas(self, nas_id: int) -> None:
        lock = self._locks.setdefault(nas_id, asyncio.Lock())
        if self.is_running(nas_id) or lock.locked():
            raise NasSyncAlreadyRunning()
        self._tasks[nas_id] = asyncio.create_task(self._run_nas(nas_id, lock))

    async def _run_nas(self, nas_id: int, lock: asyncio.Lock) -> None:
        async with lock:
            await self.scanner_factory(nas_id).run()

    async def wait_all(self) -> None:
        tasks = [task for task in self._tasks.values() if not task.done()]
        if tasks:
            await asyncio.gather(*tasks)
```

- [ ] **Step 4: Wire sync manager into app state**

Modify `src/nas_index/web/app.py`:

```python
from nas_index.repositories.nas import NasRepository
from nas_index.repositories.syncs import SyncRepository
from nas_index.services.access import AccessSessionStore
from nas_index.services.sync_manager import SyncManager
```

In lifespan, replace `ScanRepository.interrupt_running()` with:

```python
with session_factory() as session:
    SyncRepository(session).interrupt_running()
    session.commit()
```

Add access store:

```python
app.state.access_store = AccessSessionStore(
    ttl_seconds=settings.user_access_ttl_seconds
)
```

Replace `scanner_factory()` with a NAS-aware factory:

```python
def scanner_factory(nas_id: int) -> Scanner:
    with Session(engine) as session:
        connection = NasRepository(session).connection_for_indexer(nas_id)
    if connection is None:
        raise RuntimeError("NAS configuration is missing")
    return Scanner(
        engine=engine,
        nas_id=nas_id,
        client_factory=lambda: QnapClient(
            connection,
            timeout_seconds=settings.qnap_timeout_seconds,
            retry_attempts=settings.qnap_retry_attempts,
        ),
        page_size=settings.scan_page_size,
        batch_size=settings.scan_batch_size,
        concurrency=settings.scan_concurrency,
        progress_interval_seconds=settings.scan_progress_interval_seconds,
        skip_recycle=settings.scan_skip_recycle,
    )


app.state.sync_manager = SyncManager(scanner_factory)
```

- [ ] **Step 5: Update scan routes for NAS ids**

Modify `src/nas_index/web/routes/scans.py`:

```python
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from nas_index.repositories.nas import NasRepository
from nas_index.repositories.syncs import SyncRepository
from nas_index.services.sync_manager import NasSyncAlreadyRunning
from nas_index.web.dependencies import get_session

router = APIRouter(prefix="/scans")


@router.post("")
async def start_scan(
    request: Request,
    nas_id: int = Form(...),
    session: Session = Depends(get_session),
):
    if NasRepository(session).get_server(nas_id) is None:
        return HTMLResponse("NAS 不存在", status_code=404)
    try:
        request.app.state.sync_manager.start_nas(nas_id)
    except NasSyncAlreadyRunning:
        return HTMLResponse("同步任务正在运行", status_code=409)
    return RedirectResponse("/", status_code=303)


@router.get("/status", response_class=HTMLResponse)
def scan_status(
    request: Request,
    nas_id: int,
    session: Session = Depends(get_session),
):
    latest = SyncRepository(session).latest_for_nas(nas_id)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="partials/scan_status.html",
        context={"scan": latest},
    )
```

- [ ] **Step 6: Run targeted tests**

Run:

```bash
uv run pytest tests/unit/test_sync_manager.py tests/integration/test_scan_routes.py tests/integration/test_recovery.py -q
```

Expected: PASS after updating integration tests to post `nas_id`.

- [ ] **Step 7: Run full tests and commit**

Run:

```bash
uv run pytest
```

Expected: PASS.

Commit:

```bash
git add src/nas_index/services/sync_manager.py src/nas_index/web/app.py src/nas_index/web/routes/scans.py tests/unit/test_sync_manager.py tests/integration/test_scan_routes.py tests/integration/test_recovery.py
git commit -m "feat: add NAS sync manager"
```

---

## Task 7: Add User Access Routes and NAS-Aware Browse/Search

**Files:**
- Create: `src/nas_index/web/routes/access.py`
- Modify: `src/nas_index/web/routes/browse.py`
- Modify: `src/nas_index/web/routes/search.py`
- Modify: `src/nas_index/web/app.py`
- Create: `src/nas_index/web/templates/access.html`
- Modify: `src/nas_index/web/templates/base.html`
- Modify: `src/nas_index/web/templates/browse.html`
- Modify: `src/nas_index/web/templates/search.html`
- Test: `tests/integration/test_user_access.py`
- Test: `tests/integration/test_browse.py`
- Test: `tests/integration/test_search.py`

- [ ] **Step 1: Write failing user access integration tests**

Create `tests/integration/test_user_access.py`:

```python
from sqlalchemy.orm import Session

from nas_index.repositories.nas import NasRepository
from nas_index.types import IndexedItem
from nas_index.repositories.entries import EntryRepository


def test_browse_requires_access_session(client):
    response = client.get("/browse")

    assert response.status_code == 303
    assert response.headers["location"] == "/access"


def test_access_session_filters_browse_results(client, monkeypatch):
    with Session(client.app.state.engine) as session:
        nas_id = NasRepository(session).create_server(
            name="Office",
            base_url="http://nas.local",
            port=8080,
            use_https=False,
            enabled=True,
            sync_interval_minutes=30,
            full_resync_interval_hours=24,
            username="indexer",
            password="secret",
        ).id
        EntryRepository(session).upsert_batch(
            nas_id,
            [
                IndexedItem("Public", "/Public", "/", "directory", None, None, "/Public"),
                IndexedItem("public.txt", "/Public/public.txt", "/Public", "file", 1, None, "/Public"),
                IndexedItem("Secret", "/Secret", "/", "directory", None, None, "/Secret"),
                IndexedItem("secret.txt", "/Secret/secret.txt", "/Secret", "file", 1, None, "/Secret"),
            ],
            generation=1,
        )
        session.commit()

    async def fake_check_access(connection):
        return ("/Public",)

    monkeypatch.setattr(
        "nas_index.web.routes.access.check_user_access",
        fake_check_access,
    )

    response = client.post(
        "/access",
        data={
            "nas_id": str(nas_id),
            "username": "alice",
            "password": "secret",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    response = client.get("/browse")
    assert response.status_code == 200
    assert "public.txt" in response.text
    assert "secret.txt" not in response.text
```

- [ ] **Step 2: Run user access tests to verify they fail**

Run:

```bash
uv run pytest tests/integration/test_user_access.py -q
```

Expected: FAIL because `/access` and access enforcement do not exist.

- [ ] **Step 3: Add access route**

Create `src/nas_index/web/routes/access.py`:

```python
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from nas_index.qnap.client import QnapClient
from nas_index.qnap.errors import QnapError
from nas_index.repositories.nas import NasRepository
from nas_index.types import NasConnection, UserAccess
from nas_index.web.dependencies import get_session

router = APIRouter(prefix="/access")


async def check_user_access(connection: NasConnection) -> tuple[str, ...]:
    async with QnapClient(connection) as client:
        return tuple(item.full_path for item in await client.list_shares())


def current_access(request: Request) -> UserAccess | None:
    token = request.cookies.get("nas_access")
    return request.app.state.access_store.get(token)


def require_access(request: Request) -> UserAccess | RedirectResponse:
    access = current_access(request)
    if access is None:
        return RedirectResponse("/access", status_code=303)
    return access


@router.get("", response_class=HTMLResponse)
def access_page(
    request: Request,
    session: Session = Depends(get_session),
):
    servers = NasRepository(session).list_enabled_servers()
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="access.html",
        context={"servers": servers},
    )


@router.post("")
async def create_access(
    request: Request,
    nas_id: int = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session),
):
    repository = NasRepository(session)
    server = repository.get_server(nas_id)
    if server is None:
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="access.html",
            context={
                "servers": repository.list_enabled_servers(),
                "error": "NAS 不存在",
            },
            status_code=404,
        )

    connection = server.to_connection(username=username, password=password)
    try:
        share_paths = await check_user_access(connection)
    except QnapError as exc:
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="access.html",
            context={
                "servers": repository.list_enabled_servers(),
                "error": str(exc),
            },
            status_code=401,
        )

    token = request.app.state.access_store.create(
        nas_id=nas_id,
        username=username,
        share_paths=share_paths,
    )
    response = RedirectResponse("/browse", status_code=303)
    response.set_cookie(
        "nas_access",
        token,
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/logout")
def logout(request: Request):
    request.app.state.access_store.delete(
        request.cookies.get("nas_access")
    )
    response = RedirectResponse("/access", status_code=303)
    response.delete_cookie("nas_access")
    return response
```

- [ ] **Step 4: Register access router**

Modify `src/nas_index/web/app.py` imports and router registration:

```python
from nas_index.web.routes import access as access_routes

app.include_router(access_routes.router)
```

- [ ] **Step 5: Add access template**

Create `src/nas_index/web/templates/access.html`:

```html
{% extends "base.html" %}
{% block content %}
<section class="panel">
  <h1>NAS 访问</h1>
  {% if error %}
    <p class="message message-error">{{ error }}</p>
  {% endif %}
  <form method="post" action="/access" class="settings-form">
    <label>
      NAS
      <select name="nas_id" required>
        {% for server in servers %}
          <option value="{{ server.id }}">{{ server.name }}</option>
        {% endfor %}
      </select>
    </label>
    <label>
      用户名
      <input name="username" autocomplete="username" required>
    </label>
    <label>
      密码
      <input name="password" type="password" autocomplete="current-password" required>
    </label>
    <button type="submit">进入</button>
  </form>
</section>
{% endblock %}
```

- [ ] **Step 6: Protect browse route**

At the top of `src/nas_index/web/routes/browse.py`, import:

```python
from fastapi.responses import HTMLResponse, RedirectResponse
from nas_index.web.routes.access import current_access
```

In `browse()`, before repository queries:

```python
access = current_access(request)
if access is None:
    return RedirectResponse("/access", status_code=303)
```

Pass NAS scope into repository calls:

```python
listing = repository.list_children(
    access.nas_id,
    path,
    allowed_share_paths=access.share_paths,
    page=page,
    page_size=100,
)
```

Update `_build_tree()` signature to include `nas_id` and `allowed_share_paths`, and pass them into `repository.list_child_directories()`.

- [ ] **Step 7: Protect search route**

At the top of `src/nas_index/web/routes/search.py`, import:

```python
from fastapi.responses import HTMLResponse, RedirectResponse
from nas_index.web.routes.access import current_access
```

In `search()`, before repository queries:

```python
access = current_access(request)
if access is None:
    return RedirectResponse("/access", status_code=303)
```

Change `repository.search()` call:

```python
results = repository.search(
    query,
    nas_id=access.nas_id,
    allowed_share_paths=access.share_paths,
    page=page,
    page_size=50,
)
```

Update `_build_search_tree()` and `_tree_context()` to accept `nas_id` and `allowed_share_paths`, and use them in `repository.list_child_directories()`.

- [ ] **Step 8: Run access and route tests**

Run:

```bash
uv run pytest tests/integration/test_user_access.py tests/integration/test_browse.py tests/integration/test_search.py -q
```

Expected: PASS after updating existing browse/search tests to create access cookies by calling `client.app.state.access_store.create` with `nas_id`, `username`, and `share_paths`.

- [ ] **Step 9: Run full tests and commit**

Run:

```bash
uv run pytest
```

Expected: PASS.

Commit:

```bash
git add src/nas_index/web/routes/access.py src/nas_index/web/routes/browse.py src/nas_index/web/routes/search.py src/nas_index/web/app.py src/nas_index/web/templates/access.html src/nas_index/web/templates/base.html src/nas_index/web/templates/browse.html src/nas_index/web/templates/search.html tests/integration/test_user_access.py tests/integration/test_browse.py tests/integration/test_search.py
git commit -m "feat: add user NAS access filtering"
```

---

## Task 8: Replace Settings With Multi-NAS Admin UI

**Files:**
- Modify: `src/nas_index/web/routes/settings.py`
- Modify: `src/nas_index/web/templates/settings.html`
- Modify: `src/nas_index/web/templates/dashboard.html`
- Modify: `src/nas_index/web/templates/partials/connection_result.html`
- Modify: `src/nas_index/web/templates/partials/scan_status.html`
- Test: `tests/integration/test_settings.py`
- Test: `tests/integration/test_dashboard.py`

- [ ] **Step 1: Write failing settings tests**

Modify `tests/integration/test_settings.py` to add:

```python
from sqlalchemy.orm import Session

from nas_index.repositories.nas import NasRepository


def test_settings_can_create_multiple_nas_servers(client):
    for name in ("Office", "Lab"):
        response = client.post(
            "/settings/nas",
            data={
                "name": name,
                "host": f"{name.lower()}.local",
                "port": "8080",
                "use_https": "",
                "enabled": "on",
                "sync_interval_minutes": "30",
                "full_resync_interval_hours": "24",
                "username": "indexer",
                "password": "secret",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303

    with Session(client.app.state.engine) as session:
        names = [
            server.name
            for server in NasRepository(session).list_servers()
        ]

    assert names == ["Lab", "Office"]
```

- [ ] **Step 2: Run settings tests to verify they fail**

Run:

```bash
uv run pytest tests/integration/test_settings.py::test_settings_can_create_multiple_nas_servers -q
```

Expected: FAIL because `/settings/nas` does not exist.

- [ ] **Step 3: Update settings route**

Modify `src/nas_index/web/routes/settings.py`:

```python
@router.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    session: Session = Depends(get_session),
):
    servers = NasRepository(session).list_servers()
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={"servers": servers},
    )


@router.post("/settings/nas")
def create_nas(
    request: Request,
    name: str = Form(...),
    host: str = Form(...),
    port: int = Form(..., ge=1, le=65535),
    use_https: bool = Form(False),
    enabled: bool = Form(False),
    sync_interval_minutes: int = Form(..., ge=1),
    full_resync_interval_hours: int = Form(..., ge=1),
    username: str = Form(...),
    password: str = Form(""),
    session: Session = Depends(get_session),
):
    repository = NasRepository(session)
    try:
        repository.create_server(
            name=name,
            base_url=normalize_base_url(host, use_https),
            port=port,
            use_https=use_https,
            enabled=enabled,
            sync_interval_minutes=sync_interval_minutes,
            full_resync_interval_hours=full_resync_interval_hours,
            username=username,
            password=password,
        )
    except ValueError as exc:
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="settings.html",
            context={
                "servers": repository.list_servers(),
                "error": str(exc),
            },
            status_code=422,
        )
    session.commit()
    return RedirectResponse("/settings", status_code=303)
```

Add an edit endpoint:

```python
@router.post("/settings/nas/{nas_id}")
def update_nas(
    request: Request,
    nas_id: int,
    name: str = Form(...),
    host: str = Form(...),
    port: int = Form(..., ge=1, le=65535),
    use_https: bool = Form(False),
    enabled: bool = Form(False),
    sync_interval_minutes: int = Form(..., ge=1),
    full_resync_interval_hours: int = Form(..., ge=1),
    username: str = Form(...),
    password: str = Form(""),
    session: Session = Depends(get_session),
):
    repository = NasRepository(session)
    try:
        repository.update_server(
            nas_id,
            name=name,
            base_url=normalize_base_url(host, use_https),
            port=port,
            use_https=use_https,
            enabled=enabled,
            sync_interval_minutes=sync_interval_minutes,
            full_resync_interval_hours=full_resync_interval_hours,
            username=username,
            password=password,
        )
    except (LookupError, ValueError) as exc:
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="settings.html",
            context={
                "servers": repository.list_servers(),
                "error": str(exc),
            },
            status_code=422,
        )
    session.commit()
    return RedirectResponse("/settings", status_code=303)
```

Update connection testing to accept `nas_id` and use `NasRepository.connection_for_indexer()`.

- [ ] **Step 4: Update settings template**

Modify `src/nas_index/web/templates/settings.html` so it renders:

```html
{% extends "base.html" %}
{% block content %}
<section class="panel">
  <h1>NAS 管理</h1>
  {% if error %}
    <p class="message message-error">{{ error }}</p>
  {% endif %}

  <h2>已配置 NAS</h2>
  {% for server in servers %}
    <form method="post" action="/settings/nas/{{ server.id }}" class="settings-form nas-card">
      <input name="name" value="{{ server.name }}" required>
      <input name="host" value="{{ server.base_url }}" required>
      <input name="port" type="number" min="1" max="65535" value="{{ server.port }}" required>
      <label><input name="use_https" type="checkbox" {% if server.use_https %}checked{% endif %}> HTTPS</label>
      <label><input name="enabled" type="checkbox" {% if server.enabled %}checked{% endif %}> 启用同步</label>
      <input name="sync_interval_minutes" type="number" min="1" value="{{ server.sync_interval_minutes }}" required>
      <input name="full_resync_interval_hours" type="number" min="1" value="{{ server.full_resync_interval_hours }}" required>
      <input name="username" autocomplete="username" required>
      <input name="password" type="password" autocomplete="new-password" placeholder="留空保留原密码">
      <button type="submit">保存</button>
      <button
        type="submit"
        formaction="/settings/nas/{{ server.id }}/test"
        formmethod="post"
      >测试连接</button>
    </form>
  {% endfor %}

  <h2>新增 NAS</h2>
  <form method="post" action="/settings/nas" class="settings-form">
    <input name="name" placeholder="名称" required>
    <input name="host" placeholder="主机或地址" required>
    <input name="port" type="number" min="1" max="65535" value="8080" required>
    <label><input name="use_https" type="checkbox"> HTTPS</label>
    <label><input name="enabled" type="checkbox" checked> 启用同步</label>
    <input name="sync_interval_minutes" type="number" min="1" value="30" required>
    <input name="full_resync_interval_hours" type="number" min="1" value="24" required>
    <input name="username" placeholder="索引账号" autocomplete="username" required>
    <input name="password" type="password" placeholder="索引账号密码" autocomplete="new-password" required>
    <button type="submit">新增</button>
  </form>
</section>
{% endblock %}
```

Preserve existing visual classes that are still referenced in `app.css`.

- [ ] **Step 5: Update dashboard and scan status templates**

Change scan forms to include `nas_id` for each server:

```html
{% for server in servers %}
  <form method="post" action="/scans">
    <input type="hidden" name="nas_id" value="{{ server.id }}">
    <button type="submit">同步 {{ server.name }}</button>
  </form>
{% endfor %}
```

Update `dashboard.py` to pass `servers`:

```python
servers = NasRepository(session).list_servers()
```

- [ ] **Step 6: Run settings and dashboard tests**

Run:

```bash
uv run pytest tests/integration/test_settings.py tests/integration/test_dashboard.py -q
```

Expected: PASS after updating assertions to look for NAS management text and per-NAS scan controls.

- [ ] **Step 7: Run full tests and commit**

Run:

```bash
uv run pytest
```

Expected: PASS.

Commit:

```bash
git add src/nas_index/web/routes/settings.py src/nas_index/web/routes/dashboard.py src/nas_index/web/templates/settings.html src/nas_index/web/templates/dashboard.html src/nas_index/web/templates/partials/connection_result.html src/nas_index/web/templates/partials/scan_status.html tests/integration/test_settings.py tests/integration/test_dashboard.py
git commit -m "feat: add multi-nas admin settings"
```

---

## Task 9: Add Due-Share Scheduling, Incremental Child Replacement, and Docs

**Files:**
- Modify: `src/nas_index/services/sync_manager.py`
- Modify: `src/nas_index/services/scanner.py`
- Modify: `src/nas_index/repositories/entries.py`
- Modify: `src/nas_index/repositories/syncs.py`
- Modify: `src/nas_index/web/app.py`
- Modify: `README.md`
- Test: `tests/unit/test_sync_manager.py`
- Test: `tests/unit/test_scanner.py`
- Test: `tests/integration/test_complete_scan.py`

- [ ] **Step 1: Add failing direct-child replacement scanner test**

Add to `tests/unit/test_scanner.py`:

```python
def test_successful_directory_sync_deletes_missing_direct_children(database):
    with Session(database) as session:
        nas_id = NasRepository(session).create_server(
            name="Office",
            base_url="http://nas.local",
            port=8080,
            use_https=False,
            enabled=True,
            sync_interval_minutes=30,
            full_resync_interval_hours=24,
            username="indexer",
            password="secret",
        ).id
        EntryRepository(session).upsert_batch(
            nas_id,
            [
                IndexedItem("old.txt", "/Public/old.txt", "/Public", "file", 1, None, "/Public"),
            ],
            generation=1,
        )
        session.commit()

    client = FakeClient(
        shares=[
            IndexedItem("Public", "/Public", "/", "directory", None, None, "/Public"),
        ],
        children={
            "/Public": [
                IndexedItem("new.txt", "/Public/new.txt", "/Public", "file", 1, None, "/Public"),
            ],
        },
    )
    scanner = Scanner(
        engine=database,
        nas_id=nas_id,
        client_factory=lambda: client,
        page_size=100,
        batch_size=100,
        concurrency=1,
    )

    asyncio.run(scanner.run())

    with Session(database) as session:
        paths = [
            entry.full_path
            for entry in EntryRepository(session).list_children(
                nas_id,
                "/Public",
                allowed_share_paths=("/Public",),
                page=1,
                page_size=100,
            ).items
        ]

    assert paths == ["/Public/new.txt"]
```

- [ ] **Step 2: Run scanner test to verify it fails**

Run:

```bash
uv run pytest tests/unit/test_scanner.py::test_successful_directory_sync_deletes_missing_direct_children -q
```

Expected: FAIL because the scanner does not call `replace_children()` per directory.

- [ ] **Step 3: Make scanner replace children after successful directory listing**

Modify `DirectoryItems` in `src/nas_index/services/scanner.py`:

```python
@dataclass(frozen=True)
class DirectoryItems:
    path: str
    items: list[IndexedItem]
```

After processing a `DirectoryItems` result in `_scan_directories()`, call:

```python
self._replace_children(
    result.path,
    {item.full_path for item in result.items},
)
```

Add helper:

```python
def _replace_children(
    self,
    parent_path: str,
    observed_full_paths: set[str],
) -> None:
    with Session(self.engine) as session:
        EntryRepository(session).replace_children(
            self.nas_id,
            parent_path,
            observed_full_paths,
        )
        session.commit()
```

Keep the existing failed-run behavior: only call `_replace_children()` for directories whose listing completed successfully.

- [ ] **Step 4: Add scheduler loop**

Modify `src/nas_index/services/sync_manager.py`:

```python
from collections.abc import Callable
from datetime import UTC, datetime
from sqlalchemy.orm import Session

from nas_index.repositories.syncs import SyncRepository


class SyncManager:
    def __init__(
        self,
        scanner_factory: Callable[[int], Any],
        *,
        session_factory: Callable[[], Session] | None = None,
        poll_seconds: float = 10.0,
    ):
        self.scanner_factory = scanner_factory
        self.session_factory = session_factory
        self.poll_seconds = poll_seconds
        self._tasks: dict[int, asyncio.Task] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._scheduler_task: asyncio.Task | None = None

    def start_scheduler(self) -> None:
        if self.session_factory is None:
            raise RuntimeError("session_factory is required for scheduling")
        if self._scheduler_task is None or self._scheduler_task.done():
            self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    async def stop_scheduler(self) -> None:
        if self._scheduler_task is not None:
            self._scheduler_task.cancel()
            await asyncio.gather(self._scheduler_task, return_exceptions=True)

    async def _scheduler_loop(self) -> None:
        while True:
            self.start_due_syncs()
            await asyncio.sleep(self.poll_seconds)

    def start_due_syncs(self) -> None:
        if self.session_factory is None:
            return
        with self.session_factory() as session:
            due = SyncRepository(session).due_share_states(datetime.now(UTC))
        for state in due:
            if not self.is_running(state.nas_id):
                self.start_nas(state.nas_id)
```

- [ ] **Step 5: Wire scheduler lifecycle**

Modify `src/nas_index/web/app.py` when creating `SyncManager`:

```python
app.state.sync_manager = SyncManager(
    scanner_factory,
    session_factory=session_factory,
    poll_seconds=settings.sync_scheduler_poll_seconds,
)
```

Inside lifespan:

```python
_app.state.sync_manager.start_scheduler()
try:
    yield
finally:
    await _app.state.sync_manager.stop_scheduler()
    engine.dispose()
```

- [ ] **Step 6: Update README**

Modify `README.md` to include:

```markdown
## 多 NAS 与权限

管理员在 `/settings` 中添加一个或多个 NAS。每个 NAS 使用一个只读索引账号同步本地文件名索引。

普通用户从 `/access` 选择 NAS 并输入自己的 NAS 账号密码。程序只临时使用该账号读取可访问共享文件夹列表，不会把用户密码写入 SQLite。浏览和搜索只返回该用户可访问共享文件夹下的本地索引数据。

## 同步

程序启动后会按 NAS 的同步间隔调度同步任务，也可以在概览页手动触发单个 NAS 同步。同步失败时保留旧索引，避免因为网络、权限或 NAS API 错误误删本地记录。

QNAP File Station 没有作为第一版依赖的可靠文件变化回调。本程序以定时增量同步为准；后续接入 QNAP Notification Center 或 Qmiix 时，可以把事件作为提前触发同步的信号。
```

- [ ] **Step 7: Run targeted scheduling and scanner tests**

Run:

```bash
uv run pytest tests/unit/test_sync_manager.py tests/unit/test_scanner.py tests/integration/test_complete_scan.py -q
```

Expected: PASS.

- [ ] **Step 8: Run full test suite**

Run:

```bash
uv run pytest
```

Expected: PASS with the existing Starlette deprecation warning only.

- [ ] **Step 9: Commit**

Commit:

```bash
git add src/nas_index/services/sync_manager.py src/nas_index/services/scanner.py src/nas_index/repositories/entries.py src/nas_index/repositories/syncs.py src/nas_index/web/app.py README.md tests/unit/test_sync_manager.py tests/unit/test_scanner.py tests/integration/test_complete_scan.py
git commit -m "feat: schedule incremental NAS sync"
```

---

## Final Verification

- [ ] **Step 1: Run all tests**

Run:

```bash
uv run pytest
```

Expected: PASS with the existing Starlette deprecation warning only.

- [ ] **Step 2: Start the app**

Run:

```bash
uv run uvicorn nas_index.web.app:app --host 127.0.0.1 --port 8000 --reload
```

Expected: Uvicorn reports `http://127.0.0.1:8000`.

- [ ] **Step 3: Check health**

Run:

```bash
curl -fsS http://127.0.0.1:8000/health
```

Expected:

```json
{"status":"ok"}
```

- [ ] **Step 4: Manual smoke test**

Use the browser:

1. Open `/settings`.
2. Add a NAS with indexing credentials.
3. Test the indexing connection.
4. Trigger manual sync for that NAS.
5. Open `/access`.
6. Log in with a NAS user that can see one shared folder.
7. Open `/browse` and confirm only that share is visible.
8. Search for a known filename and confirm results are limited to that share.

---

## Self-Review Checklist

- Spec coverage:
  - Multi-NAS schema: Task 1 and Task 2.
  - Admin indexing credentials: Task 2 and Task 8.
  - NAS-scoped entries and FTS filtering: Task 3.
  - User credentials not stored and share permission discovery: Task 4 and Task 7.
  - Scheduled incremental sync: Task 5, Task 6, and Task 9.
  - Failure behavior preserves old entries: Task 5 and Task 9 tests.
  - UI updates: Task 7 and Task 8.
  - Documentation: Task 9.
- Placeholder scan:
  - No empty implementation markers.
  - Each task has exact files, commands, expected results, and commit commands.
- Type consistency:
  - `NasServerValue`, `NasCredentialValue`, `IndexedItem.share_path`, and `UserAccess` are defined before use.
  - Repository method signatures are introduced before route and scanner tasks call them.
  - `SyncRepository` and `SyncManager` are introduced before app lifecycle wiring.
