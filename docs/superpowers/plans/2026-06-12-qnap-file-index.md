# QNAP File Index Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local FastAPI application that indexes all QTS 5.2.9 shares visible to one read-only account, stores metadata in SQLite, and provides read-only browsing and filename search.

**Architecture:** A QNAP adapter authenticates through `authLogin.cgi` and reads File Station `get_tree`/`get_list` endpoints. A scan service performs iterative, paginated traversal and generation-based SQLite upserts, while server-rendered FastAPI pages expose settings, progress, lazy browsing, and FTS5-backed search.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, HTMX, SQLAlchemy 2, SQLite/FTS5, HTTPX, Pydantic Settings, Pytest

---

## Source References

- Design: `docs/superpowers/specs/2026-06-12-qnap-file-index-design.md`
- QNAP authentication API: `https://eu1.qnap.com/dev/QTS_HTTP_API-Authentication_v5.1.0.pdf`
- QNAP File Station API: `https://download.qnap.com/dev/QNAP_QTS_File_Station_API_v5.pdf`

The QNAP implementation must use these documented contracts:

- Login: `GET /cgi-bin/authLogin.cgi` with `user`, Base64-encoded `pwd`, and `remme=0`; parse XML `authPassed` and `authSid`.
- Logout: `GET /cgi-bin/authLogout.cgi` with `sid`.
- Shares: `GET /cgi-bin/filemanager/utilRequest.cgi?func=get_tree&node=share_root`.
- Children: `GET /cgi-bin/filemanager/utilRequest.cgi?func=get_list&list_mode=all&path=...&start=...&limit=...&v=1`.
- File rows use `filename`, `isfolder`, `filesize`, and `epochmt`.
- File Station status `4` means permission denied and status `17` means authentication failure.

## File Structure

```text
pyproject.toml                         Project metadata, dependencies, test settings
.gitignore                             Local database, cache, and environment exclusions
src/nas_index/__init__.py              Package marker
src/nas_index/config.py                Process-level paths and scan tuning
src/nas_index/db.py                    Engine/session creation and SQLite initialization
src/nas_index/models.py                SQLAlchemy tables
src/nas_index/types.py                 Shared immutable domain values
src/nas_index/repositories/config.py   Single-NAS configuration persistence
src/nas_index/repositories/entries.py  Entry upsert, browse, counts, cleanup, search
src/nas_index/repositories/scans.py    Scan lifecycle and error persistence
src/nas_index/qnap/client.py           QTS authentication and File Station reads
src/nas_index/qnap/errors.py           Typed QNAP failures and safe user messages
src/nas_index/services/scanner.py      Full traversal and generation orchestration
src/nas_index/services/scan_manager.py In-process background task and single-scan lock
src/nas_index/web/__init__.py           Web package marker
src/nas_index/web/app.py               FastAPI factory and lifespan recovery
src/nas_index/web/dependencies.py      Request-scoped session and service access
src/nas_index/web/routes/dashboard.py  Dashboard and scan status partial
src/nas_index/web/routes/settings.py   Settings save and connection test
src/nas_index/web/routes/browse.py     Lazy tree and directory listing
src/nas_index/web/routes/search.py     Paginated name search
src/nas_index/web/routes/scans.py      Manual scan start
src/nas_index/web/templates/*.html     Base, pages, and HTMX partials
src/nas_index/web/static/app.css       Responsive local UI styling
tests/conftest.py                      Temporary SQLite and application fixtures
tests/unit/*.py                        Repository, parser, and service tests
tests/integration/*.py                 Web and complete-scan tests
README.md                              Local setup and real-NAS acceptance steps
```

### Task 1: Bootstrap the Application and SQLite Runtime

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `src/nas_index/__init__.py`
- Create: `src/nas_index/config.py`
- Create: `src/nas_index/db.py`
- Create: `src/nas_index/web/__init__.py`
- Create: `src/nas_index/web/app.py`
- Create: `tests/conftest.py`
- Create: `tests/integration/test_health.py`

- [ ] **Step 1: Write the failing application smoke test**

```python
# tests/integration/test_health.py
def test_health_endpoint(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

```python
# tests/conftest.py
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nas_index.config import AppSettings
from nas_index.web.app import create_app


@pytest.fixture
def settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        log_dir=tmp_path / "logs",
    )


@pytest.fixture
def client(settings: AppSettings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client
```

- [ ] **Step 2: Run the smoke test and verify import failure**

Run: `uv run pytest tests/integration/test_health.py -v`

Expected: FAIL because `nas_index.config` and `nas_index.web.app` do not exist.

- [ ] **Step 3: Create project metadata and minimal runtime**

```toml
# pyproject.toml
[project]
name = "qnap-file-index"
version = "0.1.0"
description = "Local read-only QNAP file index and search"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115,<1",
  "httpx>=0.28,<1",
  "jinja2>=3.1,<4",
  "pydantic-settings>=2.7,<3",
  "python-multipart>=0.0.20,<1",
  "sqlalchemy>=2.0,<3",
  "uvicorn[standard]>=0.34,<1",
]

[dependency-groups]
dev = [
  "pytest>=8.3,<9",
  "pytest-asyncio>=0.25,<1",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/nas_index"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
asyncio_mode = "auto"
```

```gitignore
# .gitignore
.venv/
__pycache__/
.pytest_cache/
*.pyc
data/
logs/
*.db
*.db-shm
*.db-wal
```

```python
# src/nas_index/config.py
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    database_url: str = "sqlite:///data/nas-index.db"
    log_dir: Path = Path("logs")
    scan_page_size: int = 500
    scan_batch_size: int = 100
    qnap_timeout_seconds: float = 20.0
    qnap_retry_attempts: int = 3

    model_config = SettingsConfigDict(
        env_prefix="NAS_INDEX_",
        env_file=".env",
        extra="ignore",
    )
```

```python
# src/nas_index/db.py
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker


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
```

```python
# src/nas_index/web/app.py
from contextlib import asynccontextmanager

from fastapi import FastAPI

from nas_index.config import AppSettings
from nas_index.db import create_database_engine, create_session_factory


def create_app(settings: AppSettings | None = None) -> FastAPI:
    settings = settings or AppSettings()
    engine = create_database_engine(settings.database_url)
    session_factory = create_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        engine.dispose()

    app = FastAPI(title="QNAP File Index", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
```

- [ ] **Step 4: Install dependencies and run the smoke test**

Run: `uv sync && uv run pytest tests/integration/test_health.py -v`

Expected: PASS.

- [ ] **Step 5: Commit the project bootstrap**

```bash
git add pyproject.toml .gitignore src tests
git commit -m "chore: bootstrap FastAPI application"
```

### Task 2: Create the Relational Schema and FTS5 Index

**Files:**
- Create: `src/nas_index/models.py`
- Modify: `src/nas_index/db.py`
- Modify: `src/nas_index/web/app.py`
- Create: `tests/unit/test_database.py`

- [ ] **Step 1: Write failing schema and FTS synchronization tests**

```python
# tests/unit/test_database.py
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
                text("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
            )
        }

    assert mode == "wal"
    assert {"nas_config", "entries", "scan_runs", "scan_errors", "entry_search"} <= tables


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
        assert connection.execute(
            text("SELECT count(*) FROM entry_search WHERE entry_search MATCH 'report'")
        ).scalar_one() == 1
        connection.execute(
            text("UPDATE entries SET name = 'budget.pdf' WHERE full_path = '/Public/report.pdf'")
        )
        assert connection.execute(
            text("SELECT count(*) FROM entry_search WHERE entry_search MATCH 'budget'")
        ).scalar_one() == 1
        connection.execute(text("DELETE FROM entries"))
        assert connection.execute(text("SELECT count(*) FROM entry_search")).scalar_one() == 0
```

- [ ] **Step 2: Run the schema tests and verify failure**

Run: `uv run pytest tests/unit/test_database.py -v`

Expected: FAIL because the models and `init_database` do not exist.

- [ ] **Step 3: Define tables and deterministic initialization**

```python
# src/nas_index/models.py
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


class Entry(Base):
    __tablename__ = "entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
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
        UniqueConstraint("full_path", name="uq_entries_full_path"),
        CheckConstraint("entry_type IN ('file', 'directory')", name="entry_type_values"),
        Index("ix_entries_parent_path", "parent_path"),
        Index("ix_entries_entry_type", "entry_type"),
        Index("ix_entries_generation", "scan_generation"),
    )


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
        back_populates="scan_run", cascade="all, delete-orphan"
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
    scan_run_id: Mapped[int] = mapped_column(ForeignKey("scan_runs.id", ondelete="CASCADE"))
    path: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    scan_run: Mapped[ScanRun] = relationship(back_populates="errors")
```

Add this initializer to `src/nas_index/db.py`:

```python
from sqlalchemy import Engine

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


def init_database(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        for statement in FTS_DDL:
            connection.exec_driver_sql(statement)
```

Call `init_database(engine)` immediately after engine creation in `create_app`.

- [ ] **Step 4: Add the shared database fixture and run tests**

Add to `tests/conftest.py`:

```python
from nas_index.db import create_database_engine, init_database


@pytest.fixture
def database(tmp_path: Path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'unit.db'}")
    init_database(engine)
    yield engine
    engine.dispose()
```

Run: `uv run pytest tests/unit/test_database.py tests/integration/test_health.py -v`

Expected: PASS.

- [ ] **Step 5: Commit the database schema**

```bash
git add src/nas_index/models.py src/nas_index/db.py src/nas_index/web/app.py tests
git commit -m "feat: add SQLite schema and FTS index"
```

### Task 3: Persist and Validate the Single NAS Configuration

**Files:**
- Create: `src/nas_index/types.py`
- Create: `src/nas_index/repositories/__init__.py`
- Create: `src/nas_index/repositories/config.py`
- Create: `tests/unit/test_config_repository.py`

- [ ] **Step 1: Write failing configuration repository tests**

```python
# tests/unit/test_config_repository.py
from sqlalchemy.orm import Session

from nas_index.repositories.config import ConfigRepository
from nas_index.types import NasConnection


def test_save_and_load_single_configuration(database):
    connection = NasConnection(
        base_url="https://192.168.1.20",
        port=443,
        use_https=True,
        username="indexer",
        password="secret",
    )
    with Session(database) as session:
        repository = ConfigRepository(session)
        repository.save(connection)
        session.commit()
        saved = repository.get()

    assert saved == connection
    assert saved.endpoint == "https://192.168.1.20:443"


def test_blank_password_preserves_saved_password(database):
    with Session(database) as session:
        repository = ConfigRepository(session)
        repository.save(
            NasConnection("http://nas.local", 8080, False, "indexer", "secret")
        )
        repository.save(
            NasConnection("http://nas.local", 8080, False, "indexer", "")
        )
        session.commit()

        assert repository.get().password == "secret"
```

- [ ] **Step 2: Run repository tests and verify failure**

Run: `uv run pytest tests/unit/test_config_repository.py -v`

Expected: FAIL because `NasConnection` and `ConfigRepository` do not exist.

- [ ] **Step 3: Implement the immutable connection type and repository**

```python
# src/nas_index/types.py
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
class IndexedItem:
    name: str
    full_path: str
    parent_path: str
    entry_type: str
    size_bytes: int | None
    modified_at: datetime | None
```

```python
# src/nas_index/repositories/config.py
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from nas_index.models import NasConfig
from nas_index.types import NasConnection


class ConfigRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self) -> NasConnection | None:
        row = self.session.get(NasConfig, 1)
        if row is None:
            return None
        return NasConnection(
            base_url=row.base_url,
            port=row.port,
            use_https=row.use_https,
            username=row.username,
            password=row.password,
        )

    def save(self, value: NasConnection) -> NasConnection:
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
        return self.get_pending(row)

    @staticmethod
    def get_pending(row: NasConfig) -> NasConnection:
        return NasConnection(
            row.base_url, row.port, row.use_https, row.username, row.password
        )
```

- [ ] **Step 4: Run configuration tests**

Run: `uv run pytest tests/unit/test_config_repository.py -v`

Expected: PASS.

- [ ] **Step 5: Commit configuration persistence**

```bash
git add src/nas_index/types.py src/nas_index/repositories tests/unit/test_config_repository.py
git commit -m "feat: persist single NAS configuration"
```

### Task 4: Implement QTS Authentication and Safe Error Mapping

**Files:**
- Create: `src/nas_index/qnap/__init__.py`
- Create: `src/nas_index/qnap/errors.py`
- Create: `src/nas_index/qnap/client.py`
- Create: `tests/unit/test_qnap_auth.py`

- [ ] **Step 1: Write failing authentication, two-step rejection, and logout tests**

```python
# tests/unit/test_qnap_auth.py
import base64

import httpx
import pytest

from nas_index.qnap.client import QnapClient
from nas_index.qnap.errors import QnapAuthenticationError, QnapTwoStepRequired
from nas_index.types import NasConnection


CONNECTION = NasConnection("http://nas.local", 8080, False, "indexer", "päss")


@pytest.mark.asyncio
async def test_login_base64_encodes_password_and_logout_uses_sid():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("authLogin.cgi"):
            return httpx.Response(
                200,
                text="<QDocRoot><authPassed>1</authPassed><authSid>abc123</authSid></QDocRoot>",
            )
        return httpx.Response(200, text="<QDocRoot><authPassed>0</authPassed></QDocRoot>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = QnapClient(CONNECTION, http=http)
        await client.login()
        await client.logout()

    assert requests[0].url.params["pwd"] == base64.b64encode("päss".encode()).decode()
    assert requests[0].url.params["remme"] == "0"
    assert requests[1].url.params["sid"] == "abc123"
    assert "päss" not in str(requests[0].url)


@pytest.mark.asyncio
async def test_login_rejects_two_step_account():
    response = "<QDocRoot><authPassed>0</authPassed><need_2sv>1</need_2sv></QDocRoot>"
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, text=response))

    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(QnapTwoStepRequired, match="两步验证"):
            await QnapClient(CONNECTION, http=http).login()


@pytest.mark.asyncio
async def test_login_maps_invalid_credentials():
    response = "<QDocRoot><authPassed>0</authPassed><errorValue>-1</errorValue></QDocRoot>"
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, text=response))

    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(QnapAuthenticationError, match="用户名或密码"):
            await QnapClient(CONNECTION, http=http).login()
```

- [ ] **Step 2: Run authentication tests and verify failure**

Run: `uv run pytest tests/unit/test_qnap_auth.py -v`

Expected: FAIL because the QNAP client does not exist.

- [ ] **Step 3: Implement authentication with typed sanitized exceptions**

```python
# src/nas_index/qnap/errors.py
class QnapError(Exception):
    user_message = "NAS 请求失败"

    def __str__(self) -> str:
        return self.user_message


class QnapConnectionError(QnapError):
    user_message = "无法连接 NAS，请检查地址、端口和网络"


class QnapAuthenticationError(QnapError):
    user_message = "NAS 用户名或密码错误"


class QnapTwoStepRequired(QnapError):
    user_message = "此账号启用了两步验证，请改用未启用两步验证的只读账号"


class QnapPermissionError(QnapError):
    user_message = "NAS 账号没有读取该目录的权限"


class QnapProtocolError(QnapError):
    user_message = "NAS 返回了无法识别的数据"
```

```python
# src/nas_index/qnap/client.py
import base64
from xml.etree import ElementTree

import httpx

from nas_index.qnap.errors import (
    QnapAuthenticationError,
    QnapConnectionError,
    QnapProtocolError,
    QnapTwoStepRequired,
)
from nas_index.types import NasConnection


class QnapClient:
    def __init__(
        self,
        connection: NasConnection,
        *,
        http: httpx.AsyncClient | None = None,
        timeout_seconds: float = 20.0,
        retry_attempts: int = 3,
    ):
        self.connection = connection
        self.http = http or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_http = http is None
        self.retry_attempts = max(1, min(retry_attempts, 3))
        self.sid: str | None = None

    async def login(self) -> str:
        encoded = base64.b64encode(self.connection.password.encode("utf-8")).decode("ascii")
        try:
            response = await self.http.get(
                f"{self.connection.endpoint}/cgi-bin/authLogin.cgi",
                params={
                    "user": self.connection.username,
                    "pwd": encoded,
                    "remme": 0,
                    "serviceKey": 1,
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise QnapConnectionError() from exc

        try:
            root = ElementTree.fromstring(response.text)
        except ElementTree.ParseError as exc:
            raise QnapProtocolError() from exc

        if root.findtext("need_2sv") == "1":
            raise QnapTwoStepRequired()
        if root.findtext("authPassed") != "1":
            raise QnapAuthenticationError()

        sid = root.findtext("authSid")
        if not sid:
            raise QnapProtocolError()
        self.sid = sid
        return sid

    async def logout(self) -> None:
        if self.sid:
            try:
                await self.http.get(
                    f"{self.connection.endpoint}/cgi-bin/authLogout.cgi",
                    params={"sid": self.sid},
                )
            finally:
                self.sid = None
        if self._owns_http:
            await self.http.aclose()

    async def __aenter__(self) -> "QnapClient":
        await self.login()
        return self

    async def __aexit__(self, *_exc_info) -> None:
        await self.logout()
```

- [ ] **Step 4: Run authentication tests**

Run: `uv run pytest tests/unit/test_qnap_auth.py -v`

Expected: PASS and no assertion/log output contains the plaintext password or SID.

- [ ] **Step 5: Commit QTS authentication**

```bash
git add src/nas_index/qnap tests/unit/test_qnap_auth.py
git commit -m "feat: add QTS authentication client"
```

### Task 5: Parse Shares and Paginate Directory Children

**Files:**
- Modify: `src/nas_index/qnap/client.py`
- Modify: `src/nas_index/types.py`
- Create: `tests/unit/test_qnap_listing.py`

- [ ] **Step 1: Write failing share, path, pagination, and status tests**

```python
# tests/unit/test_qnap_listing.py
from datetime import UTC, datetime

import httpx
import pytest

from nas_index.qnap.client import QnapClient
from nas_index.qnap.errors import QnapAuthenticationError, QnapPermissionError
from nas_index.types import NasConnection


@pytest.mark.asyncio
async def test_lists_readable_shares_and_skips_non_folder_nodes():
    payload = [
        {"text": "Public", "id": "/Public", "iconCls": "folder", "cls": "r"},
        {"text": "Archive", "id": "/Archive/", "iconCls": "folder", "cls": "w"},
        {"text": "DVD", "id": "/DVD", "iconCls": "odd", "cls": "r"},
    ]
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=payload))
    async with httpx.AsyncClient(transport=transport) as http:
        client = QnapClient(
            NasConnection("http://nas", 8080, False, "u", "p"), http=http
        )
        client.sid = "sid"
        shares = await client.list_shares()

    assert [item.full_path for item in shares] == ["/Public", "/Archive"]


@pytest.mark.asyncio
async def test_iter_children_follows_total_and_normalizes_metadata():
    starts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params["start"])
        starts.append(start)
        rows = (
            [{"filename": "子目录", "isfolder": 1, "filesize": "4096", "epochmt": 10}]
            if start == 0
            else [{"filename": "a.txt", "isfolder": 0, "filesize": "12", "epochmt": 20}]
        )
        return httpx.Response(200, json={"total": 2, "datas": rows})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = QnapClient(
            NasConnection("http://nas", 8080, False, "u", "p"), http=http
        )
        client.sid = "sid"
        items = [item async for item in client.iter_children("/Public", page_size=1)]

    assert starts == [0, 1]
    assert items[0].full_path == "/Public/子目录"
    assert items[0].entry_type == "directory"
    assert items[1].size_bytes == 12
    assert items[1].modified_at == datetime.fromtimestamp(20, UTC)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "exception"),
    [(4, QnapPermissionError), (17, QnapAuthenticationError)],
)
async def test_listing_maps_qnap_status(status, exception):
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, json={"status": status, "success": "true"})
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = QnapClient(
            NasConnection("http://nas", 8080, False, "u", "p"), http=http
        )
        client.sid = "sid"
        with pytest.raises(exception):
            _ = [item async for item in client.iter_children("/Public", page_size=100)]


@pytest.mark.asyncio
async def test_listing_retries_transient_connection_failure(monkeypatch):
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ConnectError("temporary", request=request)
        return httpx.Response(200, json=[])

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr("nas_index.qnap.client.asyncio.sleep", no_sleep)
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = QnapClient(
            NasConnection("http://nas", 8080, False, "u", "p"),
            http=http,
            retry_attempts=3,
        )
        client.sid = "sid"
        assert await client.list_shares() == []

    assert attempts == 3
```

- [ ] **Step 2: Run listing tests and verify failure**

Run: `uv run pytest tests/unit/test_qnap_listing.py -v`

Expected: FAIL because share and child listing methods do not exist.

- [ ] **Step 3: Implement canonical paths, status mapping, and pagination**

Add to `src/nas_index/qnap/client.py`:

```python
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import AsyncIterator

from nas_index.qnap.errors import QnapAuthenticationError, QnapPermissionError
from nas_index.types import IndexedItem


def canonical_path(value: str) -> str:
    parts = [part for part in PurePosixPath(value.replace("\\", "/")).parts if part != "/"]
    return "/" + "/".join(parts)


def join_path(parent: str, name: str) -> str:
    return canonical_path(f"{parent}/{name}")


def _raise_for_qnap_status(payload: object) -> None:
    if not isinstance(payload, dict):
        return
    status = payload.get("status")
    if status == 4:
        raise QnapPermissionError()
    if status == 17:
        raise QnapAuthenticationError()
    if status not in {None, 0, 1}:
        raise QnapProtocolError()
```

Insert these methods at class indentation inside `QnapClient`:

```python
    async def list_shares(self) -> list[IndexedItem]:
        payload = await self._file_station_request(
            {"func": "get_tree", "node": "share_root", "is_iso": 0, "hidden_file": 0}
        )
        if not isinstance(payload, list):
            raise QnapProtocolError()
        return [
            IndexedItem(
                name=str(row["text"]),
                full_path=canonical_path(str(row["id"])),
                parent_path="/",
                entry_type="directory",
                size_bytes=None,
                modified_at=None,
            )
            for row in payload
            if row.get("iconCls") == "folder" and row.get("cls") in {"r", "w"}
        ]

    async def iter_children(
        self, path: str, *, page_size: int
    ) -> AsyncIterator[IndexedItem]:
        start = 0
        while True:
            payload = await self._file_station_request(
            {
                "func": "get_list",
                "is_iso": 0,
                "list_mode": "all",
                "path": canonical_path(path),
                "dir": "ASC",
                "limit": page_size,
                "sort": "filename",
                "start": start,
                "hidden_file": 1,
                "v": 1,
            }
        )
            if not isinstance(payload, dict) or not isinstance(payload.get("datas"), list):
                raise QnapProtocolError()
            rows = payload["datas"]
            for row in rows:
                is_directory = int(row.get("isfolder", 0)) == 1
                epoch = int(row.get("epochmt") or 0)
                yield IndexedItem(
                    name=str(row["filename"]),
                    full_path=join_path(path, str(row["filename"])),
                    parent_path=canonical_path(path),
                    entry_type="directory" if is_directory else "file",
                    size_bytes=None if is_directory else int(row.get("filesize") or 0),
                    modified_at=datetime.fromtimestamp(epoch, UTC) if epoch else None,
                )
            start += len(rows)
            if not rows or start >= int(payload.get("total", start)):
                break

    async def _file_station_request(self, params: dict[str, object]) -> object:
        if not self.sid:
            raise QnapAuthenticationError()
        response = await self._request_with_retry(
            f"{self.connection.endpoint}/cgi-bin/filemanager/utilRequest.cgi",
            {**params, "sid": self.sid},
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise QnapProtocolError() from exc
        _raise_for_qnap_status(payload)
        return payload
```

- [ ] **Step 4: Add bounded transport retries and run all QNAP tests**

Wrap only `httpx.TimeoutException`, `httpx.ConnectError`, and HTTP 502/503/504 in a three-attempt exponential backoff helper. Do not retry QNAP status 4 or 17.

```python
import asyncio


async def _request_with_retry(self, url: str, params: dict[str, object]) -> httpx.Response:
    delays = (0.0, 0.25, 0.75)[: self.retry_attempts]
    for attempt, delay in enumerate(delays, start=1):
        if delay:
            await asyncio.sleep(delay)
        try:
            response = await self.http.get(url, params=params)
            if response.status_code not in {502, 503, 504}:
                response.raise_for_status()
                return response
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            if attempt == len(delays):
                raise QnapConnectionError() from exc
        except httpx.HTTPStatusError as exc:
            raise QnapConnectionError() from exc
    raise QnapConnectionError()
```

Replace the direct login request with:

```python
response = await self._request_with_retry(
    f"{self.connection.endpoint}/cgi-bin/authLogin.cgi",
    {
        "user": self.connection.username,
        "pwd": encoded,
        "remme": 0,
        "serviceKey": 1,
    },
)
```

Run: `uv run pytest tests/unit/test_qnap_auth.py tests/unit/test_qnap_listing.py -v`

Expected: PASS.

- [ ] **Step 5: Commit File Station listing support**

```bash
git add src/nas_index/qnap src/nas_index/types.py tests
git commit -m "feat: list QNAP shares and directory contents"
```

### Task 6: Implement Entry Upserts, Browsing, Cleanup, and Search

**Files:**
- Create: `src/nas_index/repositories/entries.py`
- Create: `tests/unit/test_entry_repository.py`
- Create: `tests/unit/test_search_repository.py`

- [ ] **Step 1: Write failing upsert, browse, stale cleanup, and search tests**

```python
# tests/unit/test_entry_repository.py
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from nas_index.repositories.entries import EntryRepository
from nas_index.types import IndexedItem


def item(name, path, parent, kind="file", size=1):
    return IndexedItem(name, path, parent, kind, size, datetime(2026, 1, 1, tzinfo=UTC))


def test_upsert_updates_metadata_and_generation(database):
    with Session(database) as session:
        repository = EntryRepository(session)
        repository.upsert_batch([item("a.txt", "/Public/a.txt", "/Public", size=1)], 1)
        session.commit()
        repository.upsert_batch([item("a.txt", "/Public/a.txt", "/Public", size=9)], 2)
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
                item("docs", "/Public/docs", "/Public", "directory", None),
                item("a.txt", "/Public/a.txt", "/Public"),
            ],
            1,
        )
        session.commit()
        page = repository.list_children("/Public", page=1, page_size=2)

    assert [entry.name for entry in page.items] == ["docs", "a.txt"]
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
        repository.upsert_batch([item("new", "/Public/new", "/Public")], 2)
        removed = repository.delete_stale(2)
        session.commit()

    assert removed == 1


def test_page_for_entry_locates_selected_row(database):
    with Session(database) as session:
        repository = EntryRepository(session)
        repository.upsert_batch(
            [
                item(f"{number:03}.txt", f"/Public/{number:03}.txt", "/Public")
                for number in range(125)
            ],
            1,
        )
        session.commit()
        selected = repository.get_by_path("/Public/120.txt")

        assert repository.page_for_entry(selected.id, page_size=50) == 3
```

```python
# tests/unit/test_search_repository.py
from sqlalchemy.orm import Session

from nas_index.repositories.entries import EntryRepository


def test_search_matches_unicode_substring_and_returns_full_path(database, seeded_entries):
    with Session(database) as session:
        result = EntryRepository(session).search("年度项目", page=1, page_size=20)

    assert result.total == 1
    assert result.items[0].full_path == "/Public/年度项目计划.docx"


def test_short_search_falls_back_to_safe_like(database, seeded_entries):
    with Session(database) as session:
        result = EntryRepository(session).search("项", page=1, page_size=20)

    assert result.total == 1


def test_empty_search_returns_no_rows(database, seeded_entries):
    with Session(database) as session:
        result = EntryRepository(session).search("   ", page=1, page_size=20)

    assert result.total == 0
```

- [ ] **Step 2: Run repository tests and verify failure**

Run: `uv run pytest tests/unit/test_entry_repository.py tests/unit/test_search_repository.py -v`

Expected: FAIL because `EntryRepository` does not exist.

- [ ] **Step 3: Implement batch upsert and browse operations**

```python
# src/nas_index/repositories/entries.py
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Generic, TypeVar

from sqlalchemy import case, delete, func, select, text
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from nas_index.models import Entry
from nas_index.types import IndexedItem

T = TypeVar("T")


@dataclass(frozen=True)
class Page(Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class EntryRepository:
    def __init__(self, session: Session):
        self.session = session

    def upsert_batch(self, items: list[IndexedItem], generation: int) -> None:
        if not items:
            return
        now = datetime.now(UTC)
        values = [
            {
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
                index_elements=[Entry.full_path],
                set_={
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

    def get_by_path(self, full_path: str) -> Entry | None:
        return self.session.scalar(select(Entry).where(Entry.full_path == full_path))

    def list_children(self, parent_path: str, *, page: int, page_size: int) -> Page[Entry]:
        predicate = Entry.parent_path == parent_path
        total = self.session.scalar(select(func.count()).select_from(Entry).where(predicate)) or 0
        rows = list(
            self.session.scalars(
                select(Entry)
                .where(predicate)
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

    def list_child_directories(self, parent_path: str) -> list[Entry]:
        return list(
            self.session.scalars(
                select(Entry)
                .where(
                    Entry.parent_path == parent_path,
                    Entry.entry_type == "directory",
                )
                .order_by(func.lower(Entry.name))
            )
        )

    def delete_stale(self, generation: int) -> int:
        result = self.session.execute(
            delete(Entry).where(Entry.scan_generation < generation)
        )
        return result.rowcount or 0

    def counts(self) -> tuple[int, int]:
        rows = dict(
            self.session.execute(
                select(Entry.entry_type, func.count()).group_by(Entry.entry_type)
            )
        )
        return int(rows.get("file", 0)), int(rows.get("directory", 0))

    def page_for_entry(self, entry_id: int, *, page_size: int) -> int | None:
        selected = self.session.get(Entry, entry_id)
        if selected is None:
            return None
        order = (
            case((Entry.entry_type == "directory", 0), else_=1),
            func.lower(Entry.name),
            Entry.id,
        )
        ranked = (
            select(
                Entry.id,
                func.row_number().over(order_by=order).label("position"),
            )
            .where(Entry.parent_path == selected.parent_path)
            .subquery()
        )
        position = self.session.scalar(
            select(ranked.c.position).where(ranked.c.id == entry_id)
        )
        return ((int(position) - 1) // page_size) + 1 if position else None
```

- [ ] **Step 4: Implement trigram search with a short-query fallback**

Add to `EntryRepository`:

```python
    def search(self, query: str, *, page: int, page_size: int) -> Page[Entry]:
        query = query.strip()
        if not query:
            return Page([], 0, page, page_size)

        offset = (page - 1) * page_size
        if len(query) < 3:
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            predicate = Entry.name.ilike(f"%{escaped}%", escape="\\")
            total = self.session.scalar(
                select(func.count()).select_from(Entry).where(predicate)
            ) or 0
            rows = list(
                self.session.scalars(
                    select(Entry)
                    .where(predicate)
                    .order_by(
                        case((Entry.entry_type == "directory", 0), else_=1),
                        func.lower(Entry.name),
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
            WHERE entry_search MATCH :query
            """
        )
        rows_sql = text(
            """
            SELECT e.*
            FROM entry_search s
            JOIN entries e ON e.id = s.rowid
            WHERE entry_search MATCH :query
            ORDER BY bm25(entry_search),
                     CASE WHEN e.entry_type = 'directory' THEN 0 ELSE 1 END,
                     lower(e.name)
            LIMIT :limit OFFSET :offset
            """
        )
        total = int(self.session.execute(count_sql, {"query": match_query}).scalar_one())
        rows = list(
            self.session.scalars(
                select(Entry).from_statement(rows_sql),
                {"query": match_query, "limit": page_size, "offset": offset},
            )
        )
        return Page(rows, total, page, page_size)
```

Add these fixtures to `tests/conftest.py`:

```python
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from nas_index.repositories.entries import EntryRepository
from nas_index.types import IndexedItem


def seed_entries(engine) -> None:
    with Session(engine) as session:
        EntryRepository(session).upsert_batch(
            [
                IndexedItem("Public", "/Public", "/", "directory", None, None),
                IndexedItem(
                    "年度项目计划.docx",
                    "/Public/年度项目计划.docx",
                    "/Public",
                    "file",
                    128,
                    datetime(2026, 1, 1, tzinfo=UTC),
                ),
                IndexedItem("资料", "/Public/资料", "/Public", "directory", None, None),
                IndexedItem(
                    "nested-only.txt",
                    "/Public/资料/nested-only.txt",
                    "/Public/资料",
                    "file",
                    8,
                    datetime(2026, 1, 1, tzinfo=UTC),
                ),
            ],
            generation=1,
        )
        session.commit()


@pytest.fixture
def seeded_entries(database):
    seed_entries(database)


@pytest.fixture
def web_seeded_entries(client):
    seed_entries(client.app.state.engine)
```

Run: `uv run pytest tests/unit/test_entry_repository.py tests/unit/test_search_repository.py -v`

Expected: PASS.

- [ ] **Step 5: Commit the index repository**

```bash
git add src/nas_index/repositories/entries.py tests/unit
git commit -m "feat: add index browsing and name search"
```

### Task 7: Build the Generation-Based Scan Service

**Files:**
- Create: `src/nas_index/repositories/scans.py`
- Create: `src/nas_index/services/__init__.py`
- Create: `src/nas_index/services/scanner.py`
- Create: `tests/unit/test_scanner.py`

- [ ] **Step 1: Write failing successful-scan and failed-scan tests**

```python
# tests/unit/test_scanner.py
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nas_index.models import Entry, ScanRun
from nas_index.qnap.errors import QnapPermissionError
from nas_index.services.scanner import Scanner
from nas_index.types import IndexedItem


def directory(name, path, parent):
    return IndexedItem(name, path, parent, "directory", None, datetime.now(UTC))


def file(name, path, parent):
    return IndexedItem(name, path, parent, "file", 4, datetime.now(UTC))


class FakeQnap:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def list_shares(self):
        return [directory("Public", "/Public", "/")]

    async def iter_children(self, path, *, page_size):
        rows = {
            "/Public": [directory("docs", "/Public/docs", "/Public")],
            "/Public/docs": [file("a.txt", "/Public/docs/a.txt", "/Public/docs")],
        }[path]
        for row in rows:
            yield row


@pytest.mark.asyncio
async def test_successful_scan_indexes_tree_and_deletes_stale(database):
    with Session(database) as session:
        session.add(
            Entry(
                name="stale.txt",
                full_path="/Public/stale.txt",
                parent_path="/Public",
                entry_type="file",
                size_bytes=1,
                modified_at=None,
                scan_generation=0,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        session.commit()

    await Scanner(database, lambda: FakeQnap(), page_size=100, batch_size=2).run()

    with Session(database) as session:
        paths = set(session.scalars(select(Entry.full_path)))
        scan = session.scalar(select(ScanRun).order_by(ScanRun.id.desc()))

    assert paths == {"/Public", "/Public/docs", "/Public/docs/a.txt"}
    assert scan.status == "succeeded"
    assert scan.processed_entries == 3


@pytest.mark.asyncio
async def test_failed_directory_preserves_stale_rows(database):
    class FailingQnap(FakeQnap):
        async def iter_children(self, path, *, page_size):
            if path == "/Public":
                raise QnapPermissionError()
            yield

    with Session(database) as session:
        session.add(
            Entry(
                name="old.txt",
                full_path="/Public/old.txt",
                parent_path="/Public",
                entry_type="file",
                size_bytes=1,
                modified_at=None,
                scan_generation=0,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )
        session.commit()

    await Scanner(database, lambda: FailingQnap(), 100, 100).run()

    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(Entry)) == 2
        assert session.scalar(select(ScanRun).order_by(ScanRun.id.desc())).status == "failed"
```

- [ ] **Step 2: Run scanner tests and verify failure**

Run: `uv run pytest tests/unit/test_scanner.py -v`

Expected: FAIL because scan repositories and service do not exist.

- [ ] **Step 3: Implement persistent scan lifecycle operations**

```python
# src/nas_index/repositories/scans.py
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from nas_index.models import ScanError, ScanRun


class ScanRepository:
    def __init__(self, session: Session):
        self.session = session

    def create(self) -> ScanRun:
        generation = (self.session.scalar(select(func.max(ScanRun.generation))) or 0) + 1
        run = ScanRun(
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
        run = self.session.get(ScanRun, run_id)
        run.processed_entries = processed
        run.current_path = current_path

    def succeed(self, run_id: int, processed: int) -> None:
        run = self.session.get(ScanRun, run_id)
        run.status = "succeeded"
        run.processed_entries = processed
        run.finished_at = datetime.now(UTC)

    def fail(self, run_id: int, path: str, reason: str, processed: int) -> None:
        run = self.session.get(ScanRun, run_id)
        run.status = "failed"
        run.processed_entries = processed
        run.error_summary = reason
        run.finished_at = datetime.now(UTC)
        self.session.add(
            ScanError(
                scan_run_id=run_id,
                path=path,
                reason=reason,
                created_at=datetime.now(UTC),
            )
        )

    def latest(self) -> ScanRun | None:
        return self.session.scalar(select(ScanRun).order_by(ScanRun.id.desc()).limit(1))

    def last_successful(self) -> ScanRun | None:
        return self.session.scalar(
            select(ScanRun)
            .where(ScanRun.status == "succeeded")
            .order_by(ScanRun.finished_at.desc())
            .limit(1)
        )

    def interrupt_running(self) -> int:
        result = self.session.execute(
            update(ScanRun)
            .where(ScanRun.status == "running")
            .values(status="interrupted", finished_at=datetime.now(UTC))
        )
        return result.rowcount or 0
```

- [ ] **Step 4: Implement iterative traversal with short transactions**

```python
# src/nas_index/services/scanner.py
from collections import deque
from collections.abc import Callable

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from nas_index.qnap.errors import QnapError
from nas_index.repositories.entries import EntryRepository
from nas_index.repositories.scans import ScanRepository
from nas_index.types import IndexedItem


class Scanner:
    def __init__(
        self,
        engine: Engine,
        client_factory: Callable,
        page_size: int,
        batch_size: int,
    ):
        self.engine = engine
        self.client_factory = client_factory
        self.page_size = page_size
        self.batch_size = batch_size

    async def run(self) -> int:
        with Session(self.engine) as session:
            run = ScanRepository(session).create()
            session.commit()
            run_id, generation = run.id, run.generation

        processed = 0
        current_path = "/"
        batch: list[IndexedItem] = []
        try:
            async with self.client_factory() as client:
                shares = await client.list_shares()
                self._write_batch(shares, generation)
                processed += len(shares)
                queue = deque(item.full_path for item in shares)

                while queue:
                    current_path = queue.popleft()
                    async for item in client.iter_children(
                        current_path, page_size=self.page_size
                    ):
                        batch.append(item)
                        processed += 1
                        if item.entry_type == "directory":
                            queue.append(item.full_path)
                        if len(batch) >= self.batch_size:
                            self._write_batch(batch, generation)
                            batch.clear()
                            self._progress(run_id, processed, current_path)

                self._write_batch(batch, generation)

            with Session(self.engine) as session:
                EntryRepository(session).delete_stale(generation)
                ScanRepository(session).succeed(run_id, processed)
                session.commit()
            return run_id
        except Exception as exc:
            reason = str(exc) if isinstance(exc, QnapError) else "扫描任务异常中断"
            with Session(self.engine) as session:
                ScanRepository(session).fail(run_id, current_path, reason, processed)
                session.commit()
            return run_id

    def _write_batch(self, batch: list[IndexedItem], generation: int) -> None:
        if not batch:
            return
        with Session(self.engine) as session:
            EntryRepository(session).upsert_batch(batch, generation)
            session.commit()

    def _progress(self, run_id: int, processed: int, path: str) -> None:
        with Session(self.engine) as session:
            ScanRepository(session).progress(run_id, processed, path)
            session.commit()
```

Run: `uv run pytest tests/unit/test_scanner.py -v`

Expected: PASS, including preservation of stale data on failure.

- [ ] **Step 5: Commit the scanner**

```bash
git add src/nas_index/repositories/scans.py src/nas_index/services tests/unit/test_scanner.py
git commit -m "feat: add generation-based NAS scanner"
```

### Task 8: Add Settings, Connection Test, and Dashboard Pages

**Files:**
- Create: `src/nas_index/web/dependencies.py`
- Create: `src/nas_index/web/routes/__init__.py`
- Create: `src/nas_index/web/routes/settings.py`
- Create: `src/nas_index/web/routes/dashboard.py`
- Modify: `src/nas_index/web/app.py`
- Create: `src/nas_index/web/templates/base.html`
- Create: `src/nas_index/web/templates/dashboard.html`
- Create: `src/nas_index/web/templates/settings.html`
- Create: `src/nas_index/web/templates/partials/connection_result.html`
- Create: `tests/integration/test_settings.py`
- Create: `tests/integration/test_dashboard.py`

- [ ] **Step 1: Write failing settings and dashboard route tests**

```python
# tests/integration/test_settings.py
def test_save_settings_and_preserve_blank_password(client):
    response = client.post(
        "/settings",
        data={
            "host": "nas.local",
            "port": "8080",
            "use_https": "",
            "username": "indexer",
            "password": "secret",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    response = client.post(
        "/settings",
        data={
            "host": "nas.local",
            "port": "8080",
            "use_https": "",
            "username": "indexer",
            "password": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get("/settings")
    assert "secret" not in page.text
    assert "密码已保存" in page.text


def test_connection_test_returns_sanitized_error(client, monkeypatch):
    async def fail(_connection):
        raise RuntimeError("secret-token")

    monkeypatch.setattr("nas_index.web.routes.settings.test_connection", fail)
    response = client.post("/settings/test")

    assert response.status_code == 200
    assert "连接测试失败" in response.text
    assert "secret-token" not in response.text
```

```python
# tests/integration/test_dashboard.py
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from nas_index.models import ScanRun


def test_dashboard_displays_counts(client, web_seeded_entries):
    response = client.get("/")

    assert response.status_code == 200
    assert "文件" in response.text
    assert "文件夹" in response.text


def test_dashboard_displays_last_successful_scan(client):
    finished = datetime(2026, 6, 12, 8, 30, tzinfo=UTC)
    with Session(client.app.state.engine) as session:
        session.add(
            ScanRun(
                generation=1,
                status="succeeded",
                started_at=finished,
                finished_at=finished,
                processed_entries=42,
                current_path="/Public",
                error_summary=None,
            )
        )
        session.commit()

    response = client.get("/")

    assert "最后成功扫描" in response.text
    assert "2026-06-12" in response.text
```

- [ ] **Step 2: Run web tests and verify 404 failures**

Run: `uv run pytest tests/integration/test_settings.py tests/integration/test_dashboard.py -v`

Expected: FAIL because the routes and templates do not exist.

- [ ] **Step 3: Add request dependencies and settings routes**

```python
# src/nas_index/web/dependencies.py
from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session
```

```python
# src/nas_index/web/routes/settings.py
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from nas_index.qnap.client import QnapClient
from nas_index.qnap.errors import QnapError
from nas_index.repositories.config import ConfigRepository
from nas_index.types import NasConnection
from nas_index.web.dependencies import get_session

router = APIRouter()


def normalize_base_url(host: str, use_https: bool) -> str:
    host = host.strip().rstrip("/")
    parsed = urlsplit(host if "://" in host else f"//{host}")
    hostname = parsed.hostname or host
    return f"{'https' if use_https else 'http'}://{hostname}"


async def test_connection(connection: NasConnection) -> int:
    async with QnapClient(connection) as client:
        return len(await client.list_shares())


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, session: Session = Depends(get_session)):
    config = ConfigRepository(session).get()
    return request.app.state.templates.TemplateResponse(
        request, "settings.html", {"config": config}
    )


@router.post("/settings")
def save_settings(
    host: str = Form(...),
    port: int = Form(..., ge=1, le=65535),
    use_https: bool = Form(False),
    username: str = Form(...),
    password: str = Form(""),
    session: Session = Depends(get_session),
):
    ConfigRepository(session).save(
        NasConnection(
            normalize_base_url(host, use_https),
            port,
            use_https,
            username,
            password,
        )
    )
    session.commit()
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/test", response_class=HTMLResponse)
async def connection_test(request: Request, session: Session = Depends(get_session)):
    config = ConfigRepository(session).get()
    if config is None:
        context = {"success": False, "message": "请先保存 NAS 设置"}
    else:
        try:
            share_count = await test_connection(config)
            context = {"success": True, "message": f"连接成功，可访问 {share_count} 个共享目录"}
        except QnapError as exc:
            context = {"success": False, "message": str(exc)}
        except Exception:
            context = {"success": False, "message": "连接测试失败"}
    return request.app.state.templates.TemplateResponse(
        request, "partials/connection_result.html", context
    )
```

- [ ] **Step 4: Add dashboard routes, templates, and app registration**

```python
# src/nas_index/web/routes/dashboard.py
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from nas_index.repositories.entries import EntryRepository
from nas_index.repositories.scans import ScanRepository
from nas_index.web.dependencies import get_session

router = APIRouter()


@router.get("/", response_class=HTMLResponse, name="dashboard")
def dashboard(request: Request, session: Session = Depends(get_session)):
    file_count, directory_count = EntryRepository(session).counts()
    scans = ScanRepository(session)
    return request.app.state.templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "file_count": file_count,
            "directory_count": directory_count,
            "scan": scans.latest(),
            "last_successful_scan": scans.last_successful(),
        },
    )
```

Add this setup to `create_app`:

```python
from pathlib import Path

from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from nas_index.web.routes import dashboard, settings as settings_routes

web_dir = Path(__file__).parent
app.state.templates = Jinja2Templates(directory=web_dir / "templates")
app.mount(
    "/static",
    StaticFiles(directory=web_dir / "static", check_dir=False),
    name="static",
)
app.include_router(dashboard.router)
app.include_router(settings_routes.router)
```

```html
<!-- src/nas_index/web/templates/base.html -->
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}QNAP 文件索引{% endblock %}</title>
  <link rel="stylesheet" href="{{ url_for('static', path='/app.css') }}">
  <script src="https://unpkg.com/htmx.org@2.0.4"></script>
</head>
<body>
  <header>
    <a href="/">QNAP 文件索引</a>
    <nav>
      <a href="/">概览</a>
      <a href="/browse">目录</a>
      <a href="/search">搜索</a>
      <a href="/settings">设置</a>
    </nav>
  </header>
  <main>{% block content %}{% endblock %}</main>
</body>
</html>
```

```html
<!-- src/nas_index/web/templates/dashboard.html -->
{% extends "base.html" %}
{% block title %}概览 - QNAP 文件索引{% endblock %}
{% block content %}
<h1>概览</h1>
<section class="cards">
  <article><strong>{{ file_count }}</strong><span>文件</span></article>
  <article><strong>{{ directory_count }}</strong><span>文件夹</span></article>
</section>
<p>
  最后成功扫描：
  {{ last_successful_scan.finished_at if last_successful_scan else "尚无" }}
</p>
<form method="post" action="/scans"><button type="submit">开始扫描</button></form>
<div id="scan-status" hx-get="/scans/status" hx-trigger="load" hx-swap="innerHTML">
  {% if scan %}{{ scan.status }}{% else %}尚未扫描{% endif %}
</div>
{% endblock %}
```

```html
<!-- src/nas_index/web/templates/settings.html -->
{% extends "base.html" %}
{% block title %}设置 - QNAP 文件索引{% endblock %}
{% block content %}
<h1>NAS 设置</h1>
<p class="warning">密码将以明文保存在本机数据库中，请仅在可信设备上运行。</p>
<form method="post" action="/settings">
  <label>主机<input name="host" required value="{{ config.base_url if config else '' }}"></label>
  <label>端口<input name="port" type="number" required value="{{ config.port if config else 8080 }}"></label>
  <label><input name="use_https" type="checkbox" value="true"
    {% if config and config.use_https %}checked{% endif %}> 使用 HTTPS</label>
  <label>用户名<input name="username" required value="{{ config.username if config else '' }}"></label>
  <label>密码<input name="password" type="password" value="" autocomplete="new-password"></label>
  {% if config %}<small>密码已保存；留空表示保持不变。</small>{% endif %}
  <button type="submit">保存设置</button>
  <button type="button" hx-post="/settings/test" hx-target="#connection-result">测试连接</button>
</form>
<div id="connection-result"></div>
{% endblock %}
```

```html
<!-- src/nas_index/web/templates/partials/connection_result.html -->
<p class="{{ 'success' if success else 'error' }}">{{ message }}</p>
```

Run: `uv run pytest tests/integration/test_settings.py tests/integration/test_dashboard.py -v`

Expected: PASS.

- [ ] **Step 5: Commit settings and dashboard**

```bash
git add src/nas_index/web tests/integration
git commit -m "feat: add NAS settings and dashboard"
```

### Task 9: Add Lazy Directory Browsing and Search Pages

**Files:**
- Create: `src/nas_index/web/routes/browse.py`
- Create: `src/nas_index/web/routes/search.py`
- Create: `src/nas_index/web/templates/browse.html`
- Create: `src/nas_index/web/templates/search.html`
- Create: `src/nas_index/web/templates/partials/tree_children.html`
- Modify: `src/nas_index/web/app.py`
- Create: `tests/integration/test_browse.py`
- Create: `tests/integration/test_search.py`

- [ ] **Step 1: Write failing browse and search tests**

```python
# tests/integration/test_browse.py
def test_browse_page_lists_direct_children(client, web_seeded_entries):
    response = client.get("/browse", params={"path": "/Public"})

    assert response.status_code == 200
    assert "年度项目计划.docx" in response.text
    assert "nested-only.txt" not in response.text


def test_tree_endpoint_returns_only_child_directories(client, web_seeded_entries):
    response = client.get("/browse/tree", params={"path": "/Public"})

    assert response.status_code == 200
    assert "资料" in response.text
    assert "年度项目计划.docx" not in response.text
```

```python
# tests/integration/test_search.py
def test_search_page_returns_name_and_full_path(client, web_seeded_entries):
    response = client.get("/search", params={"q": "项目"})

    assert response.status_code == 200
    assert "年度项目计划.docx" in response.text
    assert "/Public/年度项目计划.docx" in response.text


def test_search_result_links_to_parent_and_selected_entry(client, web_seeded_entries):
    response = client.get("/search", params={"q": "项目"})

    assert "/browse?path=" in response.text
    assert "&amp;selected=" in response.text
```

- [ ] **Step 2: Run browse and search tests and verify 404 failures**

Run: `uv run pytest tests/integration/test_browse.py tests/integration/test_search.py -v`

Expected: FAIL because the endpoints do not exist.

- [ ] **Step 3: Implement browse routes with server-side pagination**

```python
# src/nas_index/web/routes/browse.py
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from nas_index.repositories.entries import EntryRepository
from nas_index.web.dependencies import get_session

router = APIRouter(prefix="/browse")


@router.get("", response_class=HTMLResponse)
def browse(
    request: Request,
    path: str = Query("/"),
    selected: int | None = None,
    page: int = Query(1, ge=1),
    session: Session = Depends(get_session),
):
    repository = EntryRepository(session)
    if selected is not None:
        page = repository.page_for_entry(selected, page_size=100) or page
    listing = repository.list_children(path, page=page, page_size=100)
    return request.app.state.templates.TemplateResponse(
        request,
        "browse.html",
        {
            "path": path,
            "selected": selected,
            "listing": listing,
            "root_directories": repository.list_child_directories("/"),
        },
    )


@router.get("/tree", response_class=HTMLResponse)
def tree_children(
    request: Request,
    path: str = Query("/"),
    session: Session = Depends(get_session),
):
    directories = EntryRepository(session).list_child_directories(path)
    return request.app.state.templates.TemplateResponse(
        request,
        "partials/tree_children.html",
        {"directories": directories},
    )
```

- [ ] **Step 4: Implement search routes and result navigation**

```python
# src/nas_index/web/routes/search.py
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from nas_index.repositories.entries import EntryRepository
from nas_index.web.dependencies import get_session

router = APIRouter(prefix="/search")


@router.get("", response_class=HTMLResponse)
def search(
    request: Request,
    q: str = Query(""),
    page: int = Query(1, ge=1),
    session: Session = Depends(get_session),
):
    results = EntryRepository(session).search(q, page=page, page_size=50)
    return request.app.state.templates.TemplateResponse(
        request,
        "search.html",
        {"query": q, "results": results},
    )
```

```html
<!-- src/nas_index/web/templates/partials/tree_children.html -->
<ul>
{% for directory in directories %}
  <li>
    <a href="/browse?path={{ directory.full_path | urlencode }}">{{ directory.name }}</a>
    <button type="button"
            hx-get="/browse/tree?path={{ directory.full_path | urlencode }}"
            hx-target="next .tree-children"
            hx-swap="innerHTML">展开</button>
    <div class="tree-children"></div>
  </li>
{% endfor %}
</ul>
```

```html
<!-- src/nas_index/web/templates/browse.html -->
{% extends "base.html" %}
{% block title %}目录 - QNAP 文件索引{% endblock %}
{% block content %}
<h1>目录浏览</h1>
<div class="browser-layout">
  <aside>
    <ul>
    {% for directory in root_directories %}
      <li>
        <a href="/browse?path={{ directory.full_path | urlencode }}">{{ directory.name }}</a>
        <button type="button"
                hx-get="/browse/tree?path={{ directory.full_path | urlencode }}"
                hx-target="next .tree-children">展开</button>
        <div class="tree-children"></div>
      </li>
    {% endfor %}
    </ul>
  </aside>
  <section>
    <h2>{{ path }}</h2>
    <div class="table-wrap">
      <table>
        <thead><tr><th>名称</th><th>类型</th><th>大小</th><th>修改时间</th></tr></thead>
        <tbody>
        {% for item in listing.items %}
          <tr class="{% if selected == item.id %}selected{% endif %}">
            <td>
              {% if item.entry_type == "directory" %}
                <a href="/browse?path={{ item.full_path | urlencode }}">{{ item.name }}</a>
              {% else %}
                {{ item.name }}
              {% endif %}
            </td>
            <td>{{ "文件夹" if item.entry_type == "directory" else "文件" }}</td>
            <td>{{ item.size_bytes if item.size_bytes is not none else "-" }}</td>
            <td>{{ item.modified_at or "-" }}</td>
          </tr>
        {% endfor %}
        </tbody>
      </table>
    </div>
    <nav class="pagination">
      {% if listing.page > 1 %}
        <a href="/browse?path={{ path | urlencode }}&page={{ listing.page - 1 }}">上一页</a>
      {% endif %}
      {% if listing.page * listing.page_size < listing.total %}
        <a href="/browse?path={{ path | urlencode }}&page={{ listing.page + 1 }}">下一页</a>
      {% endif %}
    </nav>
  </section>
</div>
{% endblock %}
```

```html
<!-- src/nas_index/web/templates/search.html -->
{% extends "base.html" %}
{% block title %}搜索 - QNAP 文件索引{% endblock %}
{% block content %}
<h1>搜索</h1>
<form method="get" action="/search">
  <input name="q" value="{{ query }}" placeholder="文件名或文件夹名">
  <button type="submit">搜索</button>
</form>
{% if query %}
  <p>找到 {{ results.total }} 条结果</p>
  <div class="table-wrap">
    <table>
      <thead><tr><th>名称</th><th>类型</th><th>大小</th><th>修改时间</th><th>完整路径</th></tr></thead>
      <tbody>
      {% for item in results.items %}
        <tr>
          <td>
            <a href="/browse?path={{ item.parent_path | urlencode }}&selected={{ item.id }}">
              {{ item.name }}
            </a>
          </td>
          <td>{{ "文件夹" if item.entry_type == "directory" else "文件" }}</td>
          <td>{{ item.size_bytes if item.size_bytes is not none else "-" }}</td>
          <td>{{ item.modified_at or "-" }}</td>
          <td><code>{{ item.full_path }}</code></td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
  <nav class="pagination">
    {% if results.page > 1 %}
      <a href="/search?q={{ query | urlencode }}&page={{ results.page - 1 }}">上一页</a>
    {% endif %}
    {% if results.page * results.page_size < results.total %}
      <a href="/search?q={{ query | urlencode }}&page={{ results.page + 1 }}">下一页</a>
    {% endif %}
  </nav>
{% endif %}
{% endblock %}
```

The result navigation uses:

```html
<a href="{{ url_for('browse') }}?path={{ item.parent_path | urlencode }}&selected={{ item.id }}">
  {{ item.name }}
</a>
```

Register both routers in `create_app`:

```python
from nas_index.web.routes import browse as browse_routes
from nas_index.web.routes import search as search_routes

app.include_router(browse_routes.router)
app.include_router(search_routes.router)
```

Then run:

Run: `uv run pytest tests/integration/test_browse.py tests/integration/test_search.py -v`

Expected: PASS.

- [ ] **Step 5: Commit browsing and search**

```bash
git add src/nas_index/web tests/integration
git commit -m "feat: add directory browsing and search pages"
```

### Task 10: Run Scans in the Background and Expose Live Progress

**Files:**
- Create: `src/nas_index/services/scan_manager.py`
- Create: `src/nas_index/web/routes/scans.py`
- Create: `src/nas_index/web/templates/partials/scan_status.html`
- Modify: `src/nas_index/web/routes/dashboard.py`
- Modify: `src/nas_index/web/templates/dashboard.html`
- Modify: `src/nas_index/web/app.py`
- Create: `tests/unit/test_scan_manager.py`
- Create: `tests/integration/test_scan_routes.py`

- [ ] **Step 1: Write failing duplicate-start and progress route tests**

```python
# tests/unit/test_scan_manager.py
import asyncio

import pytest

from nas_index.services.scan_manager import ScanAlreadyRunning, ScanManager


@pytest.mark.asyncio
async def test_manager_rejects_second_scan_while_worker_is_active():
    started = asyncio.Event()
    release = asyncio.Event()

    class SlowScanner:
        async def run(self):
            started.set()
            await release.wait()

    manager = ScanManager(lambda: SlowScanner())
    manager.start()
    await started.wait()

    with pytest.raises(ScanAlreadyRunning):
        manager.start()

    release.set()
    await manager.wait()
```

```python
# tests/integration/test_scan_routes.py
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from nas_index.models import ScanRun


@pytest.fixture
def running_scan(client):
    with Session(client.app.state.engine) as session:
        session.add(
            ScanRun(
                generation=1,
                status="running",
                started_at=datetime.now(UTC),
                finished_at=None,
                processed_entries=12,
                current_path="/Public",
                error_summary=None,
            )
        )
        session.commit()


def test_scan_start_requires_saved_configuration(client):
    response = client.post("/scans", follow_redirects=False)

    assert response.status_code == 409
    assert "请先保存 NAS 设置" in response.text


def test_scan_status_partial_polls_while_running(client, running_scan):
    response = client.get("/scans/status")

    assert response.status_code == 200
    assert 'hx-get="/scans/status"' in response.text
    assert "正在扫描" in response.text
```

- [ ] **Step 2: Run scan manager and route tests and verify failure**

Run: `uv run pytest tests/unit/test_scan_manager.py tests/integration/test_scan_routes.py -v`

Expected: FAIL because `ScanManager` and scan endpoints do not exist.

- [ ] **Step 3: Implement one-task background coordination**

```python
# src/nas_index/services/scan_manager.py
import asyncio
from collections.abc import Callable


class ScanAlreadyRunning(RuntimeError):
    pass


class ScanManager:
    def __init__(self, scanner_factory: Callable):
        self.scanner_factory = scanner_factory
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.is_running or self._lock.locked():
            raise ScanAlreadyRunning()
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        async with self._lock:
            await self.scanner_factory().run()

    async def wait(self) -> None:
        if self._task:
            await self._task
```

- [ ] **Step 4: Wire the configured QNAP client, scanner, routes, and polling partial**

Add this wiring to `create_app` after `app.state.session_factory` is assigned:

```python
from sqlalchemy.orm import Session

from nas_index.qnap.client import QnapClient
from nas_index.repositories.config import ConfigRepository
from nas_index.services.scan_manager import ScanManager
from nas_index.services.scanner import Scanner


def scanner_factory() -> Scanner:
    with Session(engine) as session:
        connection = ConfigRepository(session).get()
    if connection is None:
        raise RuntimeError("NAS configuration is missing")

    return Scanner(
        engine=engine,
        client_factory=lambda: QnapClient(
            connection,
            timeout_seconds=settings.qnap_timeout_seconds,
            retry_attempts=settings.qnap_retry_attempts,
        ),
        page_size=settings.scan_page_size,
        batch_size=settings.scan_batch_size,
    )


app.state.scan_manager = ScanManager(scanner_factory)
```

```python
# src/nas_index/web/routes/scans.py
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from nas_index.repositories.config import ConfigRepository
from nas_index.repositories.scans import ScanRepository
from nas_index.services.scan_manager import ScanAlreadyRunning
from nas_index.web.dependencies import get_session

router = APIRouter(prefix="/scans")


@router.post("")
def start_scan(request: Request, session: Session = Depends(get_session)):
    if ConfigRepository(session).get() is None:
        return HTMLResponse("请先保存 NAS 设置", status_code=409)
    try:
        request.app.state.scan_manager.start()
    except ScanAlreadyRunning:
        return HTMLResponse("扫描任务正在运行", status_code=409)
    return RedirectResponse("/", status_code=303)


@router.get("/status", response_class=HTMLResponse)
def scan_status(request: Request, session: Session = Depends(get_session)):
    latest = ScanRepository(session).latest()
    return request.app.state.templates.TemplateResponse(
        request,
        "partials/scan_status.html",
        {"scan": latest},
    )
```

Register the scan router in `create_app`:

```python
from nas_index.web.routes import scans as scan_routes

app.include_router(scan_routes.router)
```

```html
{% if scan and scan.status == "running" %}
<section hx-get="/scans/status" hx-trigger="every 2s" hx-swap="outerHTML">
  <strong>正在扫描</strong>
  <span>{{ scan.processed_entries }} 条</span>
  <code>{{ scan.current_path or "/" }}</code>
</section>
{% elif scan %}
<section>
  <strong>{{ {"succeeded": "扫描完成", "failed": "扫描失败", "interrupted": "扫描中断"}[scan.status] }}</strong>
  <span>{{ scan.processed_entries }} 条</span>
  {% if scan.error_summary %}<p>{{ scan.error_summary }}</p>{% endif %}
</section>
{% else %}
<section>尚未扫描</section>
{% endif %}
```

Run: `uv run pytest tests/unit/test_scan_manager.py tests/integration/test_scan_routes.py -v`

Expected: PASS.

- [ ] **Step 5: Commit background scanning**

```bash
git add src/nas_index/services/scan_manager.py src/nas_index/web tests
git commit -m "feat: add manual background scanning"
```

### Task 11: Add Startup Recovery, Styling, Documentation, and Final Verification

**Files:**
- Modify: `src/nas_index/web/app.py`
- Create: `src/nas_index/logging.py`
- Create: `src/nas_index/web/static/app.css`
- Modify: `src/nas_index/web/templates/base.html`
- Create: `tests/integration/test_recovery.py`
- Create: `tests/integration/test_complete_scan.py`
- Create: `tests/unit/test_redaction.py`
- Create: `README.md`

- [ ] **Step 1: Write failing startup recovery and credential-redaction tests**

```python
# tests/integration/test_recovery.py
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from nas_index.models import ScanRun
from nas_index.web.app import create_app


def test_startup_marks_orphaned_running_scan_interrupted(settings):
    app = create_app(settings)
    with Session(app.state.engine) as session:
        session.add(
            ScanRun(
                generation=1,
                status="running",
                started_at=datetime.now(UTC),
                finished_at=None,
                processed_entries=10,
                current_path="/Public",
                error_summary=None,
            )
        )
        session.commit()

    with TestClient(app):
        pass

    with Session(app.state.engine) as session:
        assert session.query(ScanRun).one().status == "interrupted"
```

```python
# tests/unit/test_redaction.py
import logging

from nas_index.logging import CredentialRedactionFilter


def test_redaction_filter_masks_password_and_sid():
    record = logging.LogRecord(
        "test",
        logging.ERROR,
        __file__,
        1,
        "request password=%s sid=%s",
        ("secret", "abc123"),
        None,
    )
    CredentialRedactionFilter().filter(record)

    assert record.getMessage() == "request password=*** sid=***"
```

```python
# tests/integration/test_complete_scan.py
import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from nas_index.models import Entry, ScanRun
from nas_index.qnap.client import QnapClient
from nas_index.services.scanner import Scanner
from nas_index.types import NasConnection


@pytest.mark.asyncio
async def test_real_qnap_adapter_and_scanner_index_complete_tree(database):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("authLogin.cgi"):
            return httpx.Response(
                200,
                text="<QDocRoot><authPassed>1</authPassed><authSid>sid1</authSid></QDocRoot>",
            )
        if request.url.path.endswith("authLogout.cgi"):
            return httpx.Response(200, text="<QDocRoot />")
        if request.url.params.get("func") == "get_tree":
            return httpx.Response(
                200,
                json=[{"text": "Public", "id": "/Public", "iconCls": "folder", "cls": "r"}],
            )
        if request.url.params.get("path") == "/Public":
            return httpx.Response(
                200,
                json={
                    "total": 1,
                    "datas": [
                        {
                            "filename": "docs",
                            "isfolder": 1,
                            "filesize": "4096",
                            "epochmt": 1,
                        }
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "total": 1,
                "datas": [
                    {
                        "filename": "readme.txt",
                        "isfolder": 0,
                        "filesize": "7",
                        "epochmt": 2,
                    }
                ],
            },
        )

    connection = NasConnection("http://nas.local", 8080, False, "indexer", "secret")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        scanner = Scanner(
            database,
            lambda: QnapClient(connection, http=http),
            page_size=100,
            batch_size=2,
        )
        await scanner.run()

    with Session(database) as session:
        assert set(session.scalars(select(Entry.full_path))) == {
            "/Public",
            "/Public/docs",
            "/Public/docs/readme.txt",
        }
        assert session.scalar(select(ScanRun.status)) == "succeeded"
```

- [ ] **Step 2: Run recovery and redaction tests and verify failure**

Run: `uv run pytest tests/integration/test_recovery.py tests/integration/test_complete_scan.py tests/unit/test_redaction.py -v`

Expected: FAIL because recovery and logging redaction are not implemented.

- [ ] **Step 3: Implement startup recovery and defensive log filtering**

```python
# src/nas_index/logging.py
import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path


class CredentialRedactionFilter(logging.Filter):
    _pattern = re.compile(
        r"(?i)(password|pwd|sid|authSid|qtoken)=([^&\\s]+)"
    )

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = self._pattern.sub(lambda match: f"{match.group(1)}=***", message)
        record.msg = redacted
        record.args = ()
        return True


def configure_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / "nas-index.log",
        maxBytes=5_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.addFilter(CredentialRedactionFilter())
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logger = logging.getLogger("nas_index")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.addHandler(handler)
```

At the start of the FastAPI lifespan:

```python
with session_factory() as session:
    ScanRepository(session).interrupt_running()
    session.commit()
```

Call `configure_logging(settings.log_dir)` in `create_app`. Keep QNAP request logging at
method/path level only; never log request query strings.

- [ ] **Step 4: Finish responsive styling and operational documentation**

```css
/* src/nas_index/web/static/app.css */
:root {
  color-scheme: light;
  font-family: system-ui, sans-serif;
  color: #172033;
  background: #f4f6fa;
}
body { margin: 0; }
header {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  padding: 1rem 2rem;
  background: #16213b;
}
header a { color: white; text-decoration: none; }
nav { display: flex; gap: 1rem; flex-wrap: wrap; }
main { max-width: 1200px; margin: 0 auto; padding: 2rem; }
.cards { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1rem; }
.cards article, section, aside {
  background: white;
  border: 1px solid #dce2ec;
  border-radius: .6rem;
  padding: 1rem;
}
.cards strong { display: block; font-size: 2rem; }
.browser-layout { display: grid; grid-template-columns: 280px 1fr; gap: 1rem; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: .7rem; border-bottom: 1px solid #e4e8ef; text-align: left; }
tr.selected { background: #fff3bf; }
label { display: block; margin: .8rem 0; }
input { max-width: 32rem; width: 100%; padding: .55rem; box-sizing: border-box; }
button { padding: .55rem .9rem; cursor: pointer; }
.warning { border-left: .3rem solid #d97706; padding: .8rem; background: #fff7ed; }
.success { color: #166534; }
.error { color: #b91c1c; }
.pagination { margin-top: 1rem; }
code { overflow-wrap: anywhere; }
@media (max-width: 800px) {
  header { padding: 1rem; align-items: flex-start; }
  main { padding: 1rem; }
  .browser-layout, .cards { grid-template-columns: 1fr; }
}
```

````markdown
<!-- README.md -->
# QNAP File Index

本机只读索引 QTS 5.2.9 中的共享目录，并提供目录浏览和名称搜索。

## 启动

```bash
uv sync
uv run uvicorn nas_index.web.app:app --reload
```

打开 `http://127.0.0.1:8000/settings`，填写 NAS 主机、端口、HTTP/HTTPS、
只读账号和密码，然后先执行“测试连接”，再返回概览页开始扫描。

## QTS 账号

新建专用账号，授予需要索引的全部共享目录只读权限，并关闭该账号的两步验证。
网页中保存的密码会以明文写入本机 SQLite，请仅在可信设备上运行。

## 数据与日志

- 默认数据库：`data/nas-index.db`
- 数据库覆盖：`NAS_INDEX_DATABASE_URL=sqlite:////absolute/path/index.db`
- 默认日志目录：`logs`
- 日志目录覆盖：`NAS_INDEX_LOG_DIR=/absolute/path/logs`

首次扫描 10 万至 100 万条记录可能耗时较长。扫描失败或程序中断时会保留旧索引；
只有完整成功的扫描才会删除已不存在的旧记录。

## 真实 NAS 验收

1. 保存 QTS 5.2.9 设置并通过连接测试。
2. 确认所有只读账号可见共享目录都出现在目录页。
3. 在 NAS 新增文件、修改文件时间并删除文件，然后再次扫描。
4. 确认新增项出现、修改时间更新、删除项消失。
5. 临时撤销一个子目录的读取权限并扫描，确认任务失败且旧索引仍然保留。
6. 恢复权限后重新扫描，确认任务成功。

## 测试

```bash
uv run pytest
```
````

- [ ] **Step 5: Run the full verification suite and commit**

Run:

```bash
uv run pytest -v
uv run python -m compileall -q src
uv run python -c "from nas_index.web.app import create_app; print(create_app().title)"
```

Expected:

- All tests PASS.
- `compileall` exits 0.
- Final command prints `QNAP File Index`.

Then run the app and verify the known local target with the in-app Browser:

```bash
uv run uvicorn nas_index.web.app:app --host 127.0.0.1 --port 8000
```

Browser checks:

1. `/` renders dashboard statistics and scan state.
2. `/settings` masks the saved password and shows the plaintext warning.
3. `/browse?path=/Public` shows only direct children.
4. `/search?q=项目` shows matching names and full paths.
5. Starting a scan updates `/scans/status` without a full page reload.

Commit:

```bash
git add src tests README.md
git commit -m "docs: finish recovery and local operations"
```

## Completion Checklist

- [ ] Every accessible share returned by `get_tree` is inserted as a root directory.
- [ ] Every directory uses paginated `get_list` traversal.
- [ ] Canonical paths have a leading slash and no trailing slash.
- [ ] A successful scan removes entries from older generations.
- [ ] A failed or interrupted scan never performs stale cleanup.
- [ ] Only one background scan can run.
- [ ] Startup changes orphaned `running` scans to `interrupted`.
- [ ] Settings preserve a blank password and never render the saved password.
- [ ] QNAP passwords, SIDs, and qtokens do not appear in logs or user errors.
- [ ] Directory listing and search use server-side pagination.
- [ ] Search supports Unicode substring matches and short-query fallback.
- [ ] The UI contains no NAS write action.
- [ ] Full automated suite, compile check, and browser smoke checks pass.
