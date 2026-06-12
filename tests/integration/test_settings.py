def test_save_settings_and_preserve_blank_password(client):
    response = client.post(
        "/settings",
        data={
            "host": "nas.local",
            "port": "8080",
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
            "username": "indexer",
            "password": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get("/settings")
    assert "secret" not in page.text
    assert "密码已保存" in page.text


def test_connection_test_returns_sanitized_error(
    client,
    monkeypatch,
):
    client.post(
        "/settings",
        data={
            "host": "nas.local",
            "port": "8080",
            "username": "indexer",
            "password": "secret",
        },
    )

    async def fail(_connection):
        raise RuntimeError("secret-token")

    monkeypatch.setattr(
        "nas_index.web.routes.settings.test_connection",
        fail,
    )
    response = client.post("/settings/test")

    assert response.status_code == 200
    assert "连接测试失败" in response.text
    assert "secret-token" not in response.text


def test_first_save_requires_password(client):
    response = client.post(
        "/settings",
        data={
            "host": "nas.local",
            "port": "8080",
            "username": "indexer",
            "password": "",
        },
    )

    assert response.status_code == 422
    assert "首次保存时必须输入密码" in response.text
