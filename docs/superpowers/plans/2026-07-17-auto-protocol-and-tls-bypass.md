# NAS 自动协议探测与 TLS 忽略 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让设置页不再手动选择 HTTP/HTTPS，而是自动探测协议，并支持每台 NAS 单独配置“忽略 HTTPS 证书校验”。

**Architecture:** 这次改造分成三层：先把 `skip_tls_verify` 落到数据库和连接值对象，再把 QNAP client 增强为可控 TLS 校验与自动协议探测，最后收口到设置页表单、测试凭证和保存逻辑。运行时链路继续统一依赖 `NasConnection`，这样扫描、权限校验、缩略图和下载都会自动复用新配置。

**Tech Stack:** Python 3.12+, FastAPI, Jinja2, SQLAlchemy 2, SQLite, httpx, pytest.

## Global Constraints

- 设置页不再要求管理员手动选择 HTTP 或 HTTPS。
- 管理员只填写主机、端口、索引账号和密码，服务端在测试连接时自动探测可用协议。
- 针对内网自签名证书场景，每台 NAS 额外提供“忽略 HTTPS 证书校验”开关。
- 自动探测成功后，将最终协议写回 `use_https`，供后续同步、缩略图、权限验证和下载复用。
- 连接相关字段改动后，清空测试结果并要求重新测试。
- 本次不实现运行时自动重试另一种协议。
- 本次不新增复杂证书管理界面，只提供单个忽略校验开关。

---

## Scope Check

这份计划只覆盖一个子系统：NAS 设置和连接链路。数据库、QNAP client、设置页和测试会一起改，但都服务于同一个交付目标，不需要拆分成多份实施计划。

## File Structure

- Modify `src/nas_index/models.py`
  - 为 `nas_servers` 增加 `skip_tls_verify` 字段。
- Modify `src/nas_index/types.py`
  - 为 `NasConnection` 和 `NasServerValue` 增加 `skip_tls_verify`，保持运行时统一传递。
- Modify `src/nas_index/repositories/nas.py`
  - 创建、更新和读取 NAS 时带上 `skip_tls_verify`。
- Modify `src/nas_index/repositories/config.py`
  - 单 NAS 兼容读取和保存时补齐 `skip_tls_verify=False`。
- Modify `src/nas_index/db.py`
  - 为现有 SQLite 库补齐 `nas_servers.skip_tls_verify` 列。
- Modify `src/nas_index/qnap/errors.py`
  - 新增 TLS 校验失败的业务错误类型。
- Modify `src/nas_index/qnap/client.py`
  - 根据 `skip_tls_verify` 配置 `httpx.AsyncClient`，并新增自动协议探测辅助函数。
- Modify `src/nas_index/web/routes/settings.py`
  - 去掉手填 `use_https`，新增自动探测与 `skip_tls_verify` 参数。
- Modify `src/nas_index/services/connection_tests.py`
  - 测试凭证指纹增加 `skip_tls_verify`，并绑定最终探测出的协议。
- Modify `src/nas_index/web/templates/settings.html`
  - 删除 `HTTPS` 复选框，新增“忽略 HTTPS 证书校验”开关与成功协议提示。
- Modify `tests/unit/test_multi_nas_schema.py`
  - 覆盖新列创建和旧库迁移。
- Modify `tests/unit/test_nas_repository.py`
  - 覆盖 `skip_tls_verify` 的创建与更新。
- Modify `tests/unit/test_config_repository.py`
  - 覆盖单 NAS 兼容读取默认值。
- Modify `tests/unit/test_connection_test_store.py`
  - 覆盖指纹包含 `skip_tls_verify`。
- Modify `tests/unit/test_qnap_auth.py`
  - 覆盖 TLS verify 行为和探测顺序。
- Modify `tests/integration/test_settings.py`
  - 覆盖自动探测协议、HTTPS 复选框移除、忽略证书校验开关与保存行为。

### Task 1: Persist `skip_tls_verify` in schema and value objects

**Files:**
- Modify: `tests/unit/test_multi_nas_schema.py`
- Modify: `tests/unit/test_nas_repository.py`
- Modify: `tests/unit/test_config_repository.py`
- Modify: `tests/unit/test_connection_test_store.py`
- Modify: `src/nas_index/models.py`
- Modify: `src/nas_index/types.py`
- Modify: `src/nas_index/repositories/nas.py`
- Modify: `src/nas_index/repositories/config.py`
- Modify: `src/nas_index/services/connection_tests.py`
- Modify: `src/nas_index/db.py`

**Interfaces:**
- Consumes: existing `NasServer`, `NasServerValue`, `NasConnection`, `ConnectionTestStore`
- Produces:
  - `NasConnection(base_url: str, port: int, use_https: bool, username: str, password: str, skip_tls_verify: bool = False)`
  - `NasServerValue(..., use_https: bool, skip_tls_verify: bool, enabled: bool, sync_interval_minutes: int)`
  - `NasRepository.create_server(..., skip_tls_verify: bool, ...) -> NasServerValue`
  - `NasRepository.update_server(..., skip_tls_verify: bool, ...) -> NasServerValue`

- [ ] **Step 1: Write failing persistence and migration tests**

Add these assertions:

```python
def test_init_database_creates_skip_tls_verify_column(tmp_path):
    engine = create_database_engine(
        f"sqlite:///{tmp_path / 'schema.db'}"
    )
    try:
        init_database(engine)
        columns = {
            column["name"]
            for column in inspect(engine).get_columns("nas_servers")
        }
        assert "skip_tls_verify" in columns
    finally:
        engine.dispose()


def test_create_and_update_nas_server_persists_skip_tls_verify(database):
    with Session(database) as session:
        repository = NasRepository(session)
        server = repository.create_server(
            name="Office NAS",
            base_url="https://nas.example.com",
            port=5001,
            use_https=True,
            skip_tls_verify=True,
            enabled=True,
            sync_interval_minutes=15,
            username="indexer",
            password="secret",
        )
        session.commit()

    with Session(database) as session:
        loaded = NasRepository(session).get_server(server.id)
        assert loaded is not None
        assert loaded.skip_tls_verify is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/unit/test_multi_nas_schema.py tests/unit/test_nas_repository.py tests/unit/test_config_repository.py tests/unit/test_connection_test_store.py -q
```

Expected: FAIL because `skip_tls_verify` does not exist on the model, repository, or connection fingerprint.

- [ ] **Step 3: Implement the schema and value-object changes**

Update the core types and models:

```python
@dataclass(frozen=True, slots=True)
class NasConnection:
    base_url: str
    port: int
    use_https: bool
    username: str
    password: str
    skip_tls_verify: bool = False
```

```python
class NasServer(Base):
    __tablename__ = "nas_servers"
    # ...
    use_https: Mapped[bool] = mapped_column(Boolean)
    skip_tls_verify: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )
```

Add SQLite backfill:

```python
if "skip_tls_verify" not in columns:
    connection.exec_driver_sql(
        "ALTER TABLE nas_servers "
        "ADD COLUMN skip_tls_verify BOOLEAN NOT NULL DEFAULT 0"
    )
```

Update repository signatures:

```python
def create_server(
    self,
    *,
    name: str,
    base_url: str,
    port: int,
    use_https: bool,
    skip_tls_verify: bool,
    enabled: bool,
    sync_interval_minutes: int,
    username: str,
    password: str,
) -> NasServerValue:
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
uv run pytest tests/unit/test_multi_nas_schema.py tests/unit/test_nas_repository.py tests/unit/test_config_repository.py tests/unit/test_connection_test_store.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/nas_index/models.py src/nas_index/types.py src/nas_index/repositories/nas.py src/nas_index/repositories/config.py src/nas_index/services/connection_tests.py src/nas_index/db.py tests/unit/test_multi_nas_schema.py tests/unit/test_nas_repository.py tests/unit/test_config_repository.py tests/unit/test_connection_test_store.py
git commit -m "feat: persist NAS TLS bypass setting"
```

### Task 2: Add TLS-aware QNAP client behavior and protocol probing

**Files:**
- Modify: `tests/unit/test_qnap_auth.py`
- Modify: `src/nas_index/qnap/errors.py`
- Modify: `src/nas_index/qnap/client.py`

**Interfaces:**
- Consumes: `NasConnection.skip_tls_verify`, existing `QnapClient.login()`
- Produces:
  - `class QnapTlsVerificationError(QnapConnectionError)`
  - `def candidate_protocols(port: int) -> tuple[bool, bool]`
  - `@dataclass(frozen=True, slots=True) class QnapProbeResult: connection: NasConnection; share_count: int`
  - `async def probe_qnap_connection(*, host: str, port: int, username: str, password: str, skip_tls_verify: bool, timeout_seconds: float = 20.0, retry_attempts: int = 3) -> QnapProbeResult`

- [ ] **Step 1: Write failing client and probe tests**

Add these tests to `tests/unit/test_qnap_auth.py`:

```python
@pytest.mark.asyncio
async def test_client_disables_tls_verification_when_requested():
    client = QnapClient(
        NasConnection(
            "https://nas.local",
            5001,
            True,
            "indexer",
            "secret",
            True,
        )
    )
    try:
        assert client.http._verify is False
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_probe_qnap_connection_prefers_https_for_port_5001(monkeypatch):
    attempts = []

    async def fake_test_connection(connection, *, timeout_seconds, retry_attempts):
        attempts.append((connection.base_url, connection.use_https))
        if connection.use_https:
            return 4
        raise AssertionError("should not fall back after success")

    monkeypatch.setattr(
        "nas_index.qnap.client._test_qnap_connection",
        fake_test_connection,
    )

    result = await probe_qnap_connection(
        host="192.168.1.16",
        port=5001,
        username="indexer",
        password="secret",
        skip_tls_verify=True,
    )

    assert attempts == [("https://192.168.1.16", True)]
    assert result.connection.skip_tls_verify is True
    assert result.share_count == 4
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/unit/test_qnap_auth.py -q
```

Expected: FAIL because `skip_tls_verify` is unused and `probe_qnap_connection` does not exist.

- [ ] **Step 3: Implement TLS verify control and probing**

Add a specific TLS error:

```python
class QnapTlsVerificationError(QnapConnectionError):
    user_message = (
        "HTTPS 证书校验失败，请改用证书匹配的域名，"
        "或开启忽略 HTTPS 证书校验"
    )
```

Use the flag in the client:

```python
self.http = http or httpx.AsyncClient(
    timeout=timeout_seconds,
    trust_env=False,
    verify=(False if connection.skip_tls_verify else True),
)
```

Map the httpx exception:

```python
except httpx.ConnectError as exc:
    if "CERTIFICATE_VERIFY_FAILED" in str(exc) or "certificate verify failed" in str(exc).lower():
        raise QnapTlsVerificationError() from exc
    if attempt == len(delays):
        raise QnapConnectionError() from exc
```

Add probing helpers:

```python
def candidate_protocols(port: int) -> tuple[bool, bool]:
    return (True, False) if port in {443, 5001} else (False, True)
```

```python
async def probe_qnap_connection(... ) -> QnapProbeResult:
    last_error: QnapError | None = None
    for use_https in candidate_protocols(port):
        connection = NasConnection(
            base_url=("https" if use_https else "http") + f"://{host}",
            port=port,
            use_https=use_https,
            username=username,
            password=password,
            skip_tls_verify=skip_tls_verify,
        )
        try:
            share_count = await _test_qnap_connection(
                connection,
                timeout_seconds=timeout_seconds,
                retry_attempts=retry_attempts,
            )
        except QnapError as exc:
            last_error = exc
            continue
        return QnapProbeResult(connection=connection, share_count=share_count)
    raise last_error or QnapConnectionError()
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
uv run pytest tests/unit/test_qnap_auth.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/nas_index/qnap/errors.py src/nas_index/qnap/client.py tests/unit/test_qnap_auth.py
git commit -m "feat: add QNAP protocol probing and TLS bypass"
```

### Task 3: Switch settings flow to automatic protocol detection

**Files:**
- Modify: `tests/integration/test_settings.py`
- Modify: `src/nas_index/web/routes/settings.py`
- Modify: `src/nas_index/web/templates/settings.html`

**Interfaces:**
- Consumes:
  - `probe_qnap_connection(...) -> QnapProbeResult`
  - `NasRepository.create_server(..., use_https: bool, skip_tls_verify: bool, ...)`
  - `ConnectionTestStore.create(connection: NasConnection) -> str`
- Produces:
  - Settings form posts `skip_tls_verify`
  - `test_nas_form_connection()` uses probe result instead of raw `use_https`
  - Save/update routes validate tokens against final probed `NasConnection`

- [ ] **Step 1: Write failing settings integration tests**

Add these tests to `tests/integration/test_settings.py`:

```python
def test_settings_page_replaces_https_checkbox_with_tls_bypass_toggle(admin_client):
    response = admin_client.get("/settings")

    assert response.status_code == 200
    assert 'name="use_https"' not in response.text
    assert "忽略 HTTPS 证书校验" in response.text
    assert "仅在 HTTPS 时生效" in response.text


def test_connection_test_autodetects_https_and_saves_final_protocol(
    admin_client,
    monkeypatch,
):
    async def fake_probe_qnap_connection(**kwargs):
        return QnapProbeResult(
            connection=NasConnection(
                base_url="https://192.168.1.16",
                port=5001,
                use_https=True,
                username="indexer",
                password="secret",
                skip_tls_verify=True,
            ),
            share_count=6,
        )

    monkeypatch.setattr(
        "nas_index.web.routes.settings.probe_qnap_connection",
        fake_probe_qnap_connection,
    )

    response = admin_client.post(
        "/settings/nas/test",
        data={**_nas_form(), "host": "192.168.1.16", "port": "5001", "skip_tls_verify": "on"},
    )

    assert response.status_code == 200
    assert "连接成功（HTTPS），可访问 6 个共享目录" in response.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/integration/test_settings.py -q
```

Expected: FAIL because the page still renders `use_https` and the route still calls `test_connection(connection)`.

- [ ] **Step 3: Implement automatic probing in the settings flow**

Replace the old route helper:

```python
async def test_connection(
    connection: NasConnection,
) -> int:
    async with QnapClient(connection) as client:
        return len(await client.list_shares())
```

with:

```python
async def test_connection(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    skip_tls_verify: bool,
    settings: AppSettings,
) -> QnapProbeResult:
    return await probe_qnap_connection(
        host=host,
        port=port,
        username=username,
        password=password,
        skip_tls_verify=skip_tls_verify,
        timeout_seconds=settings.qnap_timeout_seconds,
        retry_attempts=settings.qnap_retry_attempts,
    )
```

Update the form handling:

```python
skip_tls_verify: bool = Form(False)
```

and save using the probed connection:

```python
probe = await test_connection(
    host=host,
    port=port,
    username=connection.username,
    password=connection.password,
    skip_tls_verify=skip_tls_verify,
    settings=request.app.state.settings,
)
token = request.app.state.connection_test_store.create(
    probe.connection
)
context = {
    "success": True,
    "message": (
        f"连接成功（{'HTTPS' if probe.connection.use_https else 'HTTP'}），"
        f"可访问 {probe.share_count} 个共享目录"
    ),
    "connection_test_token": token,
}
```

Update the template controls:

```html
<label class="checkbox-line settings-checkbox">
  <input name="skip_tls_verify"
         type="checkbox"
         value="on"
         data-connection-field>
  忽略 HTTPS 证书校验
</label>
<span class="plain-note">仅在 HTTPS 时生效</span>
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
uv run pytest tests/integration/test_settings.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/nas_index/web/routes/settings.py src/nas_index/web/templates/settings.html tests/integration/test_settings.py
git commit -m "feat: auto detect NAS protocol in settings"
```

### Task 4: Verify runtime propagation and finish regression pass

**Files:**
- Modify: `tests/integration/test_complete_scan.py`
- Modify: `tests/unit/test_thumbnails.py`
- Modify: `tests/integration/test_user_access.py`
- Modify: `tests/integration/test_downloads.py`
- Modify: `src/nas_index/web/app.py` (only if a helper wiring change is needed)

**Interfaces:**
- Consumes: `NasRepository.connection_for_indexer() -> NasConnection` with `skip_tls_verify`
- Produces: all runtime paths constructing `QnapClient` from repository connections honor `skip_tls_verify` without extra route-specific branches

- [ ] **Step 1: Write one regression test showing runtime clients preserve `skip_tls_verify`**

Add a focused test like:

```python
def test_connection_for_indexer_preserves_skip_tls_verify(database):
    with Session(database) as session:
        repository = NasRepository(session)
        server = repository.create_server(
            name="TLS NAS",
            base_url="https://192.168.1.16",
            port=5001,
            use_https=True,
            skip_tls_verify=True,
            enabled=True,
            sync_interval_minutes=30,
            username="indexer",
            password="secret",
        )
        session.commit()

    with Session(database) as session:
        connection = NasRepository(session).connection_for_indexer(server.id)

    assert connection is not None
    assert connection.skip_tls_verify is True
```

- [ ] **Step 2: Run targeted regression tests to verify any remaining failure**

Run:

```bash
uv run pytest tests/unit/test_thumbnails.py tests/integration/test_user_access.py tests/integration/test_downloads.py tests/integration/test_complete_scan.py -q
```

Expected: PASS, or a focused failure where an old `NasConnection(...)` call site still needs the new argument.

- [ ] **Step 3: Fix remaining call sites with minimal changes**

If any constructor call sites fail, update them by explicitly passing the new flag or relying on the default:

```python
connection = NasConnection(
    base_url="http://nas.local",
    port=8080,
    use_https=False,
    username="indexer",
    password="secret",
    skip_tls_verify=False,
)
```

- [ ] **Step 4: Run full verification**

Run:

```bash
uv run pytest -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_thumbnails.py tests/integration/test_user_access.py tests/integration/test_downloads.py tests/integration/test_complete_scan.py src/nas_index/web/app.py
git commit -m "test: verify NAS protocol auto detection regressions"
```

## Self-Review

- Spec coverage:
  - 自动探测协议 → Task 2, Task 3
  - 忽略 HTTPS 证书校验 → Task 1, Task 2, Task 3
  - 保存最终协议并复用到运行时 → Task 1, Task 4
  - 设置页去掉手选 HTTPS → Task 3
- Placeholder scan: no `TODO` / `TBD`; each task includes exact files, tests, commands, and code snippets.
- Type consistency:
  - `skip_tls_verify` is added consistently to `NasConnection`, `NasServerValue`, repository APIs, and connection test fingerprint.
  - `probe_qnap_connection()` consistently returns `QnapProbeResult`.
