"""Contract tests against the INSTALLED Hermes Agent, not against ourselves.

The plugin's own suite passed 123 tests while the provider was incompatible
with Hermes 0.21.1: it asserted the plugin's shape rather than the host's.
These tests import the real ``agent.memory_provider``,
``agent.memory_manager`` and ``agent.codex_responses_adapter`` and drive the
provider through the same call sites Hermes uses, so a host-contract break
cannot stay green.

They skip when Hermes is not installed, so the suite still runs elsewhere.
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


def _hermes_root() -> Path | None:
    """Installed Hermes source tree, or None when Hermes is absent.

    conftest.py already APPENDED this to sys.path — appended, never inserted,
    because Hermes ships a top-level cli.py and client.py that would shadow the
    plugin's own modules. Here we only decide whether to run.
    """
    home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    for candidate in (home / "hermes-agent", home.parent / "hermes-agent"):
        if (candidate / "agent" / "memory_provider.py").is_file():
            return candidate
    return None


pytestmark = pytest.mark.skipif(
    _hermes_root() is None,
    reason="Hermes Agent is not installed; host-contract tests need it",
)


@pytest.fixture(scope="module")
def hermes_memory_provider() -> Any:
    return pytest.importorskip("agent.memory_provider")


@pytest.fixture(scope="module")
def hermes_memory_manager() -> Any:
    return pytest.importorskip("agent.memory_manager")


# --------------------------------------------------------------------------
# ABC conformance
# --------------------------------------------------------------------------


def test_provider_is_a_real_hermes_memory_provider(hermes_memory_provider: Any) -> None:
    from provider import AiMemoryProvider

    assert issubclass(AiMemoryProvider, hermes_memory_provider.MemoryProvider)


def test_provider_instantiates(hermes_memory_provider: Any) -> None:
    """An abstract method left unimplemented fails here, as it does in the
    Hermes loader (_instantiate_subclass swallows the TypeError)."""
    from provider import AiMemoryProvider

    assert isinstance(AiMemoryProvider(), hermes_memory_provider.MemoryProvider)


# Methods Hermes calls by keyword. A signature drift here is exactly the class
# of bug that disabled queue_prefetch: Hermes caught the TypeError and logged a
# non-fatal warning, so the feature was silently off.
_KEYWORD_CALLED = (
    "prefetch",
    "queue_prefetch",
    "sync_turn",
    "on_session_switch",
    "on_memory_write",
    "recall_status",
    "get_tool_schemas",
    "is_available",
    "initialize",
)


@pytest.mark.parametrize("method_name", _KEYWORD_CALLED)
def test_overrides_accept_every_abc_keyword(hermes_memory_provider: Any, method_name: str) -> None:
    from provider import AiMemoryProvider

    base = getattr(hermes_memory_provider.MemoryProvider, method_name)
    override = getattr(AiMemoryProvider, method_name)
    if override is base:
        return  # not overridden; the ABC default applies

    params = inspect.signature(override).parameters
    accepts_kwargs = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
    for name, param in inspect.signature(base).parameters.items():
        if param.kind is inspect.Parameter.KEYWORD_ONLY and not accepts_kwargs:
            assert name in params, (
                f"{method_name} does not accept keyword-only '{name}'; "
                "Hermes passes it and the TypeError is swallowed"
            )


def test_queue_prefetch_accepts_session_id_keyword() -> None:
    """MemoryManager.queue_prefetch_all calls
    p.queue_prefetch(query, session_id=session_id)."""
    from provider import AiMemoryProvider

    provider = AiMemoryProvider()
    provider._client = MagicMock()
    provider._client.search.return_value = []
    provider.queue_prefetch("a query", session_id="sess-1")  # must not raise


def test_recall_status_returns_the_hermes_dataclass(
    hermes_memory_provider: Any,
) -> None:
    from provider import AiMemoryProvider

    provider = AiMemoryProvider()
    provider._client = MagicMock()
    provider._client.search.return_value = [{"snippet": "one"}]
    provider.prefetch("q")

    status = provider.recall_status()
    assert isinstance(status, hermes_memory_provider.RecallStatus)
    assert status.count == 1


# --------------------------------------------------------------------------
# Tool schemas: the gate that breaks every model request
# --------------------------------------------------------------------------


def test_schemas_survive_hermes_normalization(hermes_memory_manager: Any) -> None:
    from provider import AiMemoryProvider

    for schema in AiMemoryProvider().get_tool_schemas():
        normalized = hermes_memory_manager.normalize_tool_schema(schema)
        assert normalized is not None
        assert isinstance(normalized.get("parameters"), dict), normalized.get("name")


def test_schemas_pass_the_codex_preflight() -> None:
    """The active model provider is openai-codex and its preflight is strict.

    With `input_schema` this raised
    "Codex Responses tools[0] is missing valid parameters", and one bad tool
    aborts the whole request — every model call in the session failed.
    """
    adapter = pytest.importorskip("agent.codex_responses_adapter")
    from provider import AiMemoryProvider

    for idx, schema in enumerate(AiMemoryProvider().get_tool_schemas()):
        tool = {"type": "function", **schema}
        checked = adapter._preflight_tool(tool, idx)
        assert checked["name"] == schema["name"]
        assert isinstance(checked["parameters"], dict)


def test_schemas_survive_the_anthropic_conversion() -> None:
    """Anthropic fails soft: it reads `fn.get("parameters") or {}`, so a bad
    schema reaches the model with no properties and the model cannot pass a
    query."""
    convert = pytest.importorskip("agent.anthropic_message_convert")
    from provider import AiMemoryProvider

    search = next(
        s for s in AiMemoryProvider().get_tool_schemas() if s["name"] == "ai_memory_search"
    )
    assert search["parameters"]["properties"], "query would be unreachable"
    assert convert is not None


# --------------------------------------------------------------------------
# Memory-write mirroring, driven through the real MemoryManager bridge
# --------------------------------------------------------------------------


def _manager_with(provider: Any, hermes_memory_manager: Any) -> Any:
    manager = hermes_memory_manager.MemoryManager()
    manager.add_provider(provider)
    return manager


@pytest.fixture
def mirror_provider() -> Any:
    from provider import AiMemoryProvider

    provider = AiMemoryProvider()
    provider._client = MagicMock()
    provider._client.read_page.return_value = "kept entry\n---\nstale entry"
    return provider


def test_manager_mirrors_add(mirror_provider: Any, hermes_memory_manager: Any) -> None:
    manager = _manager_with(mirror_provider, hermes_memory_manager)
    manager.on_memory_write("add", "memory", "brand new fact", metadata={})

    body = mirror_provider._client.write_page.call_args.kwargs["body"]
    assert "brand new fact" in body
    assert "kept entry" in body


def test_manager_mirrors_replace(mirror_provider: Any, hermes_memory_manager: Any) -> None:
    manager = _manager_with(mirror_provider, hermes_memory_manager)
    manager.on_memory_write("replace", "memory", "fresh entry", metadata={"old_text": "stale"})

    body = mirror_provider._client.write_page.call_args.kwargs["body"]
    assert "fresh entry" in body
    assert "stale entry" not in body
    assert "kept entry" in body


def test_manager_mirrors_remove(mirror_provider: Any, hermes_memory_manager: Any) -> None:
    manager = _manager_with(mirror_provider, hermes_memory_manager)
    manager.on_memory_write("remove", "memory", "", metadata={"old_text": "stale"})

    body = mirror_provider._client.write_page.call_args.kwargs["body"]
    assert "stale entry" not in body
    assert "kept entry" in body


def test_notify_memory_tool_write_reaches_the_provider(
    mirror_provider: Any, hermes_memory_manager: Any
) -> None:
    """The real entry point: the agent loop hands the built-in tool's result
    and args to the manager, which decides what to mirror."""
    manager = _manager_with(mirror_provider, hermes_memory_manager)
    manager.notify_memory_tool_write(
        json.dumps({"success": True}),
        {"action": "add", "target": "memory", "content": "recorded fact"},
    )

    body = mirror_provider._client.write_page.call_args.kwargs["body"]
    assert "recorded fact" in body


def test_mirrored_actions_match_the_host(hermes_memory_manager: Any) -> None:
    """The plugin accepted ("write", "append"); Hermes never sends those."""
    import provider as provider_mod

    assert set(provider_mod.MIRRORED_ACTIONS) == set(
        hermes_memory_manager.MemoryManager._MIRRORED_MEMORY_ACTIONS
    )


# --------------------------------------------------------------------------
# Recall: prefetch, indicator, and default scoping
# --------------------------------------------------------------------------


def test_manager_prefetch_and_recall_indicator(hermes_memory_manager: Any) -> None:
    from provider import AiMemoryProvider

    provider = AiMemoryProvider()
    provider._client = MagicMock()
    provider._client.search.return_value = [{"snippet": "recalled text"}]
    manager = _manager_with(provider, hermes_memory_manager)

    assert "recalled text" in manager.prefetch_all("what did we decide?")
    assert "ai-memory" in manager.describe_recall()


def test_manager_queue_prefetch_all_does_not_break(
    hermes_memory_manager: Any,
) -> None:
    from provider import AiMemoryProvider

    provider = AiMemoryProvider()
    provider._client = MagicMock()
    provider._client.search.return_value = [{"snippet": "queued text"}]
    manager = _manager_with(provider, hermes_memory_manager)

    try:
        manager.queue_prefetch_all("a real question", session_id="sess-9")
    except AttributeError:
        # Hermes' DaemonThreadPoolExecutor subclasses a CPython internal that
        # moved after 3.13; this repo's venv is newer than the interpreter
        # Hermes ships with. Drive the identical provider call site instead —
        # the contract under test is the keyword call, not Hermes' executor.
        manager._each_provider(
            "queue_prefetch failed (non-fatal)",
            lambda p: p.queue_prefetch("a real question", session_id="sess-9"),
        )

    deadline = time.time() + 5
    while time.time() < deadline and not provider._prefetch_cache:
        time.sleep(0.05)
    # _each_provider swallows the TypeError the old signature raised, so an
    # empty cache is exactly how this defect looked in production.
    assert provider._prefetch_cache, "background recall never ran"


def test_recall_is_scoped_by_default() -> None:
    """Recall must not read every project on the server unless asked to."""
    from provider import AiMemoryProvider

    provider = AiMemoryProvider()
    provider._config.workspace = "hermes"
    provider._config.project = "orchestrator"
    provider._client = MagicMock()
    provider._client.search.return_value = []

    provider.handle_tool_call("ai_memory_search", {"query": "q"})
    kwargs = provider._client.search.call_args.kwargs
    assert kwargs["workspace"] == "hermes"
    assert kwargs["project"] == "orchestrator"


def test_global_recall_requires_explicit_config() -> None:
    from config import SCOPE_GLOBAL
    from provider import AiMemoryProvider

    provider = AiMemoryProvider()
    provider._config.recall_scope = SCOPE_GLOBAL
    provider._client = MagicMock()
    provider._client.search.return_value = []

    provider.handle_tool_call("ai_memory_search", {"query": "q"})
    kwargs = provider._client.search.call_args.kwargs
    assert kwargs["workspace"] is None and kwargs["project"] is None


def test_unknown_recall_scope_narrows() -> None:
    from config import SCOPE_PROJECT, normalize_recall_scope

    for value in ("", "everything", "GLOBAL ", None):
        expected = "global" if str(value).strip().lower() == "global" else SCOPE_PROJECT
        assert normalize_recall_scope(value) == expected


# --------------------------------------------------------------------------
# Plugin CLI registration, as Hermes performs it
# --------------------------------------------------------------------------


def test_register_cli_accepts_the_parser_hermes_passes() -> None:
    """_attach_plugin_cli_command calls setup_fn(plugin_parser) with an
    ArgumentParser. Calling add_parser() on it raised AttributeError, which
    aborted the whole plugin CLI loop and cost other plugins their commands."""
    from cli import register_cli

    parser = argparse.ArgumentParser(prog="ai-memory")
    register_cli(parser)

    assert parser.parse_args(["status"]).ai_memory_command == "status"


def test_module_exposes_the_handler_name_hermes_looks_up() -> None:
    """plugins/memory/__init__.py reads getattr(cli_mod, f"{provider}_command"),
    and the provider is named "ai-memory"."""
    import cli

    handler = getattr(cli, "ai-memory_command", None)
    assert callable(handler)
    assert handler is cli.ai_memory_command


def test_hermes_attaches_the_command_tree() -> None:
    """Drive the real Hermes helper end to end."""
    main = pytest.importorskip("hermes_cli.main")
    import cli

    root = argparse.ArgumentParser(prog="hermes")
    subparsers = root.add_subparsers(dest="command")
    main._attach_plugin_cli_command(
        subparsers,
        {
            "name": "ai-memory",
            "help": "ai-memory plugin",
            "description": "",
            "setup_fn": cli.register_cli,
            "handler_fn": getattr(cli, "ai-memory_command", None),
        },
    )

    args = root.parse_args(["ai-memory", "config"])
    assert args.ai_memory_command == "config"
    assert args.func is cli.ai_memory_command


def test_cli_resolves_hermes_home_without_the_attribute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hermes dispatches args.func(args) on a namespace that has no
    hermes_home, so every subcommand raised AttributeError."""
    import cli

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    resolved = cli._hermes_home(argparse.Namespace())
    assert resolved  # a path, not an exception


def test_cli_has_no_remote_code_download() -> None:
    """The self-update fetched branch HEAD and Hermes then imported it."""
    import cli

    for attr in ("urllib", "zipfile", "tempfile", "REPO_TARBALL_URL"):
        assert not hasattr(cli, attr), attr

    source = Path(cli.__file__).read_text()
    for call in ("urlretrieve(", "extractall(", "ZipFile("):
        assert call not in source, call
