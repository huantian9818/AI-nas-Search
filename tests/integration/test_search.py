import re
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from nas_index.repositories.entries import EntryRepository
from nas_index.types import IndexedItem


@pytest.fixture
def search_layout_entries(client, web_seeded_entries):
    with Session(client.app.state.engine) as session:
        EntryRepository(session).upsert_batch(
            [
                IndexedItem(
                    "项目资料",
                    "/Public/项目资料",
                    "/Public",
                    "directory",
                    None,
                    datetime(
                        2026,
                        1,
                        2,
                        9,
                        30,
                        tzinfo=UTC,
                    ),
                ),
                IndexedItem(
                    "项目预算.xlsx",
                    "/Public/项目资料/项目预算.xlsx",
                    "/Public/项目资料",
                    "file",
                    20480,
                    datetime(
                        2026,
                        2,
                        12,
                        14,
                        15,
                        tzinfo=UTC,
                    ),
                ),
                IndexedItem(
                    "Archive",
                    "/Archive",
                    "/",
                    "directory",
                    None,
                    None,
                ),
                IndexedItem(
                    "2025",
                    "/Archive/2025",
                    "/Archive",
                    "directory",
                    None,
                    None,
                ),
                IndexedItem(
                    "项目归档.md",
                    "/Archive/2025/项目归档.md",
                    "/Archive/2025",
                    "file",
                    8,
                    datetime(
                        2025,
                        12,
                        22,
                        17,
                        45,
                        tzinfo=UTC,
                    ),
                ),
            ],
            generation=1,
        )
        session.commit()
    token = client.app.state.access_store.create(
        nas_id=1,
        username="alice",
        share_paths=("/Archive", "/Public"),
    )
    client.cookies.set("nas_access", token)


def _plain_text(html: str) -> str:
    text = re.sub(r"<[^>]+>", "", html)
    return re.sub(r"\s+", " ", text).strip()


def test_search_form_labels_keyword_input_and_loading_feedback(
    client,
    web_seeded_entries,
):
    response = client.get("/search")

    assert response.status_code == 200
    assert "<h1>搜索</h1>" not in response.text
    assert re.search(
        r'<label[^>]*for="search-query"[^>]*>\s*输入关键词\s*</label>\s*'
        r'<div class="search-controls">',
        response.text,
    )
    assert re.search(
        r'<input[^>]*id="search-query"[\s\S]*?>\s*'
        r'<div class="search-actions">\s*<button type="submit">搜索</button>',
        response.text,
    )
    assert 'data-search-form' in response.text
    assert 'data-search-loading' in response.text
    assert "搜索中..." in response.text


def test_search_form_uses_compact_single_column_layout(
    client,
):
    response = client.get("/static/app.css")

    assert response.status_code == 200
    assert re.search(
        r"\.search-form\s*{[^}]*grid-template-columns:\s*minmax\(0,\s*560px\);",
        response.text,
    )
    assert "grid-template-columns: max-content minmax(0, 1fr);" not in response.text


def test_search_page_returns_name_and_full_path(
    client,
    search_layout_entries,
):
    response = client.get(
        "/search",
        params={"q": "项目"},
    )
    text = _plain_text(response.text)

    assert response.status_code == 200
    assert "年度<mark>项目</mark>计划.docx" in response.text
    assert "命中目录" in text
    assert "共 4 条结果，分布在 3 个目录" in text
    assert "Public" in text
    assert "Archive" in text
    assert "<mark>项目</mark>" in response.text
    assert "/browse?path=%2FPublic%2F%E8%B5%84%E6%96%99" not in response.text
    assert "总结这些结果" in response.text
    assert 'data-summary-form' in response.text
    assert 'data-summary-output' in response.text
    assert '"/search/summary"' in response.text


def test_search_summary_requires_access_session(client):
    response = client.post(
        "/search/summary",
        json={"q": "项目", "page": 1},
    )

    assert response.status_code == 401


def test_search_summary_uses_only_authorized_results(
    client,
    web_seeded_entries,
    monkeypatch,
):
    with Session(client.app.state.engine) as session:
        EntryRepository(session).upsert_batch(
            [
                IndexedItem(
                    "public-budget.xlsx",
                    "/Public/public-budget.xlsx",
                    "/Public",
                    "file",
                    16,
                    datetime(2026, 1, 1, tzinfo=UTC),
                    share_path="/Public",
                ),
                IndexedItem(
                    "Finance",
                    "/Finance",
                    "/",
                    "directory",
                    None,
                    None,
                    share_path="/Finance",
                ),
                IndexedItem(
                    "finance-budget.xlsx",
                    "/Finance/finance-budget.xlsx",
                    "/Finance",
                    "file",
                    32,
                    datetime(2026, 1, 1, tzinfo=UTC),
                    share_path="/Finance",
                ),
            ],
            generation=1,
        )
        session.commit()

    class FakeSummarizer:
        def __init__(self):
            self.calls = []

        async def summarize(self, context):
            self.calls.append(context)
            return "优先查看 Public 目录。"

    summarizer = FakeSummarizer()
    monkeypatch.setattr(
        client.app.state,
        "search_summarizer",
        summarizer,
        raising=False,
    )

    response = client.post(
        "/search/summary",
        json={"q": "budget", "page": 1},
    )

    assert response.status_code == 200
    assert response.json() == {"summary": "优先查看 Public 目录。"}
    assert len(summarizer.calls) == 1
    context = summarizer.calls[0]
    assert context.query == "budget"
    assert context.total == 1
    assert len(context.directories) == 1
    assert context.directories[0].path == "/Public"
    assert context.directories[0].items[0].name == "public-budget.xlsx"
    assert "finance-budget.xlsx" not in repr(context)


def test_search_result_links_to_parent_and_selected_entry(
    client,
    search_layout_entries,
):
    response = client.get(
        "/search",
        params={"q": "项目"},
    )

    assert "/search?q=%E9%A1%B9%E7%9B%AE&amp;page=1&amp;selected=" not in response.text
    assert "/browse?path=/Public&amp;selected=" in response.text
    assert "/browse?path=/Public/%E9%A1%B9%E7%9B%AE%E8%B5%84%E6%96%99" in response.text
    assert "/browse?path=/Public/%E9%A1%B9%E7%9B%AE%E8%B5%84%E6%96%99&amp;selected=" in response.text
    assert "&amp;selected=" in response.text


def test_search_page_shows_empty_state_for_missing_query(
    client,
    web_seeded_entries,
):
    response = client.get(
        "/search",
        params={"q": "不存在的关键词"},
    )

    assert response.status_code == 200
    assert "没有找到与" in response.text
    assert "可以试试更短的关键词" in response.text
