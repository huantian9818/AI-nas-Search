import json
import re
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from nas_index.repositories.entries import EntryRepository
from nas_index.repositories.nas import NasRepository
from nas_index.services.search_summary import load_search_summary_payload
from nas_index.types import IndexedItem


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


def _seed_nas_with_two_shares(client) -> int:
    with Session(client.app.state.engine) as session:
        nas = NasRepository(session).create_server(
            name="Office NAS",
            base_url="http://nas.local",
            port=8080,
            use_https=False,
            enabled=True,
            sync_interval_minutes=15,
            username="indexer",
            password="secret",
        )
        EntryRepository(session).upsert_batch(
            nas.id,
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
        return nas.id


def test_browse_redirects_to_access_without_session(client):
    response = client.get("/browse", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/access?next=%2Fbrowse"


def test_dashboard_redirects_to_access_without_session(client):
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/access?next=%2F"


def test_search_redirect_preserves_query_for_login(client):
    response = client.get(
        "/search",
        params={"q": "苹果"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith(
        "/access?next="
    )
    assert "%2Fsearch%3Fq%3D" in response.headers["location"]


def test_browse_allows_admin_without_nas_access_and_shows_prompt(
    admin_client,
):
    response = admin_client.get("/browse")

    assert response.status_code == 200
    assert "请先登录 NAS 用户" in response.text
    assert 'href="/access?next=%2Fbrowse"' in response.text


def test_search_allows_admin_without_nas_access_and_shows_prompt(
    admin_client,
):
    response = admin_client.get(
        "/search",
        params={"q": "苹果"},
    )

    assert response.status_code == 200
    assert "请先登录 NAS 用户" in response.text
    assert (
        'href="/access?next=%2Fsearch%3Fq%3D%E8%8B%B9%E6%9E%9C"'
        in response.text
    )


def test_access_page_without_servers_tells_user_to_contact_admin(client):
    response = client.get("/access")

    assert response.status_code == 200
    assert "请联系管理员先完成 NAS 配置" in response.text
    assert "请先在设置中保存 NAS" not in response.text
    assert 'href="/admin/login"' in response.text


def test_access_page_shows_sid_expired_message(client):
    response = client.get(
        "/access",
        params={"reason": "sid_expired"},
    )

    assert response.status_code == 200
    assert "NAS 登录已过期，请重新登录" in response.text


def test_access_login_filters_browse_and_search_by_allowed_shares(
    client,
    monkeypatch,
):
    nas_id = _seed_nas_with_two_shares(client)

    async def fake_check_user_access(
        *,
        server,
        username,
        password,
        settings,
    ):
        assert server.id == nas_id
        assert username == "alice"
        assert password == "pw"
        assert settings is client.app.state.settings
        return ("/Public",)

    monkeypatch.setattr(
        client.app.state,
        "access_checker",
        fake_check_user_access,
        raising=False,
    )

    response = client.post(
        "/access",
        data={
            "nas_id": str(nas_id),
            "username": "alice",
            "password": "pw",
            "next": "/search?q=budget",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/search?q=budget"
    set_cookie = response.headers["set-cookie"].lower()
    assert "max-age=2592000" in set_cookie

    browse_response = client.get("/browse")
    assert browse_response.status_code == 200
    assert "Public" in browse_response.text
    assert "Finance" not in browse_response.text
    assert "退出当前用户" in browse_response.text

    search_response = client.get(
        "/search",
        params={"q": "budget"},
    )
    assert search_response.status_code == 200
    assert "public-budget.xlsx" not in search_response.text
    assert "finance-budget.xlsx" not in search_response.text
    summary_payload = _summary_payload(search_response.text)
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
    assert "public-budget.xlsx" in payload_names
    assert "finance-budget.xlsx" not in payload_names


def test_logout_current_user_deletes_session_and_cookie(client):
    token = client.app.state.access_store.create(
        nas_id=1,
        username="alice",
        share_paths=("/Public",),
    )
    client.cookies.set("nas_access", token)

    response = client.post(
        "/access/logout",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert client.app.state.access_store.get(token) is None
    assert "nas_access=" in response.headers["set-cookie"]
    assert "max-age=0" in response.headers["set-cookie"].lower()


def test_navigation_hides_logout_without_user_session(client):
    response = client.get("/access")

    assert response.status_code == 200
    assert 'aria-label="主导航"' not in response.text
    assert 'href="/browse"' not in response.text
    assert 'href="/search"' not in response.text
    assert response.text.count('href="/"') == 1
    assert "退出当前用户" not in response.text
    assert "退出管理员用户" not in response.text
    assert 'href="/access"' not in response.text


def test_navigation_hides_access_link_with_user_session(
    client,
    web_public_access,
):
    response = client.get("/browse")

    assert response.status_code == 200
    assert 'href="/access"' not in response.text
    assert response.text.count('href="/"') == 1
    assert 'href="/browse"' in response.text
    assert 'href="/search"' in response.text
    assert 'href="/settings"' not in response.text
    assert "退出当前用户" in response.text
    assert "退出管理员用户" not in response.text
