# 目录页当前目录搜索 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `/browse` 页面增加“当前目录及子目录搜索”，并在同一页面里展示完整命中结果、相对路径、缩略图和下载入口。

**Architecture:** 这次改造直接落在现有目录页链路里，不复用全局 `/search` 页面。数据查询新增 `EntryRepository.search_subtree()`，由路由根据 `path + q + access.share_paths` 在 SQL 层完成子树范围约束；模板继续复用目录页现有卡片和缩略图能力，只在搜索模式下切换右侧内容区和交互。

**Tech Stack:** Python 3.12+, FastAPI, Jinja2, SQLAlchemy 2, SQLite/FTS5, Pytest.

---

## Scope Check

这份 spec 只覆盖一个子系统：目录页内的“当前目录搜索”。数据层、路由层、模板层和样式层需要一起改，但它们都围绕 `/browse` 这一个入口工作，不需要拆成多份计划。

## File Structure

- Modify `src/nas_index/repositories/entries.py`
  - 新增子树范围查询方法 `search_subtree()`，让目录页搜索能直接复用数据库索引和权限过滤。
- Modify `src/nas_index/web/routes/browse.py`
  - 增加 `q` 参数处理、搜索模式上下文，以及“相对当前目录路径”的格式化逻辑。
- Modify `src/nas_index/web/templates/browse.html`
  - 在右侧内容区加入当前目录搜索表单，并在搜索模式下改为结果卡片网格。
- Modify `src/nas_index/web/static/app.css`
  - 为目录页搜索表单、结果路径文案和搜索模式卡片增加紧凑样式。
- Modify `tests/unit/test_entry_repository.py`
  - 覆盖 `search_subtree()` 的范围、权限和返回分页约定。
- Modify `tests/integration/test_browse.py`
  - 覆盖 `/browse?q=` 的页面模式切换、链接行为、缩略图/下载复用，以及普通浏览回归。

### Task 1: Add subtree search coverage in the repository

**Files:**
- Modify: `tests/unit/test_entry_repository.py`
- Modify: `src/nas_index/repositories/entries.py`

- [ ] **Step 1: Write the failing repository tests**

Add these tests to `tests/unit/test_entry_repository.py`:

```python
def test_search_subtree_limits_results_to_current_branch(database):
    with Session(database) as session:
        nas_id = _create_nas(session, "Office")
        repository = EntryRepository(session)
        repository.upsert_batch(
            nas_id,
            [
                IndexedItem(
                    "Design",
                    "/Design",
                    "/",
                    "directory",
                    None,
                    None,
                    "/Design",
                ),
                IndexedItem(
                    "包装设计",
                    "/Design/包装设计",
                    "/Design",
                    "directory",
                    None,
                    None,
                    "/Design",
                ),
                IndexedItem(
                    "苹果方案.png",
                    "/Design/包装设计/苹果方案.png",
                    "/Design/包装设计",
                    "file",
                    1,
                    None,
                    "/Design",
                ),
                IndexedItem(
                    "苹果提案.png",
                    "/Design/包装设计/子目录/苹果提案.png",
                    "/Design/包装设计/子目录",
                    "file",
                    1,
                    None,
                    "/Design",
                ),
                IndexedItem(
                    "苹果归档.png",
                    "/Design/运营/苹果归档.png",
                    "/Design/运营",
                    "file",
                    1,
                    None,
                    "/Design",
                ),
            ],
            generation=1,
        )
        session.commit()

        page = repository.search_subtree(
            "苹果",
            nas_id=nas_id,
            path="/Design/包装设计",
            allowed_share_paths=("/Design",),
        )

    assert [item.full_path for item in page.items] == [
        "/Design/包装设计/苹果方案.png",
        "/Design/包装设计/子目录/苹果提案.png",
    ]
    assert page.total == 2
    assert page.page == 1
    assert page.page_size == 2


def test_search_subtree_from_root_still_respects_share_permissions(database):
    with Session(database) as session:
        nas_id = _create_nas(session, "Office")
        repository = EntryRepository(session)
        repository.upsert_batch(
            nas_id,
            [
                IndexedItem(
                    "Public",
                    "/Public",
                    "/",
                    "directory",
                    None,
                    None,
                    "/Public",
                ),
                IndexedItem(
                    "苹果资料.png",
                    "/Public/苹果资料.png",
                    "/Public",
                    "file",
                    1,
                    None,
                    "/Public",
                ),
                IndexedItem(
                    "Secret",
                    "/Secret",
                    "/",
                    "directory",
                    None,
                    None,
                    "/Secret",
                ),
                IndexedItem(
                    "苹果财务.png",
                    "/Secret/苹果财务.png",
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

        page = repository.search_subtree(
            "苹果",
            nas_id=nas_id,
            path="/",
            allowed_share_paths=("/Public",),
        )

    assert [item.full_path for item in page.items] == [
        "/Public/苹果资料.png"
    ]
    assert page.total == 1
```

- [ ] **Step 2: Run the repository tests to verify they fail**

Run:

```bash
uv run pytest tests/unit/test_entry_repository.py -q
```

Expected: FAIL with `AttributeError: 'EntryRepository' object has no attribute 'search_subtree'`.

- [ ] **Step 3: Implement `search_subtree()` in `src/nas_index/repositories/entries.py`**

Update the imports and add a subtree predicate helper plus the new repository method:

```python
from sqlalchemy import bindparam, case, delete, func, or_, select, text
```

```python
def _normalized_search_path(path: str) -> str:
    path = path.strip() or "/"
    if path != "/" and path.endswith("/"):
        return path.rstrip("/")
    return path


def _subtree_scope_clauses(path: str):
    normalized = _normalized_search_path(path)
    if normalized == "/":
        return []

    escaped = (
        normalized.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return [
        or_(
            Entry.full_path == normalized,
            Entry.full_path.like(
                f"{escaped}/%",
                escape="\\",
            ),
        )
    ]
```

```python
    def search_subtree(
        self,
        query: str,
        *,
        nas_id: int = DEFAULT_NAS_ID,
        path: str,
        allowed_share_paths: tuple[str, ...] | None = None,
    ) -> Page[Entry]:
        query = query.strip()
        if not query:
            return Page([], 0, 1, 0)
        if allowed_share_paths is not None and not allowed_share_paths:
            return Page([], 0, 1, 0)

        subtree_clauses = _subtree_scope_clauses(path)

        if len(query) < 3:
            escaped_query = (
                query.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            predicate = [
                Entry.nas_id == nas_id,
                Entry.name.ilike(
                    f"%{escaped_query}%",
                    escape="\\",
                ),
                *subtree_clauses,
            ]
            if allowed_share_paths is not None:
                predicate.append(
                    Entry.share_path.in_(allowed_share_paths)
                )
            rows = list(
                self.session.scalars(
                    select(Entry)
                    .where(*predicate)
                    .order_by(
                        case(
                            (
                                Entry.entry_type == "directory",
                                0,
                            ),
                            else_=1,
                        ),
                        func.lower(Entry.name),
                        Entry.id,
                    )
                )
            )
            return Page(rows, len(rows), 1, len(rows))

        normalized = _normalized_search_path(path)
        escaped_path = (
            normalized.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        rows_sql = text(
            """
            SELECT e.*
            FROM entry_search
            CROSS JOIN entries AS e ON e.id = entry_search.rowid
            WHERE entry_search MATCH :query
              AND e.nas_id = :nas_id
              AND (
                :filter_shares = 0
                OR e.share_path IN :share_paths
              )
              AND (
                :path_is_root = 1
                OR e.full_path = :path
                OR e.full_path LIKE :path_like ESCAPE '\\'
              )
            ORDER BY CASE
                       WHEN e.entry_type = 'directory'
                       THEN 0 ELSE 1
                     END,
                     lower(e.name),
                     e.id
            """
        ).bindparams(bindparam("share_paths", expanding=True))
        params = {
            "query": '"' + query.replace('"', '""') + '"',
            "nas_id": nas_id,
            "filter_shares": 1
            if allowed_share_paths is not None
            else 0,
            "share_paths": list(allowed_share_paths or ("/",)),
            "path_is_root": 1 if normalized == "/" else 0,
            "path": normalized,
            "path_like": f"{escaped_path}/%",
        }
        rows = list(
            self.session.scalars(
                select(Entry).from_statement(rows_sql),
                params,
            )
        )
        return Page(rows, len(rows), 1, len(rows))
```

- [ ] **Step 4: Run the repository tests to verify they pass**

Run:

```bash
uv run pytest tests/unit/test_entry_repository.py -q
```

Expected: PASS with the new subtree tests green and existing repository tests still green.

- [ ] **Step 5: Commit the repository task**

Run:

```bash
git add tests/unit/test_entry_repository.py src/nas_index/repositories/entries.py
git commit -m "feat: add browse subtree search repository query"
```

### Task 2: Add `/browse?q=` route behavior and route-level integration tests

**Files:**
- Modify: `tests/integration/test_browse.py`
- Modify: `src/nas_index/web/routes/browse.py`

- [ ] **Step 1: Write the failing browse route tests**

Add these tests to `tests/integration/test_browse.py`:

```python
import re
```

```python
def _plain_text(html: str) -> str:
    text = re.sub(r"<[^>]+>", "", html)
    return re.sub(r"\s+", " ", text).strip()
```

```python
def test_browse_search_limits_results_to_current_subtree(
    client,
    web_public_access,
):
    with Session(client.app.state.engine) as session:
        repository = EntryRepository(session)
        repository.upsert_batch(
            [
                IndexedItem(
                    "Public",
                    "/Public",
                    "/",
                    "directory",
                    None,
                    None,
                    share_path="/Public",
                ),
                IndexedItem(
                    "资料",
                    "/Public/资料",
                    "/Public",
                    "directory",
                    None,
                    None,
                    share_path="/Public",
                ),
                IndexedItem(
                    "提案",
                    "/Public/资料/提案",
                    "/Public/资料",
                    "directory",
                    None,
                    None,
                    share_path="/Public",
                ),
                IndexedItem(
                    "苹果方案.png",
                    "/Public/资料/提案/苹果方案.png",
                    "/Public/资料/提案",
                    "file",
                    42,
                    datetime(2026, 1, 1, tzinfo=UTC),
                    share_path="/Public",
                ),
                IndexedItem(
                    "苹果总结.docx",
                    "/Public/资料/苹果总结.docx",
                    "/Public/资料",
                    "file",
                    42,
                    datetime(2026, 1, 1, tzinfo=UTC),
                    share_path="/Public",
                ),
                IndexedItem(
                    "苹果归档.zip",
                    "/Public/归档/苹果归档.zip",
                    "/Public/归档",
                    "file",
                    42,
                    datetime(2026, 1, 1, tzinfo=UTC),
                    share_path="/Public",
                ),
            ],
            generation=1,
        )
        session.commit()

    response = client.get(
        "/browse",
        params={"path": "/Public/资料", "q": "苹果"},
    )
    text = _plain_text(response.text)

    assert response.status_code == 200
    assert "苹果方案.png" in text
    assert "苹果总结.docx" in text
    assert "苹果归档.zip" not in text
    assert "命中 2 项" in text
    assert "批量下载" not in text
```

```python
def test_browse_search_file_link_targets_parent_with_selected_id(
    client,
    web_public_access,
):
    with Session(client.app.state.engine) as session:
        repository = EntryRepository(session)
        repository.upsert_batch(
            [
                IndexedItem(
                    "Public",
                    "/Public",
                    "/",
                    "directory",
                    None,
                    None,
                    share_path="/Public",
                ),
                IndexedItem(
                    "资料",
                    "/Public/资料",
                    "/Public",
                    "directory",
                    None,
                    None,
                    share_path="/Public",
                ),
                IndexedItem(
                    "苹果方案.png",
                    "/Public/资料/苹果方案.png",
                    "/Public/资料",
                    "file",
                    42,
                    datetime(2026, 1, 1, tzinfo=UTC),
                    share_path="/Public",
                ),
            ],
            generation=1,
        )
        session.commit()
        entry_id = repository.get_by_nas_path(
            1,
            "/Public/资料/苹果方案.png",
        ).id

    response = client.get(
        "/browse",
        params={"path": "/Public/资料", "q": "苹果"},
    )

    assert response.status_code == 200
    assert (
        f'href="/browse?path=%2FPublic%2F%E8%B5%84%E6%96%99&amp;selected={entry_id}"'
        in response.text
    )
    assert "当前目录" in response.text
```

- [ ] **Step 2: Run the route tests to verify they fail**

Run:

```bash
uv run pytest tests/integration/test_browse.py -q
```

Expected: FAIL because `/browse` currently ignores `q`, still renders direct children, and does not build search-mode links or counters.

- [ ] **Step 3: Implement search-mode routing in `src/nas_index/web/routes/browse.py`**

Add a search result view model and small helpers above `browse()`:

```python
@dataclass(frozen=True)
class BrowseSearchResult:
    entry: Entry
    relative_path: str
    browse_path: str
    selected_id: int | None


def _search_target(entry: Entry) -> tuple[str, int | None]:
    if entry.entry_type == "directory":
        return entry.full_path, None
    return entry.parent_path, entry.id


def _relative_result_path(
    current_path: str,
    entry: Entry,
) -> str:
    anchor_path = (
        entry.full_path
        if entry.entry_type == "directory"
        else entry.parent_path
    )
    if current_path == "/":
        relative = anchor_path.removeprefix("/")
    elif anchor_path == current_path:
        relative = ""
    else:
        relative = anchor_path.removeprefix(
            f"{current_path}/"
        )
    return relative or "当前目录"
```

Inside `browse()`, branch on `q` before the normal listing call:

```python
def browse(
    request: Request,
    path: str = Query("/"),
    q: str = Query(""),
    selected: int | None = None,
    page: int = Query(1, ge=1),
    session: Session = Depends(get_session),
):
    access = current_access(request)
    if access is None:
        return access_login_redirect(request)

    path = _normalize_path(path)
    query = q.strip()
    repository = EntryRepository(session)
    search_mode = bool(query)

    if search_mode:
        search_page = repository.search_subtree(
            query,
            nas_id=access.nas_id,
            path=path,
            allowed_share_paths=access.share_paths,
        )
        search_results = []
        for entry in search_page.items:
            browse_path, selected_id = _search_target(entry)
            search_results.append(
                BrowseSearchResult(
                    entry=entry,
                    relative_path=_relative_result_path(
                        path,
                        entry,
                    ),
                    browse_path=browse_path,
                    selected_id=selected_id,
                )
            )
        listing = None
    else:
        if selected is not None:
            page = (
                repository.page_for_entry(
                    selected,
                    page_size=100,
                )
                or page
            )
        listing = repository.list_children(
            access.nas_id,
            path,
            allowed_share_paths=access.share_paths,
            page=page,
            page_size=100,
        )
        search_page = None
        search_results = []
```

Return these new values in the template context:

```python
        context={
            "path": path,
            "selected": selected,
            "listing": listing,
            "search_mode": search_mode,
            "search_query": query,
            "search_results": search_results,
            "search_total": 0 if search_page is None else search_page.total,
            "tree_nodes": _build_tree(
                repository,
                access=access,
                parent_path="/",
                current_path=path,
                expanded_paths=_expanded_paths(path),
            ),
            "is_thumbnail_candidate": is_thumbnail_candidate,
        },
```

- [ ] **Step 4: Run the route tests to verify they pass**

Run:

```bash
uv run pytest tests/integration/test_browse.py -q
```

Expected: PASS for the new `/browse?q=` tests and existing browse tests.

- [ ] **Step 5: Commit the route task**

Run:

```bash
git add tests/integration/test_browse.py src/nas_index/web/routes/browse.py
git commit -m "feat: add browse subtree search routing"
```

### Task 3: Render current-directory search UI in the browse template

**Files:**
- Modify: `tests/integration/test_browse.py`
- Modify: `src/nas_index/web/templates/browse.html`
- Modify: `src/nas_index/web/static/app.css`

- [ ] **Step 1: Write the failing rendering and stylesheet checks**

Append these checks to `tests/integration/test_browse.py`:

```python
def test_browse_search_renders_form_relative_path_and_clear_action(
    client,
    web_public_access,
):
    with Session(client.app.state.engine) as session:
        repository = EntryRepository(session)
        repository.upsert_batch(
            [
                IndexedItem(
                    "Public",
                    "/Public",
                    "/",
                    "directory",
                    None,
                    None,
                    share_path="/Public",
                ),
                IndexedItem(
                    "资料",
                    "/Public/资料",
                    "/Public",
                    "directory",
                    None,
                    None,
                    share_path="/Public",
                ),
                IndexedItem(
                    "苹果主图.jpg",
                    "/Public/资料/苹果主图.jpg",
                    "/Public/资料",
                    "file",
                    42,
                    datetime(2026, 1, 1, tzinfo=UTC),
                    share_path="/Public",
                ),
            ],
            generation=1,
        )
        session.commit()

    response = client.get(
        "/browse",
        params={"path": "/Public", "q": "苹果"},
    )

    assert response.status_code == 200
    assert 'for="browse-query"' in response.text
    assert "在当前目录及子目录搜索" in response.text
    assert 'name="q"' in response.text
    assert 'value="苹果"' in response.text
    assert ">清空<" in response.text
    assert "browse-search-result-path" in response.text
    assert "资料" in response.text
    assert 'src="/thumbnails/' in response.text
    assert 'href="/downloads/' in response.text
```

```python
def test_browse_search_styles_define_compact_form_and_result_path(
    client,
):
    response = client.get("/static/app.css")

    assert response.status_code == 200
    assert ".browse-search-form" in response.text
    assert ".browse-search-actions" in response.text
    assert ".browse-search-result-path" in response.text
    assert ".browse-search-primary" in response.text
```

- [ ] **Step 2: Run the rendering checks to verify they fail**

Run:

```bash
uv run pytest tests/integration/test_browse.py -q
```

Expected: FAIL because the current template has no browse search form, no relative-path line, and no related CSS classes.

- [ ] **Step 3: Update `src/nas_index/web/templates/browse.html` for search mode**

Insert the current-directory search form under the “当前路径” block:

```jinja2
      <form class="browse-search-form"
            method="get"
            action="/browse">
        <input type="hidden" name="path" value="{{ path }}">
        <label class="browse-search-label" for="browse-query">
          在当前目录及子目录搜索
        </label>
        <div class="browse-search-controls">
          <input id="browse-query"
                 name="q"
                 type="text"
                 value="{{ search_query }}"
                 placeholder="输入关键词">
          <div class="browse-search-actions">
            <button type="submit">搜索</button>
            {% if search_mode %}
              <a href="/browse?path={{ path | urlencode }}">清空</a>
            {% endif %}
          </div>
        </div>
      </form>
```

Replace the right-side content branch with an explicit `search_mode` split:

```jinja2
      {% if search_mode %}
        <p>命中 {{ search_total }} 项</p>

        {% if search_results %}
          <div class="browse-grid">
            {% for result in search_results %}
              {% set item = result.entry %}
              {% set is_directory = item.entry_type == "directory" %}
              <div class="browse-tile browse-search-tile{% if selected == item.id %} selected{% endif %}">
                <a class="browse-search-primary"
                   href="/browse?path={{ result.browse_path | urlencode }}{% if result.selected_id is not none %}&amp;selected={{ result.selected_id }}{% endif %}">
                  <span class="browse-tile-meta">
                    <span class="browse-tile-select"></span>
                    {% if selected == item.id %}
                      <span class="browse-tile-current-badge">
                        当前文件
                      </span>
                    {% endif %}
                  </span>
                  <span class="browse-tile-preview">
                    {% if is_directory %}
                      {{ folder_icon() }}
                    {% elif is_thumbnail_candidate(item) %}
                      <img src="/thumbnails/{{ item.id }}"
                           alt=""
                           loading="lazy"
                           onerror="this.hidden=true; this.nextElementSibling.hidden=false;">
                      <span data-thumbnail-fallback hidden>
                        {{ file_icon() }}
                      </span>
                    {% else %}
                      {{ file_icon() }}
                    {% endif %}
                  </span>
                  <span class="browse-tile-name" title="{{ item.name }}">
                    {{ item.name }}
                  </span>
                </a>
                <span class="browse-search-result-path">
                  {{ result.relative_path }}
                </span>
                {% if not is_directory %}
                  <a class="browse-tile-action"
                     href="/downloads/{{ item.id }}">
                    下载
                  </a>
                {% endif %}
              </div>
            {% endfor %}
          </div>
        {% else %}
          <p>当前目录及子目录下没有匹配项。</p>
        {% endif %}
      {% else %}
        {# 保留现有普通浏览表单、批量下载和分页 #}
      {% endif %}
```

Keep the existing batch-download script wrapped so it only runs when `[data-batch-download-form]` exists; do not duplicate download logic in search mode.

- [ ] **Step 4: Add browse search styles in `src/nas_index/web/static/app.css`**

Append these rules near the existing browse styles:

```css
.browse-search-form {
  display: grid;
  grid-template-columns: minmax(0, 560px);
  gap: 8px;
  justify-content: start;
  margin: 8px 0 12px;
}

.browse-search-label {
  display: block;
  padding-top: 4px;
  white-space: nowrap;
}

.browse-search-controls {
  display: grid;
  gap: 8px;
}

.browse-search-actions {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
}

.browse-search-tile {
  grid-template-rows: auto auto auto auto auto;
  min-height: 214px;
}

.browse-search-primary {
  display: grid;
  gap: 6px;
  color: inherit;
  text-decoration: none;
}

.browse-search-primary:hover {
  text-decoration: none;
}

.browse-search-result-path {
  min-height: 18px;
  font-size: 12px;
  line-height: 1.35;
  color: #444;
  overflow-wrap: anywhere;
}
```

- [ ] **Step 5: Run the browse integration tests again**

Run:

```bash
uv run pytest tests/integration/test_browse.py -q
```

Expected: PASS with search-form, relative-path, thumbnail, and download assertions all green.

- [ ] **Step 6: Commit the template task**

Run:

```bash
git add tests/integration/test_browse.py src/nas_index/web/templates/browse.html src/nas_index/web/static/app.css
git commit -m "feat: render browse subtree search results"
```

### Task 4: Final verification and regression sweep

**Files:**
- Modify: none
- Verify: `tests/unit/test_entry_repository.py`
- Verify: `tests/integration/test_browse.py`

- [ ] **Step 1: Run the targeted verification suite**

Run:

```bash
uv run pytest \
  tests/unit/test_entry_repository.py \
  tests/integration/test_browse.py -q
```

Expected: PASS with `0 failed`.

- [ ] **Step 2: Run one focused manual smoke test**

Start the app if it is not already running, then verify this flow in the browser:

```text
1. 打开 /browse?path=/Public
2. 在“在当前目录及子目录搜索”里输入 苹果
3. 结果区只显示 /Public 子树命中，不显示其他共享目录
4. 文件卡片显示相对路径、缩略图和下载
5. 点击文件卡片进入父目录并高亮该文件
6. 点击清空后恢复普通目录浏览和分页
```

Expected: 搜索结果和普通浏览模式切换自然，左侧树保持当前分支展开。

- [ ] **Step 3: Commit any final adjustments if the smoke test revealed UI polish work**

If Step 2 required no code changes, skip this commit.

If Step 2 required small fixes, run:

```bash
git add src/nas_index/web/routes/browse.py src/nas_index/web/templates/browse.html src/nas_index/web/static/app.css tests/integration/test_browse.py
git commit -m "fix: polish browse subtree search interactions"
```
