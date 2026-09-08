from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from config import AiMemoryConfig
from provider import AiMemoryProvider


@pytest.fixture
def provider() -> AiMemoryProvider:
    cfg = AiMemoryConfig(
        server_url="http://localhost:49374",
        auth_token="test-token",
        workspace="hermes",
        project="hermes-test",
    )
    p = AiMemoryProvider(config=cfg)
    # initialize() now fetches a handoff; keep unit tests off the
    # network (a real ai-memory may well be listening on :49374).
    p._client = MagicMock()
    p._client.fetch_handoff.return_value = None
    return p


def test_provider_name(provider: AiMemoryProvider) -> None:
    assert provider.name == "ai-memory"


def test_is_available_with_token(provider: AiMemoryProvider) -> None:
    assert provider.is_available() is True


def test_is_available_without_token() -> None:
    # ai-memory is available without auth tokens — only server_url matters.
    p = AiMemoryProvider(config=AiMemoryConfig())
    assert p.is_available() is True


def test_is_available_without_server_url() -> None:
    p = AiMemoryProvider(config=AiMemoryConfig(server_url=""))
    assert p.is_available() is False


def test_initialize_sets_session_id(provider: AiMemoryProvider) -> None:
    provider.initialize("session-123", agent_identity="test-profile")
    assert provider.session_id == "session-123"
    # A project configured in ai-memory.json is no longer clobbered.
    assert provider._config.project == "hermes-test"


def test_get_config_schema(provider: AiMemoryProvider) -> None:
    schema = provider.get_config_schema()
    assert len(schema) >= 4


def test_save_config(tmp_path: Path, provider: AiMemoryProvider) -> None:
    hermes_home = str(tmp_path)
    provider.save_config({"server_url": "http://custom:49374"}, hermes_home)
    p = tmp_path / "ai-memory.json"
    assert p.exists()


def test_get_tool_schemas(provider: AiMemoryProvider) -> None:
    schemas = provider.get_tool_schemas()
    names = [s["name"] for s in schemas]
    assert "ai_memory_search" in names
    assert "ai_memory_write" in names
    assert "ai_memory_status" in names


def test_tool_schemas_use_parameters_not_input_schema(
    provider: AiMemoryProvider,
) -> None:
    """Hermes and every model adapter read `parameters`.

    With `input_schema` the openai-codex preflight raises
    "tools[N] is missing valid parameters" and the whole model request fails,
    so this is the blocking contract, not a style preference.
    """
    for schema in provider.get_tool_schemas():
        assert "input_schema" not in schema, schema["name"]
        assert isinstance(schema["parameters"], dict), schema["name"]
        assert schema["parameters"]["type"] == "object"


def test_search_tool_schema_requires_query(provider: AiMemoryProvider) -> None:
    search = next(s for s in provider.get_tool_schemas() if s["name"] == "ai_memory_search")
    assert search["parameters"]["required"] == ["query"]
    assert "query" in search["parameters"]["properties"]


def test_handle_tool_call_search(provider: AiMemoryProvider) -> None:
    provider._client.search = MagicMock(return_value=[{"path": "test.md"}])
    result = json.loads(provider.handle_tool_call("ai_memory_search", {"query": "test"}))
    assert result["ok"] is True
    assert len(result["results"]) == 1


def test_search_defaults_invalid_max_results(provider: AiMemoryProvider) -> None:
    provider._client.search = MagicMock(return_value=[])
    provider._search({"query": "test", "max_results": "invalid"})
    assert provider._client.search.call_args.kwargs["limit"] == 5


def test_handle_tool_call_write(provider: AiMemoryProvider) -> None:
    provider._client.write_page = MagicMock(return_value={"ok": True})
    result = json.loads(
        provider.handle_tool_call(
            "ai_memory_write",
            {"path": "notes/test.md", "body": "# Hello"},
        )
    )
    assert result["ok"] is True
    assert result["written"] == "notes/test.md"


def test_handle_tool_call_write_accepts_current_api_page_id(provider: AiMemoryProvider) -> None:
    provider._client.write_page = MagicMock(return_value={"page_id": "page-123"})
    result = json.loads(
        provider.handle_tool_call(
            "ai_memory_write",
            {"path": "notes/test.md", "body": "# Hello"},
        )
    )
    assert result == {"ok": True, "written": "notes/test.md"}


def test_handle_tool_call_status(provider: AiMemoryProvider) -> None:
    provider._client.status = MagicMock(return_value={"ok": True, "pages": 10})
    result = json.loads(provider.handle_tool_call("ai_memory_status", {}))
    assert result["ok"] is True


def test_handle_tool_call_returns_json_string(provider: AiMemoryProvider) -> None:
    provider._client.search = MagicMock(return_value=[])
    result = provider.handle_tool_call("ai_memory_search", {"query": "test"})
    assert isinstance(result, str)
    parsed = json.loads(result)
    assert "ok" in parsed


def test_handle_tool_call_unknown(provider: AiMemoryProvider) -> None:
    with pytest.raises(ValueError, match="Unknown tool"):
        provider.handle_tool_call("unknown_tool", {})


def test_system_prompt_block(provider: AiMemoryProvider) -> None:
    block = provider.system_prompt_block()
    assert "ai-memory" in block


def test_prefetch_returns_context(provider: AiMemoryProvider) -> None:
    provider._search = MagicMock(
        return_value={
            "ok": True,
            "results": [
                {"snippet": "context 1"},
                {"snippet": "context 2"},
            ],
        }
    )
    result = provider.prefetch("test query")
    assert result is not None
    assert "context 1" in result
    assert "context 2" in result


def test_prefetch_returns_empty_on_no_results(provider: AiMemoryProvider) -> None:
    provider._search = MagicMock(return_value={"ok": True, "results": []})
    result = provider.prefetch("test query")
    assert result == ""


def test_sync_turn_spawns_daemon(provider: AiMemoryProvider) -> None:
    provider._client.send_hook = MagicMock()
    provider.sync_turn("user msg", "assistant msg", session_id="sess-1")
    time.sleep(0.05)
    provider._client.send_hook.assert_called_once()


def test_sync_turn_absorbs_extra_kwargs(provider: AiMemoryProvider) -> None:
    provider._client.send_hook = MagicMock()
    provider.sync_turn(
        "user msg", "assistant msg", session_id="sess-1", context="extra", extra_key="val"
    )
    time.sleep(0.05)
    provider._client.send_hook.assert_called_once()


def test_on_session_end_spawns_daemon(provider: AiMemoryProvider) -> None:
    provider._client.send_hook = MagicMock()
    provider.on_session_end([{"role": "user", "content": "hello"}])
    time.sleep(0.05)
    provider._client.send_hook.assert_called_once()


def test_on_session_end_absorbs_extra_kwargs(provider: AiMemoryProvider) -> None:
    provider._client.send_hook = MagicMock()
    provider.on_session_end([{"role": "user", "content": "hello"}], extra_key="val")
    time.sleep(0.05)
    provider._client.send_hook.assert_called_once()


def test_on_memory_write_add_appends_to_existing_page(
    provider: AiMemoryProvider,
) -> None:
    """Hermes sends add | replace | remove, never "write"/"append"."""
    provider._client.read_page = MagicMock(
        return_value="# Hermes memory mirror (memory)\n\nfirst entry\n"
    )
    provider._client.write_page = MagicMock()

    provider.on_memory_write("add", "memory", "second entry")

    body = provider._client.write_page.call_args.kwargs["body"]
    assert "first entry" in body, "an add must not clobber existing entries"
    assert "second entry" in body
    assert provider._client.write_page.call_args.kwargs["path"] == ("hermes-memory/memory.md")


def test_on_memory_write_add_is_idempotent(provider: AiMemoryProvider) -> None:
    provider._client.read_page = MagicMock(return_value="dup entry")
    provider._client.write_page = MagicMock()
    provider.on_memory_write("add", "memory", "dup entry")
    provider._client.write_page.assert_not_called()


def test_on_memory_write_replace_swaps_matching_entry(
    provider: AiMemoryProvider,
) -> None:
    provider._client.read_page = MagicMock(return_value="keep me\n---\nstale fact")
    provider._client.write_page = MagicMock()

    provider.on_memory_write("replace", "memory", "fresh fact", metadata={"old_text": "stale"})

    body = provider._client.write_page.call_args.kwargs["body"]
    assert "fresh fact" in body
    assert "stale fact" not in body
    assert "keep me" in body


def test_on_memory_write_remove_drops_matching_entry(
    provider: AiMemoryProvider,
) -> None:
    """remove carries an empty content; old_text identifies the entry."""
    provider._client.read_page = MagicMock(return_value="keep me\n---\nstale fact")
    provider._client.write_page = MagicMock()

    provider.on_memory_write("remove", "memory", "", metadata={"old_text": "stale"})

    body = provider._client.write_page.call_args.kwargs["body"]
    assert "stale fact" not in body
    assert "keep me" in body


def test_on_memory_write_skips_unmatched_old_text(provider: AiMemoryProvider) -> None:
    provider._client.read_page = MagicMock(return_value="only entry")
    provider._client.write_page = MagicMock()
    provider.on_memory_write("remove", "memory", "", metadata={"old_text": "absent"})
    provider._client.write_page.assert_not_called()


def test_on_memory_write_skips_ambiguous_old_text(provider: AiMemoryProvider) -> None:
    """An ambiguous match must not guess: the wrong guess deletes another
    entry. Mirrors MemoryStore._find_unique_match."""
    provider._client.read_page = MagicMock(return_value="fact one\n---\nfact two")
    provider._client.write_page = MagicMock()
    provider.on_memory_write("remove", "memory", "", metadata={"old_text": "fact"})
    provider._client.write_page.assert_not_called()


def test_on_memory_write_requires_old_text_for_edits(
    provider: AiMemoryProvider,
) -> None:
    provider._client.read_page = MagicMock(return_value="entry")
    provider._client.write_page = MagicMock()
    provider.on_memory_write("replace", "memory", "new")
    provider._client.write_page.assert_not_called()


def test_on_memory_write_skips_other_actions(provider: AiMemoryProvider) -> None:
    provider._client.read_page = MagicMock(return_value="")
    provider._client.write_page = MagicMock()
    for action in ("write", "append", "delete"):
        provider.on_memory_write(action, "memory", "# content")
    provider._client.write_page.assert_not_called()


def test_on_memory_write_sanitises_target_path(provider: AiMemoryProvider) -> None:
    provider._client.read_page = MagicMock(return_value=None)
    provider._client.write_page = MagicMock()
    provider.on_memory_write("add", "../../etc/passwd", "entry")
    path = provider._client.write_page.call_args.kwargs["path"]
    assert path == "hermes-memory/etcpasswd.md"


def test_queue_prefetch_accepts_session_id(provider: AiMemoryProvider) -> None:
    """MemoryManager.queue_prefetch_all always passes session_id=; the previous
    positional-only signature raised TypeError, which Hermes swallowed."""
    provider._client.search = MagicMock(return_value=[{"snippet": "recalled"}])

    provider.queue_prefetch("test query", session_id="sess-1")
    time.sleep(0.1)

    assert provider.prefetch("test query", session_id="sess-1") == "recalled"


def test_prefetch_consumes_queued_result_once(provider: AiMemoryProvider) -> None:
    provider._client.search = MagicMock(return_value=[{"snippet": "recalled"}])
    provider.queue_prefetch("q", session_id="s")
    time.sleep(0.1)
    provider._client.search.reset_mock()

    assert provider.prefetch("q", session_id="s") == "recalled"
    provider._client.search.assert_not_called()

    # Cache is single-use: the next prefetch searches again.
    provider.prefetch("q", session_id="s")
    provider._client.search.assert_called_once()


def test_recall_status_reflects_last_prefetch(provider: AiMemoryProvider) -> None:
    provider._client.search = MagicMock(return_value=[{"snippet": "a"}, {"snippet": "b"}])
    provider.prefetch("q")
    status = provider.recall_status()
    assert status is not None
    assert status.provider_label == "ai-memory"
    assert status.count == 2

    # A later empty prefetch must clear it, never leave a stale count.
    provider._client.search = MagicMock(return_value=[])
    provider.prefetch("q2")
    assert provider.recall_status() is None


def test_search_is_scoped_to_configured_project(provider: AiMemoryProvider) -> None:
    provider._client.search = MagicMock(return_value=[])
    provider.handle_tool_call("ai_memory_search", {"query": "q"})
    kwargs = provider._client.search.call_args.kwargs
    assert kwargs["workspace"] == provider._config.workspace
    assert kwargs["project"] == provider._config.project


def test_search_global_scope_is_opt_in(provider: AiMemoryProvider) -> None:
    provider._config.recall_scope = "global"
    provider._client.search = MagicMock(return_value=[])
    provider.handle_tool_call("ai_memory_search", {"query": "q"})
    kwargs = provider._client.search.call_args.kwargs
    assert kwargs["workspace"] is None
    assert kwargs["project"] is None


def test_initialize_resolves_workspace_from_kwargs(provider: AiMemoryProvider) -> None:
    provider.initialize("sess-1", profile="test-profile", ai_memory_workspace="ws-custom")
    assert provider._config.workspace == "ws-custom"


def test_initialize_project_kwarg_overrides_profile(provider: AiMemoryProvider) -> None:
    provider.initialize("sess-1", profile="test-profile", project="custom-proj")
    assert provider._config.project == "custom-proj"


def test_initialize_preserves_default_workspace(provider: AiMemoryProvider) -> None:
    provider.initialize("sess-1", agent_identity="test-profile")
    assert provider._config.workspace == "hermes"


def test_initialize_uses_identity_when_no_configured_project(
    provider: AiMemoryProvider,
) -> None:
    provider._config.project = ""
    provider.initialize("sess-1", agent_identity="custom-profile")
    assert provider._config.project == "hermes-custom-profile"


def test_prefetch_propagates_search_errors(provider: AiMemoryProvider) -> None:
    provider._client.search = MagicMock(side_effect=RuntimeError("search failed"))
    with pytest.raises(RuntimeError, match="search failed"):
        provider.prefetch("test query")


def test_handle_tool_call_propagates_errors(provider: AiMemoryProvider) -> None:
    provider._client.search = MagicMock(side_effect=RuntimeError("search failed"))
    with pytest.raises(RuntimeError):
        provider.handle_tool_call("ai_memory_search", {"query": "test"})
