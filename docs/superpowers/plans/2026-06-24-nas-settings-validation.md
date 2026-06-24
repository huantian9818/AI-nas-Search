# NAS Settings Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除无效的完整重扫配置，并让 NAS 当前连接参数测试成功后才能保存，同时实现已确认的紧凑两行设置页。

**Architecture:** 新增内存型 `ConnectionTestStore`，用随机短期凭证绑定连接参数摘要。测试接口使用当前表单值连接 NAS 并签发凭证；保存接口服务端校验凭证。旧数据库列只作为兼容存储保留，不再进入应用业务类型。

**Tech Stack:** FastAPI、SQLAlchemy、Jinja2、原生 JavaScript、HTMX 现有运行时、pytest、Playwright。

---

### Task 1: 连接测试凭证

**Files:**
- Create: `src/nas_index/services/connection_tests.py`
- Create: `tests/unit/test_connection_test_store.py`
- Modify: `src/nas_index/web/app.py`

- [ ] **Step 1: 写失败测试**

```python
def test_connection_test_token_matches_only_original_connection():
    store = ConnectionTestStore(ttl_seconds=300, now=lambda: NOW)
    token = store.create(CONNECTION)
    assert store.matches(token, CONNECTION) is True
    assert store.matches(token, CHANGED_CONNECTION) is False
```

- [ ] **Step 2: 验证测试因缺少实现而失败**

Run: `.venv/bin/pytest -q tests/unit/test_connection_test_store.py`
Expected: FAIL，提示无法导入 `ConnectionTestStore`。

- [ ] **Step 3: 实现最小凭证存储**

```python
class ConnectionTestStore:
    def create(self, connection: NasConnection) -> str:
        token = token_urlsafe(32)
        self._tests[token] = (
            self._fingerprint(connection),
            self.now() + timedelta(seconds=self.ttl_seconds),
        )
        return token

    def matches(self, token: str, connection: NasConnection) -> bool:
        tested = self._tests.get(token)
        return bool(
            tested
            and tested[1] > self.now()
            and compare_digest(tested[0], self._fingerprint(connection))
        )
```

- [ ] **Step 4: 注册到应用状态并运行测试**

Run: `.venv/bin/pytest -q tests/unit/test_connection_test_store.py`
Expected: PASS。

### Task 2: 删除公开的完整重扫配置

**Files:**
- Modify: `src/nas_index/types.py`
- Modify: `src/nas_index/repositories/nas.py`
- Modify: `src/nas_index/models.py`
- Modify: `src/nas_index/web/routes/settings.py`
- Modify: `src/nas_index/web/templates/settings.html`
- Modify: `tests/integration/test_settings.py`
- Modify: `tests/unit/test_nas_repository.py`

- [ ] **Step 1: 修改测试，移除 `full_resync_interval_hours` 并断言页面不再显示**

```python
assert "完整重扫间隔" not in response.text
```

- [ ] **Step 2: 运行相关测试并确认因旧接口仍存在而失败**

Run: `.venv/bin/pytest -q tests/integration/test_settings.py tests/unit/test_nas_repository.py`
Expected: FAIL，页面仍包含完整重扫字段或函数签名不匹配。

- [ ] **Step 3: 删除公开字段并保留内部兼容列**

```python
legacy_full_resync_interval_hours: Mapped[int] = mapped_column(
    "full_resync_interval_hours",
    Integer,
    default=24,
)
```

- [ ] **Step 4: 运行相关测试**

Run: `.venv/bin/pytest -q tests/integration/test_settings.py tests/unit/test_nas_repository.py`
Expected: PASS。

### Task 3: 测试当前表单并强制保存验证

**Files:**
- Modify: `src/nas_index/web/routes/settings.py`
- Modify: `src/nas_index/web/templates/partials/connection_result.html`
- Modify: `tests/integration/test_settings.py`

- [ ] **Step 1: 写失败集成测试**

```python
def test_create_requires_successful_current_connection_test(admin_client):
    response = admin_client.post("/settings/nas", data=FORM)
    assert response.status_code == 422

def test_current_form_test_issues_token(admin_client, monkeypatch):
    response = admin_client.post("/settings/nas/test", data=FORM)
    assert response.status_code == 200
    assert 'name="connection_test_token"' in response.text
```

- [ ] **Step 2: 运行测试确认新测试接口和保存校验尚不存在**

Run: `.venv/bin/pytest -q tests/integration/test_settings.py`
Expected: FAIL。

- [ ] **Step 3: 实现连接解析、测试接口和保存校验**

```python
connection = _connection_from_form(
    repository,
    nas_id=nas_id,
    host=host,
    port=port,
    use_https=use_https,
    username=username,
    password=password,
)
if not request.app.state.connection_test_store.matches(
    connection_test_token,
    connection,
):
    return _settings_error(..., status_code=422)
```

- [ ] **Step 4: 运行设置页集成测试**

Run: `.venv/bin/pytest -q tests/integration/test_settings.py`
Expected: PASS。

### Task 4: 紧凑两行布局和状态联动

**Files:**
- Modify: `src/nas_index/web/templates/settings.html`
- Modify: `src/nas_index/web/static/app.css`
- Modify: `tests/integration/test_settings.py`

- [ ] **Step 1: 添加布局与前端标记测试**

```python
assert "settings-fields-primary" in response.text
assert "settings-fields-secondary" in response.text
assert "data-connection-field" in response.text
assert "data-test-connection" in response.text
```

- [ ] **Step 2: 运行测试并确认标记缺失**

Run: `.venv/bin/pytest -q tests/integration/test_settings.py`
Expected: FAIL。

- [ ] **Step 3: 实现模板、CSS 和原生 JavaScript**

```javascript
connectionFields.forEach((field) => {
  field.addEventListener("input", invalidateConnectionTest);
  field.addEventListener("change", invalidateConnectionTest);
});
```

- [ ] **Step 4: 运行设置页测试**

Run: `.venv/bin/pytest -q tests/integration/test_settings.py`
Expected: PASS。

### Task 5: 全量验证与部署

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 更新 README 中同步和连接测试说明**

```markdown
- 每次定时同步都会遍历 NAS 目录；设置页只保留一个同步间隔。
- 新增或修改 NAS 前必须使用当前表单参数测试连接。
```

- [ ] **Step 2: 运行完整测试和格式检查**

Run: `.venv/bin/pytest -q && git diff --check`
Expected: 全部测试通过且 `git diff --check` 无输出。

- [ ] **Step 3: 使用 Playwright 验证桌面和移动布局**

验证保存初始禁用、测试成功后启用、连接字段变化后再次禁用；确认 1280px 与 390px 视口无重叠。

- [ ] **Step 4: 构建并更新远端 Docker**

Run: `docker compose up -d --build`
Expected: `ai-nas-search` 健康接口返回 200，现有数据库数据量不变。
