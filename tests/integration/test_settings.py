import re

from sqlalchemy.orm import Session

from nas_index.qnap.client import QnapProbeResult
from nas_index.repositories.nas import NasRepository
from nas_index.types import NasConnection


def _nas_form(name: str = "Office") -> dict[str, str]:
    return {
        "name": name,
        "host": f"{name.lower()}.local",
        "port": "8080",
        "enabled": "on",
        "sync_interval_minutes": "30",
        "username": "indexer",
        "password": "secret",
    }


def _tested_token(
    client,
    *,
    host: str,
    port: int,
    use_https: bool,
    username: str,
    password: str,
    skip_tls_verify: bool = False,
) -> str:
    return client.app.state.connection_test_store.create(
        NasConnection(
            base_url=(
                f"https://{host}"
                if use_https
                else f"http://{host}"
            ),
            port=port,
            use_https=use_https,
            username=username,
            password=password,
            skip_tls_verify=skip_tls_verify,
        )
    )


def _create_nas(client, name: str = "Office") -> int:
    data = _nas_form(name)
    data["connection_test_token"] = _tested_token(
        client,
        host=data["host"],
        port=int(data["port"]),
        use_https=False,
        username=data["username"],
        password=data["password"],
    )
    response = client.post(
        "/settings/nas",
        data=data,
        follow_redirects=False,
    )
    assert response.status_code == 303
    with Session(client.app.state.engine) as session:
        server = next(
            server
            for server in NasRepository(session).list_servers()
            if server.name == name
        )
        return server.id


def test_settings_can_create_multiple_nas_servers(admin_client):
    _create_nas(admin_client, "Office")
    _create_nas(admin_client, "Lab")

    with Session(admin_client.app.state.engine) as session:
        names = [
            server.name
            for server in NasRepository(session).list_servers()
        ]

    assert names == ["Lab", "Office"]


def test_settings_can_update_nas_and_preserve_blank_password(admin_client):
    nas_id = _create_nas(admin_client)
    token = _tested_token(
        admin_client,
        host="updated.local",
        port=8443,
        use_https=True,
        username="indexer2",
        password="secret",
        skip_tls_verify=True,
    )

    response = admin_client.post(
        f"/settings/nas/{nas_id}",
        data={
            "name": "Office Updated",
            "host": "updated.local",
            "port": "8443",
            "skip_tls_verify": "on",
            "enabled": "on",
            "sync_interval_minutes": "10",
            "username": "indexer2",
            "password": "",
            "connection_test_token": token,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(admin_client.app.state.engine) as session:
        repository = NasRepository(session)
        server = repository.get_server(nas_id)
        credential = repository.get_credential(nas_id)

    assert server.name == "Office Updated"
    assert server.base_url == "https://updated.local"
    assert server.port == 8443
    assert server.use_https is True
    assert server.skip_tls_verify is True
    assert server.sync_interval_minutes == 10
    assert credential.username == "indexer2"
    assert credential.password == "secret"

    page = admin_client.get("/settings")
    assert "secret" not in page.text
    assert "留空保留原密码" in page.text


def test_settings_page_groups_nas_forms_for_scanning_workflow(admin_client):
    _create_nas(admin_client, "Office")

    response = admin_client.get("/settings")

    assert response.status_code == 200
    assert 'class="page compact-page settings-page"' in response.text
    assert 'class="nas-config-list"' in response.text
    assert "settings-fields-primary" in response.text
    assert "settings-fields-secondary" in response.text
    assert "data-nas-config-form" in response.text
    assert "data-connection-field" in response.text
    assert "data-test-connection" in response.text
    assert "data-save-config" in response.text
    assert "连接与同步" in response.text
    assert "新增 NAS" in response.text
    assert "完整重扫间隔" not in response.text
    assert "同步间隔（分钟）" in response.text
    assert 'name="use_https"' not in response.text
    assert 'name="skip_tls_verify"' in response.text
    assert "忽略 HTTPS 证书校验" in response.text
    assert "仅在 HTTPS 时生效" in response.text


def test_settings_css_uses_responsive_management_layout(client):
    response = client.get("/static/app.css")

    assert response.status_code == 200
    assert ".settings-fields-primary" in response.text
    assert "grid-template-columns: 100px 140px 60px;" in response.text
    assert "grid-template-columns: 100px 140px 130px;" in response.text
    assert ".settings-field-host" in response.text
    assert "width: 140px;" in response.text
    assert ".settings-interval-label" in response.text
    assert "white-space: nowrap;" in response.text
    assert "@media (max-width: 720px)" in response.text


def test_connection_test_returns_sanitized_error(
    admin_client,
    monkeypatch,
):
    async def fail(_connection):
        raise RuntimeError("secret-token")

    monkeypatch.setattr(
        "nas_index.web.routes.settings.test_connection",
        fail,
    )
    response = admin_client.post(
        "/settings/nas/test",
        data=_nas_form(),
    )

    assert response.status_code == 200
    assert "连接测试失败" in response.text
    assert "secret-token" not in response.text


def test_first_save_requires_password(admin_client):
    response = admin_client.post(
        "/settings/nas",
        data={
            "name": "Office",
            "host": "nas.local",
            "port": "8080",
            "enabled": "on",
            "sync_interval_minutes": "30",
            "username": "indexer",
            "password": "",
        },
    )

    assert response.status_code == 422
    assert "首次保存时必须输入索引账号密码" in response.text


def test_create_requires_successful_current_connection_test(
    admin_client,
):
    response = admin_client.post(
        "/settings/nas",
        data=_nas_form(),
    )

    assert response.status_code == 422
    assert "请先使用当前连接信息测试成功后再保存" in response.text


def test_current_form_connection_test_issues_save_token(
    admin_client,
    monkeypatch,
):
    async def succeed(
        *,
        host,
        port,
        username,
        password,
        skip_tls_verify,
        settings,
    ):
        assert host == "office.local"
        assert port == 8080
        assert username == "indexer"
        assert password == "secret"
        assert skip_tls_verify is False
        assert settings is admin_client.app.state.settings
        return QnapProbeResult(
            connection=NasConnection(
                "http://office.local",
                8080,
                False,
                "indexer",
                "secret",
                False,
            ),
            share_count=3,
        )

    monkeypatch.setattr(
        "nas_index.web.routes.settings.test_connection",
        succeed,
    )

    response = admin_client.post(
        "/settings/nas/test",
        data=_nas_form(),
    )

    assert response.status_code == 200
    assert "连接成功（HTTP），可访问 3 个共享目录" in response.text
    match = re.search(
        r'name="connection_test_token"\s+value="([^"]+)"',
        response.text,
    )
    assert match is not None

    data = _nas_form()
    data["connection_test_token"] = match.group(1)
    save = admin_client.post(
        "/settings/nas",
        data=data,
        follow_redirects=False,
    )
    assert save.status_code == 303


def test_connection_test_autodetects_https_and_saves_final_protocol(
    admin_client,
    monkeypatch,
):
    async def succeed(
        *,
        host,
        port,
        username,
        password,
        skip_tls_verify,
        settings,
    ):
        assert host == "192.168.1.16"
        assert port == 5001
        assert skip_tls_verify is True
        assert settings is admin_client.app.state.settings
        return QnapProbeResult(
            connection=NasConnection(
                "https://192.168.1.16",
                5001,
                True,
                username,
                password,
                True,
            ),
            share_count=6,
        )

    monkeypatch.setattr(
        "nas_index.web.routes.settings.test_connection",
        succeed,
    )

    response = admin_client.post(
        "/settings/nas/test",
        data={
            **_nas_form("Factory"),
            "host": "192.168.1.16",
            "port": "5001",
            "skip_tls_verify": "on",
        },
    )

    assert response.status_code == 200
    assert "连接成功（HTTPS），可访问 6 个共享目录" in response.text
    match = re.search(
        r'name="connection_test_token"\s+value="([^"]+)"',
        response.text,
    )
    assert match is not None

    save = admin_client.post(
        "/settings/nas",
        data={
            **_nas_form("Factory"),
            "host": "192.168.1.16",
            "port": "5001",
            "skip_tls_verify": "on",
            "connection_test_token": match.group(1),
        },
        follow_redirects=False,
    )

    assert save.status_code == 303

    with Session(admin_client.app.state.engine) as session:
        server = next(
            server
            for server in NasRepository(session).list_servers()
            if server.name == "Factory"
        )

    assert server.base_url == "https://192.168.1.16"
    assert server.use_https is True
    assert server.skip_tls_verify is True


def test_connection_test_token_rejects_changed_connection(
    admin_client,
):
    data = _nas_form()
    data["connection_test_token"] = _tested_token(
        admin_client,
        host=data["host"],
        port=int(data["port"]),
        use_https=False,
        username=data["username"],
        password=data["password"],
    )
    data["host"] = "changed.local"

    response = admin_client.post(
        "/settings/nas",
        data=data,
    )

    assert response.status_code == 422
    assert "请先使用当前连接信息测试成功后再保存" in response.text


def test_existing_nas_connection_test_uses_saved_password_when_blank(
    admin_client,
    monkeypatch,
):
    nas_id = _create_nas(admin_client)
    seen_passwords = []

    async def succeed(
        *,
        host,
        port,
        username,
        password,
        skip_tls_verify,
        settings,
    ):
        seen_passwords.append(password)
        assert host == "office.local"
        assert port == 8080
        assert username == "indexer"
        assert skip_tls_verify is False
        assert settings is admin_client.app.state.settings
        return QnapProbeResult(
            connection=NasConnection(
                "http://office.local",
                8080,
                False,
                username,
                password,
                False,
            ),
            share_count=2,
        )

    monkeypatch.setattr(
        "nas_index.web.routes.settings.test_connection",
        succeed,
    )
    data = _nas_form()
    data.update(
        {
            "nas_id": str(nas_id),
            "password": "",
        }
    )

    response = admin_client.post(
        "/settings/nas/test",
        data=data,
    )

    assert response.status_code == 200
    assert seen_passwords == ["secret"]
    assert 'name="connection_test_token"' in response.text
