from __future__ import annotations

import json
from pathlib import Path

import pytest
from config import (
    AiMemoryConfig,
    _secret_keys,
    get_config_schema,
    load_config,
    save_config,
)


def test_config_defaults() -> None:
    cfg = AiMemoryConfig()
    assert cfg.server_url == "http://127.0.0.1:49374"
    assert cfg.api_key == ""
    assert cfg.auth_token == ""
    assert cfg.workspace == "hermes"
    assert cfg.project == "hermes-default"


def test_config_custom_values() -> None:
    cfg = AiMemoryConfig(
        server_url="http://custom:49374",
        auth_token="abc123",
        workspace="custom-ws",
        project="custom-proj",
    )
    assert cfg.server_url == "http://custom:49374"
    assert cfg.auth_token == "abc123"
    assert cfg.workspace == "custom-ws"
    assert cfg.project == "custom-proj"


def test_get_config_schema() -> None:
    schema = get_config_schema()
    keys = {item["key"] for item in schema}
    assert "server_url" in keys
    assert "api_key" in keys
    assert "auth_token" in keys
    assert "workspace" in keys
    assert "project" in keys


def test_save_config_creates_file(tmp_path: Path) -> None:
    save_config({"server_url": "http://test:49374"}, str(tmp_path))
    p = tmp_path / "ai-memory.json"
    assert p.exists()
    data = json.loads(p.read_text())
    assert data["server_url"] == "http://test:49374"


def test_save_config_merges_existing(tmp_path: Path) -> None:
    p = tmp_path / "ai-memory.json"
    p.write_text(json.dumps({"existing_key": "value"}))
    save_config({"server_url": "http://test:49374"}, str(tmp_path))
    data = json.loads(p.read_text())
    assert data["existing_key"] == "value"
    assert data["server_url"] == "http://test:49374"


def test_load_config_from_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_MEMORY_SERVER_URL", raising=False)
    monkeypatch.delenv("AI_MEMORY_AUTH_TOKEN", raising=False)
    p = tmp_path / "ai-memory.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "server_url": "http://file:49374",
                "auth_token": "file-token",
                "workspace": "file-ws",
                "project": "file-proj",
            }
        )
    )
    cfg = load_config(str(tmp_path))
    assert cfg.server_url == "http://file:49374"
    assert cfg.auth_token == "file-token"
    assert cfg.workspace == "file-ws"
    assert cfg.project == "file-proj"


def test_load_config_file_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_MEMORY_SERVER_URL", raising=False)
    monkeypatch.delenv("AI_MEMORY_AUTH_TOKEN", raising=False)
    cfg = load_config(str(tmp_path))
    assert cfg.server_url == "http://127.0.0.1:49374"
    assert cfg.auth_token == ""


def test_save_config_creates_parent_dirs(tmp_path: Path) -> None:
    deep = tmp_path / "a" / "b" / "c"
    save_config({"server_url": "http://test:49374"}, str(deep))
    assert (deep / "ai-memory.json").exists()


def test_load_config_falls_back_to_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_MEMORY_SERVER_URL", "http://env:49374")
    monkeypatch.setenv("AI_MEMORY_AUTH_TOKEN", "env-token")
    cfg = load_config(str(tmp_path))
    assert cfg.server_url == "http://env:49374"
    assert cfg.auth_token == "env-token"


def test_config_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_MEMORY_SERVER_URL", raising=False)
    monkeypatch.delenv("AI_MEMORY_AUTH_TOKEN", raising=False)
    values = {
        "server_url": "http://roundtrip:49374",
        "auth_token": "roundtrip-token",
        "workspace": "roundtrip-ws",
        "project": "roundtrip-proj",
    }
    skipped = save_config(values, str(tmp_path))
    assert "auth_token" in skipped
    cfg = load_config(str(tmp_path))
    assert cfg.server_url == "http://roundtrip:49374"
    assert cfg.auth_token == ""  # secret was not written to disk
    assert cfg.workspace == "roundtrip-ws"
    assert cfg.project == "roundtrip-proj"


def test_load_config_corrupt_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_MEMORY_SERVER_URL", raising=False)
    monkeypatch.delenv("AI_MEMORY_AUTH_TOKEN", raising=False)
    p = tmp_path / "ai-memory.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not valid json")
    cfg = load_config(str(tmp_path))
    assert cfg.server_url == "http://127.0.0.1:49374"
    assert cfg.auth_token == ""


def test_load_config_env_overrides_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "ai-memory.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "server_url": "http://file:49374",
                "auth_token": "file-token",
            }
        )
    )
    monkeypatch.setenv("AI_MEMORY_SERVER_URL", "http://env:49374")
    monkeypatch.setenv("AI_MEMORY_AUTH_TOKEN", "env-token")
    cfg = load_config(str(tmp_path))
    assert cfg.server_url == "http://env:49374"
    assert cfg.auth_token == "env-token"


def test_load_config_filters_extra_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AI_MEMORY_SERVER_URL", raising=False)
    monkeypatch.delenv("AI_MEMORY_AUTH_TOKEN", raising=False)
    p = tmp_path / "ai-memory.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "server_url": "http://extra:49374",
                "auth_token": "extra-token",
                "unknown_key": "should be ignored",
            }
        )
    )
    cfg = load_config(str(tmp_path))
    assert cfg.server_url == "http://extra:49374"
    assert not hasattr(cfg, "unknown_key")


def test_save_config_does_not_write_secrets_to_disk(tmp_path: Path) -> None:
    skipped = save_config(
        {"server_url": "http://test:49374", "auth_token": "secret123", "api_key": "key456"},
        str(tmp_path),
    )
    assert "auth_token" in skipped
    assert "api_key" in skipped
    data = json.loads((tmp_path / "ai-memory.json").read_text())
    assert data["server_url"] == "http://test:49374"
    assert "auth_token" not in data
    assert "api_key" not in data


def test_save_config_strips_existing_secrets_from_file(tmp_path: Path) -> None:
    p = tmp_path / "ai-memory.json"
    p.write_text(
        json.dumps(
            {
                "server_url": "http://old:49374",
                "auth_token": "old-secret",
                "api_key": "old-key",
            }
        )
    )
    save_config({"server_url": "http://new:49374"}, str(tmp_path))
    data = json.loads(p.read_text())
    assert data["server_url"] == "http://new:49374"
    assert "auth_token" not in data
    assert "api_key" not in data


def test_save_config_returns_skipped_secrets(tmp_path: Path) -> None:
    skipped = save_config(
        {"auth_token": "tok", "api_key": "key", "workspace": "ws"},
        str(tmp_path),
    )
    assert set(skipped) == {"auth_token", "api_key"}
    data = json.loads((tmp_path / "ai-memory.json").read_text())
    assert data["workspace"] == "ws"


def test_secret_keys_returns_env_var_mapping() -> None:
    secrets = _secret_keys()
    assert secrets["auth_token"] == "AI_MEMORY_AUTH_TOKEN"
    assert secrets["api_key"] == "AI_MEMORY_API_KEY"


def test_get_config_schema_has_env_only() -> None:
    schema = get_config_schema()
    for item in schema:
        if item["key"] in ("auth_token", "api_key"):
            assert item.get("secret") is True
            assert item.get("env_only") is True
            assert "env_var" in item


def test_workspace_and_project_are_not_env_overridable(
    mock_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scope must come from ai-memory.json, never from the shell.

    The ai-memory CLI exports AI_MEMORY_* into interactive shells, while the
    Hermes gateway runs under systemd and sees no such shell. An env override
    would let `hermes` started from a terminal write to a different project
    than the daemon, splitting one profile's memory across two scopes with no
    visible signal.
    """
    p = mock_home / "ai-memory.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"workspace": "hermes", "project": "orchestrator"}))

    monkeypatch.setenv("AI_MEMORY_WORKSPACE", "personal-oss")
    monkeypatch.setenv("AI_MEMORY_PROJECT", "some-other-repo")

    config = load_config(str(mock_home))
    assert config.workspace == "hermes"
    assert config.project == "orchestrator"


def test_recall_scope_is_env_overridable(mock_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Scope WIDTH stays env-settable: it is the documented opt-in knob."""
    mock_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AI_MEMORY_RECALL_SCOPE", "global")
    assert load_config(str(mock_home)).recall_scope == "global"


def test_entry_point_ignores_workspace_and_project_env(
    mock_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same guard on the register() path Hermes actually loads."""
    import importlib
    import sys

    p = mock_home / "ai-memory.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"workspace": "hermes", "project": "orchestrator"}))

    monkeypatch.setenv("HERMES_HOME", str(mock_home))
    monkeypatch.setenv("AI_MEMORY_WORKSPACE", "personal-oss")
    monkeypatch.setenv("AI_MEMORY_PROJECT", "some-other-repo")

    entry = importlib.import_module("__init__") if "__init__" in sys.modules else None
    if entry is None:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "ai_memory_entry",
            Path(__file__).parent.parent / "plugins/memory/ai-memory/__init__.py",
        )
        assert spec and spec.loader
        entry = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(entry)

    captured: dict[str, object] = {}

    class _Ctx:
        def register_memory_provider(self, provider: object) -> None:
            captured["provider"] = provider

    entry.register(_Ctx())
    provider = captured["provider"]
    assert provider._config.workspace == "hermes"  # type: ignore[attr-defined]
    assert provider._config.project == "orchestrator"  # type: ignore[attr-defined]
