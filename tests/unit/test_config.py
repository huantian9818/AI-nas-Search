from nas_index.config import load_settings


def test_load_settings_reads_local_toml_config(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[app]
admin_password = "file-secret"

[ai]
api_key = "sk-file"
base_url = "https://api.openai.com/v1"
model = "deepseek-v4"
timeout_seconds = 45
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("NAS_INDEX_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("NAS_INDEX_AI_API_KEY", raising=False)
    monkeypatch.delenv("NAS_INDEX_AI_BASE_URL", raising=False)
    monkeypatch.delenv("NAS_INDEX_AI_MODEL", raising=False)
    monkeypatch.delenv("NAS_INDEX_AI_TIMEOUT_SECONDS", raising=False)

    settings = load_settings(config_path)

    assert settings.admin_password == "file-secret"
    assert settings.ai_api_key == "sk-file"
    assert settings.ai_base_url == "https://api.openai.com/v1"
    assert settings.ai_model == "deepseek-v4"
    assert settings.ai_timeout_seconds == 45


def test_load_settings_allows_env_to_override_toml_config(
    tmp_path,
    monkeypatch,
):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[app]
admin_password = "file-secret"

[ai]
api_key = "sk-file"
model = "deepseek-v4"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("NAS_INDEX_ADMIN_PASSWORD", "env-secret")
    monkeypatch.setenv("NAS_INDEX_AI_API_KEY", "sk-env")
    monkeypatch.setenv("NAS_INDEX_AI_MODEL", "env-model")

    settings = load_settings(config_path)

    assert settings.admin_password == "env-secret"
    assert settings.ai_api_key == "sk-env"
    assert settings.ai_model == "env-model"
