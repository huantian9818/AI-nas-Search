import json
import re
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from nas_index.repositories.entries import EntryRepository
from nas_index.services.search_summary import load_search_summary_payload
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


def _summary_payload(html: str) -> dict[str, str]:
    match = re.search(
        r'<script type="application/json" data-summary-payload>\s*'
        r"(?P<payload>.*?)"
        r"\s*</script>",
        html,
        re.DOTALL,
    )
    assert match is not None
    return json.loads(match.group("payload"))


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
    assert "命中目录" in text
    assert "共 4 条结果，分布在 3 个目录" in text
    assert "Public" in text
    assert "Archive" in text
    assert "<mark>项目</mark>" in response.text
    assert "年度项目计划.docx" not in text
    assert "项目预算.xlsx" not in text
    assert "项目归档.md" not in text
    assert "search-result-list" not in response.text
    assert "browse-main" not in response.text
    assert "/browse?path=%2FPublic%2F%E8%B5%84%E6%96%99" not in response.text
    assert "总结这些结果" in response.text
    assert 'data-summary-form' in response.text
    assert 'data-summary-output' in response.text
    assert 'data-summary-payload' in response.text
    assert '"/search/summary"' in response.text
    summary_payload = _summary_payload(response.text)
    assert set(summary_payload) == {"payload", "signature"}
    assert summary_payload["payload"]
    assert summary_payload["signature"]
    _, context = load_search_summary_payload(
        summary_payload["payload"],
        summary_payload["signature"],
        secret=client.app.state.search_summary_payload_secret,
    )
    payload_names = {
        item.name
        for directory in context.directories
        for item in directory.items
    }
    assert "年度项目计划.docx" in payload_names
    assert "项目预算.xlsx" in payload_names
    assert "项目归档.md" in payload_names


def test_search_page_includes_all_results_and_summary_payload(
    client,
    web_seeded_entries,
):
    with Session(client.app.state.engine) as session:
        items = [
            IndexedItem(
                "素材A",
                "/Public/素材A",
                "/Public",
                "directory",
                None,
                None,
                share_path="/Public",
            ),
            IndexedItem(
                "素材B",
                "/Public/素材B",
                "/Public",
                "directory",
                None,
                None,
                share_path="/Public",
            ),
        ]
        for index in range(55):
            parent = (
                "/Public/素材A"
                if index < 30
                else "/Public/素材B"
            )
            items.append(
                IndexedItem(
                    f"葡萄素材-{index:02d}.png",
                    f"{parent}/葡萄素材-{index:02d}.png",
                    parent,
                    "file",
                    1024,
                    datetime(2026, 1, 1, tzinfo=UTC),
                    share_path="/Public",
                )
            )
        EntryRepository(session).upsert_batch(
            items,
            generation=1,
        )
        session.commit()

    response = client.get(
        "/search",
        params={"q": "葡萄"},
    )
    text = _plain_text(response.text)

    assert response.status_code == 200
    assert "共 55 条结果，分布在 2 个目录" in text
    assert "素材A" in text
    assert "素材B" in text
    assert "葡萄素材-00.png" not in text
    assert "葡萄素材-54.png" not in text
    assert "search-result-list" not in response.text
    assert "browse-main" not in response.text
    assert "下一页" not in text

    signed_payload = _summary_payload(response.text)
    _, context = load_search_summary_payload(
        signed_payload["payload"],
        signed_payload["signature"],
        secret=client.app.state.search_summary_payload_secret,
    )
    assert context.query == "葡萄"
    assert context.total == 55
    payload_names = {
        item.name
        for directory in context.directories
        for item in directory.items
    }
    assert "葡萄素材-00.png" in payload_names
    assert "葡萄素材-54.png" in payload_names
    assert sum(
        len(directory.items)
        for directory in context.directories
    ) == 55
    assert {
        directory.path
        for directory in context.directories
    } == {"/Public/素材A", "/Public/素材B"}


def test_search_summary_requires_access_session(client):
    response = client.post(
        "/search/summary",
        json={"payload": "x", "signature": "x"},
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

    search_response = client.get(
        "/search",
        params={"q": "budget"},
    )
    assert search_response.status_code == 200
    summary_payload = _summary_payload(search_response.text)

    def fail_search(*args, **kwargs):
        raise AssertionError("summary should not re-query search")

    monkeypatch.setattr(
        EntryRepository,
        "search",
        fail_search,
    )

    response = client.post(
        "/search/summary",
        json=summary_payload,
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


def test_search_summary_rejects_tampered_payload(
    client,
    search_layout_entries,
):
    search_response = client.get(
        "/search",
        params={"q": "项目"},
    )
    assert search_response.status_code == 200
    summary_payload = _summary_payload(search_response.text)
    summary_payload["signature"] = "0" * len(
        summary_payload["signature"]
    )

    response = client.post(
        "/search/summary",
        json=summary_payload,
    )

    assert response.status_code == 400


def test_search_summary_rejects_payload_from_different_access(
    client,
    search_layout_entries,
):
    search_response = client.get(
        "/search",
        params={"q": "项目"},
    )
    assert search_response.status_code == 200
    summary_payload = _summary_payload(search_response.text)
    token = client.app.state.access_store.create(
        nas_id=1,
        username="bob",
        share_paths=("/Archive",),
    )
    client.cookies.set("nas_access", token)

    response = client.post(
        "/search/summary",
        json=summary_payload,
    )

    assert response.status_code == 403


def test_search_page_links_only_to_matching_directories(
    client,
    search_layout_entries,
):
    response = client.get(
        "/search",
        params={"q": "项目"},
    )

    assert "/search?q=%E9%A1%B9%E7%9B%AE&amp;page=1&amp;selected=" not in response.text
    assert "/browse?path=/Public" in response.text
    assert "/browse?path=/Public/%E9%A1%B9%E7%9B%AE%E8%B5%84%E6%96%99" in response.text
    assert "/browse?path=/Archive/2025" in response.text
    assert "&amp;selected=" not in response.text


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
