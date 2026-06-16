from sqlalchemy.orm import Session

from nas_index.repositories.nas import NasRepository


def _create_nas(client, name: str = "Office") -> int:
    response = client.post(
        "/settings/nas",
        data={
            "name": name,
            "host": f"{name.lower()}.local",
            "port": "8080",
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
        server = next(
            server
            for server in NasRepository(session).list_servers()
            if server.name == name
        )
        return server.id


def test_settings_can_create_multiple_nas_servers(client):
    _create_nas(client, "Office")
    _create_nas(client, "Lab")

    with Session(client.app.state.engine) as session:
        names = [
            server.name
            for server in NasRepository(session).list_servers()
        ]

    assert names == ["Lab", "Office"]


def test_settings_can_update_nas_and_preserve_blank_password(client):
    nas_id = _create_nas(client)

    response = client.post(
        f"/settings/nas/{nas_id}",
        data={
            "name": "Office Updated",
            "host": "updated.local",
            "port": "8443",
            "use_https": "on",
            "enabled": "on",
            "sync_interval_minutes": "10",
            "full_resync_interval_hours": "12",
            "username": "indexer2",
            "password": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(client.app.state.engine) as session:
        repository = NasRepository(session)
        server = repository.get_server(nas_id)
        credential = repository.get_credential(nas_id)

    assert server.name == "Office Updated"
    assert server.base_url == "https://updated.local"
    assert server.port == 8443
    assert server.sync_interval_minutes == 10
    assert credential.username == "indexer2"
    assert credential.password == "secret"

    page = client.get("/settings")
    assert "secret" not in page.text
    assert "留空保留原密码" in page.text


def test_connection_test_returns_sanitized_error(
    client,
    monkeypatch,
):
    nas_id = _create_nas(client)

    async def fail(_connection):
        raise RuntimeError("secret-token")

    monkeypatch.setattr(
        "nas_index.web.routes.settings.test_connection",
        fail,
    )
    response = client.post(f"/settings/nas/{nas_id}/test")

    assert response.status_code == 200
    assert "连接测试失败" in response.text
    assert "secret-token" not in response.text


def test_first_save_requires_password(client):
    response = client.post(
        "/settings/nas",
        data={
            "name": "Office",
            "host": "nas.local",
            "port": "8080",
            "enabled": "on",
            "sync_interval_minutes": "30",
            "full_resync_interval_hours": "24",
            "username": "indexer",
            "password": "",
        },
    )

    assert response.status_code == 422
    assert "首次保存时必须输入索引账号密码" in response.text
