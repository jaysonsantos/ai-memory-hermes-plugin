from __future__ import annotations

import json
import logging
import sys
import threading
from pathlib import Path
from typing import Any

# Ensure sibling modules are findable when this file is loaded standalone
# (Hermes pre-loads submodules before executing __init__.py).
_PLUGIN_DIR = str(Path(__file__).resolve().parent)
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

from client import AiMemoryClient  # noqa: E402
from config import (  # noqa: E402
    SCOPE_GLOBAL,
    AiMemoryConfig,
    get_config_schema,
    load_config,
    normalize_recall_scope,
    save_config,
)

try:
    from agent.memory_provider import (  # type: ignore[import-untyped]
        MemoryProvider,
        RecallStatus,
    )
except ImportError:
    from abc import ABC as _ABC
    from dataclasses import dataclass as _dataclass

    class MemoryProvider(_ABC):  # type: ignore[no-redef]  # noqa: B024 - import shim
        pass

    @_dataclass(frozen=True)
    class RecallStatus:  # type: ignore[no-redef]
        """Stand-in for ``agent.memory_provider.RecallStatus`` when Hermes is
        not importable (tests, lint). Field order matches the real one."""

        provider_label: str
        count: int
        glyph: str = "\U0001f9e0"


log = logging.getLogger(__name__)

# Actions the Hermes built-in memory tool mirrors to external providers.
# Source of truth: MemoryManager._MIRRORED_MEMORY_ACTIONS
# (agent/memory_manager.py). Hermes never sends "write" or "append".
MIRRORED_ACTIONS = ("add", "replace", "remove")

# Entry separator for the mirrored wiki page. The built-in store joins entries
# with "\n\u00a7\n"; the mirror uses a markdown rule instead so the page still
# reads as a wiki page, and round-trips through _parse_mirror_entries.
_MIRROR_SEPARATOR = "\n\n---\n\n"
_MIRROR_NOTE = (
    "<!-- Mirrored from the Hermes built-in memory tool. Edits here are "
    "overwritten on the next mirrored write. -->"
)


def _mirror_page_path(target: str) -> str:
    """Wiki path for a mirrored memory target ("memory" | "user").

    ``target`` reaches us from the built-in tool's arguments, so it is
    sanitised: only the characters a wiki path segment may hold survive, and an
    empty result falls back to "memory". This keeps "../" and absolute paths out
    of the wiki path.
    """
    safe = "".join(c for c in target.strip().lower() if c.isalnum() or c in "-_")
    return f"hermes-memory/{safe or 'memory'}.md"


def _parse_mirror_entries(body: str) -> list[str]:
    """Entries held by a mirrored page ([] for an empty or unrecognised body)."""
    if not body:
        return []
    lines = [ln for ln in body.splitlines() if not ln.startswith(("# ", "<!--"))]
    stripped = "\n".join(lines)
    parts = [p.strip() for p in stripped.split("\n---\n")]
    return [p for p in parts if p]


def _render_mirror(target: str, entries: list[str]) -> str:
    """Render entries back into the mirrored page body."""
    header = f"# Hermes memory mirror ({target})\n\n{_MIRROR_NOTE}\n"
    if not entries:
        return header
    return header + "\n" + _MIRROR_SEPARATOR.join(entries) + "\n"


def _find_unique_entry(entries: list[str], old_text: str) -> int | None:
    """Index of the single entry containing ``old_text``; None if absent or
    ambiguous. Mirrors MemoryStore._find_unique_match: an ambiguous edit must
    not guess, because the wrong guess loses a different entry."""
    matches = [i for i, e in enumerate(entries) if old_text in e]
    return matches[0] if len(matches) == 1 else None


class AiMemoryProvider(MemoryProvider):
    def __init__(
        self,
        client: AiMemoryClient | None = None,
        config: AiMemoryConfig | None = None,
    ) -> None:
        self._config = config or AiMemoryConfig()
        self._client = client or AiMemoryClient(self._config)
        self._lock = threading.Lock()
        self.session_id: str = ""
        self._hermes_home: str = ""
        # Previous-session handoff, fetched once per session in initialize()
        # and surfaced through system_prompt_block(). None = none pending.
        self._handoff_context: str | None = None
        # Background recall queued by queue_prefetch(), consumed by prefetch()
        # on the next turn: {session_id: (query, context, hit_count)}.
        self._prefetch_cache: dict[str, tuple[str, str, int]] = {}
        # What the LAST prefetch() injected, for recall_status(). None = the
        # last prefetch injected nothing, so no indicator is shown.
        self._last_recall: RecallStatus | None = None

    @property
    def name(self) -> str:
        return "ai-memory"

    def is_available(self) -> bool:
        # ai-memory does not require authentication by default; the provider
        # is available whenever a server URL is configured.
        return bool(self._config.server_url)

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        self.session_id = session_id
        hermes_home = kwargs.get("hermes_home", "")
        self._hermes_home = hermes_home

        with self._lock:
            if hermes_home:
                self._config = load_config(hermes_home)
                self._client = AiMemoryClient(self._config)

            server_url = kwargs.get("ai_memory_server_url", "")
            if server_url:
                self._config.server_url = server_url

            auth_token = kwargs.get("ai_memory_auth_token", "")
            if auth_token:
                self._config.auth_token = auth_token

            # Workspace precedence: explicit override, then what Hermes
            # reports, then whatever ai-memory.json already held.
            # `agent_workspace` is a workspace NAME, not a filesystem path.
            workspace = kwargs.get("ai_memory_workspace", "") or kwargs.get("agent_workspace", "")
            if workspace:
                self._config.workspace = workspace

            # Project precedence: explicit override, then a project already
            # configured in ai-memory.json, then a name derived from the
            # Hermes profile identity.
            #
            # Hermes 0.20.5 passes neither `project` nor `profile` - it
            # passes `agent_identity`. The old code read the missing
            # `profile` kwarg, so the else branch ran on every session and
            # clobbered whatever ai-memory.json configured, pinning every
            # session to "hermes-default".
            project = kwargs.get("project", "")
            if project:
                self._config.project = project
            elif not self._config.project:
                identity = kwargs.get("agent_identity", "") or "default"
                self._config.project = f"hermes-{identity}"

            # An unknown recall_scope must narrow to the configured
            # workspace/project, never widen to every project on the server.
            self._config.recall_scope = normalize_recall_scope(self._config.recall_scope)

            self._client = AiMemoryClient(self._config)

        # One handoff fetch per session, never polled. HANDOFF_TIMEOUT bounds
        # the call so a stalled server cannot hold up startup, and any failure
        # leaves the provider working without a handoff.
        self._handoff_context = None
        self._last_recall = None
        with self._lock:
            self._prefetch_cache.clear()
        try:
            self._handoff_context = self._client.fetch_handoff(
                agent="hermes",
                workspace=self._config.workspace,
                project=self._config.project,
            )
        except Exception:
            log.warning("ai-memory handoff fetch failed", exc_info=True)

    def get_config_schema(self) -> list[dict[str, Any]]:
        return get_config_schema()

    def save_config(self, values: dict[str, Any], hermes_home: str) -> list[str]:
        return save_config(values, hermes_home)

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """OpenAI function-calling schemas.

        The key MUST be ``parameters``, not ``input_schema``. Hermes hands each
        schema straight to the active model adapter, and the openai-codex
        preflight (agent/codex_responses_adapter.py) raises
        "tools[N] is missing valid parameters" for the whole request when the
        key is absent — one bad tool aborts every model call in the session.
        """
        return [
            {
                "name": "ai_memory_search",
                "description": "Search the ai-memory wiki for relevant context",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum hits to return (default 5)",
                        },
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "ai_memory_write",
                "description": "Write a new page to the ai-memory wiki",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Wiki page path"},
                        "body": {"type": "string", "description": "Markdown body"},
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional tags for the page",
                        },
                    },
                    "required": ["path", "body"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "ai_memory_status",
                "description": "Check ai-memory server health",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            },
        ]

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs: Any) -> str:
        if tool_name == "ai_memory_search":
            return json.dumps(self._search(args))
        if tool_name == "ai_memory_write":
            return json.dumps(self._write(args))
        if tool_name == "ai_memory_status":
            return json.dumps(self._status())
        raise ValueError(f"Unknown tool: {tool_name}")

    _BASE_PROMPT = "Long-term memory is backed by ai-memory wiki."

    def system_prompt_block(self) -> str:
        if not self._handoff_context:
            return self._BASE_PROMPT
        return self._BASE_PROMPT + "\n\nPrevious session handoff:\n" + self._handoff_context

    _PREFETCH_LIMIT = 3

    def _recall(self, query: str) -> tuple[str, int]:
        """Run one recall. Returns (context, hit count); ("", 0) on any miss."""
        results = self._search({"query": query, "max_results": self._PREFETCH_LIMIT})
        hits = results.get("results") or [] if results.get("ok") else []
        snippets = [s for s in (r.get("snippet", "") for r in hits) if s]
        return ("\n\n".join(snippets), len(snippets)) if snippets else ("", 0)

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Recall context for the upcoming turn.

        Consumes the result queued by :meth:`queue_prefetch` when it answers
        the same query, so the common path costs no network round-trip inside
        the turn. Records what was injected for :meth:`recall_status`.
        """
        key = session_id or self.session_id
        cached: tuple[str, str, int] | None = None
        with self._lock:
            entry = self._prefetch_cache.pop(key, None)
        if entry and entry[0] == query:
            cached = entry

        context, count = (cached[1], cached[2]) if cached else self._recall(query)
        self._last_recall = (
            RecallStatus(provider_label="ai-memory", count=count) if context else None
        )
        return context

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """Queue a background recall; the next prefetch() consumes it.

        ``session_id`` is keyword-only and defaults to "" to match
        MemoryProvider.queue_prefetch. Hermes always passes it
        (agent/memory_manager.py MemoryManager.queue_prefetch_all), and the
        previous signature raised TypeError there, which Hermes swallowed as a
        non-fatal warning — background recall never ran.
        """
        key = session_id or self.session_id

        def _do() -> None:
            try:
                context, count = self._recall(query)
            except Exception:
                log.warning("ai-memory queue_prefetch failed", exc_info=True)
                return
            if not context:
                return
            with self._lock:
                self._prefetch_cache[key] = (query, context, count)

        threading.Thread(target=_do, daemon=True).start()

    def recall_status(self) -> RecallStatus | None:
        """What the most recent prefetch() injected; None when it injected
        nothing. Drives the Hermes recall indicator (describe_recall)."""
        return self._last_recall

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        **kwargs: Any,
    ) -> None:
        sid = session_id or self.session_id
        ws = self._config.workspace
        proj = self._config.project

        def _do() -> None:
            try:
                # ai-memory's own lifecycle event. The previous
                # event="user-prompt" with a {user, assistant} body was
                # accepted with 202 and then stored with an EMPTY body,
                # because ai-memory reads `prompt` off a
                # `user-prompt-submit` payload. Every Hermes turn was lost.
                self._client.send_hook(
                    event="user-prompt-submit",
                    session_id=sid,
                    payload={"session_id": sid, "prompt": user_content},
                    workspace=ws,
                    project=proj,
                )
            except Exception:
                log.warning("ai-memory sync_turn failed", exc_info=True)

        threading.Thread(target=_do, daemon=True).start()

    def on_session_end(self, messages: list[dict[str, Any]], **kwargs: Any) -> None:
        sid = self.session_id
        ws = self._config.workspace
        proj = self._config.project

        def _do() -> None:
            try:
                self._client.send_hook(
                    event="session-end",
                    session_id=sid,
                    payload={"session_id": sid, "messages": messages},
                    workspace=ws,
                    project=proj,
                )
            except Exception:
                log.warning("ai-memory on_session_end failed", exc_info=True)

        threading.Thread(target=_do, daemon=True).start()

    def on_memory_write(
        self, action: str, target: str, content: str, metadata: dict[str, Any] | None = None
    ) -> None:
        """Mirror a built-in memory-tool write onto the ai-memory wiki.

        Hermes sends ``add``, ``replace`` or ``remove`` (never "write" or
        "append") with ``target`` of "memory" or "user"; for replace and remove
        the entry to edit is identified by ``metadata["old_text"]``, and remove
        carries an empty ``content``. Semantics follow the built-in store
        (tools/memory_tool_store.py).

        The page is read, edited and written back. A blind write_page would
        replace the whole page with the single entry, losing every other one.
        """
        if action not in MIRRORED_ACTIONS:
            return

        old_text = str((metadata or {}).get("old_text") or "").strip()
        entry = (content or "").strip()
        if action in ("replace", "remove") and not old_text:
            log.warning("ai-memory mirror skipped: %s needs old_text", action)
            return
        if action in ("add", "replace") and not entry:
            log.warning("ai-memory mirror skipped: %s needs content", action)
            return

        path = _mirror_page_path(target)
        try:
            body = self._client.read_page(
                path=path,
                workspace=self._config.workspace,
                project=self._config.project,
            )
            entries = _parse_mirror_entries(body or "")
            updated = self._apply_mirror(action, entries, entry, old_text)
            if updated is None or updated == entries:
                return
            self._client.write_page(
                path=path,
                body=_render_mirror(target, updated),
                tags=["hermes", "mirror"],
                workspace=self._config.workspace,
                project=self._config.project,
            )
        except Exception:
            log.warning("on_memory_write hook failed", exc_info=True)

    @staticmethod
    def _apply_mirror(
        action: str, entries: list[str], entry: str, old_text: str
    ) -> list[str] | None:
        """Apply one mirrored action to the page entries.

        Returns the new entry list, or ``None`` when the edit cannot be applied
        safely (no match, or an ambiguous match). Returning None leaves the page
        untouched: a wrong guess would delete somebody else's entry.
        """
        if action == "add":
            return entries if entry in entries else entries + [entry]
        idx = _find_unique_entry(entries, old_text)
        if idx is None:
            log.warning("ai-memory mirror skipped: %s found no unique entry for old_text", action)
            return None
        if action == "remove":
            return entries[:idx] + entries[idx + 1 :]
        return entries[:idx] + [entry] + entries[idx + 1 :]

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        rewound: bool = False,
        **kwargs: Any,
    ) -> None:
        """Follow /new, /reset, /resume, /branch and context compression.

        Signature mirrors ``MemoryProvider.on_session_switch``. Hermes swaps
        session_id on these paths without rebuilding the provider, so without
        this every later observation kept the id of the session the provider
        was first initialized with.
        """
        with self._lock:
            self.session_id = new_session_id
            if reset:
                # A reset starts a clean context; the previous session's
                # handoff must not leak into it.
                self._handoff_context = None

    def shutdown(self) -> None:
        pass

    def _search(self, args: dict[str, Any]) -> dict[str, Any]:
        query = args.get("query", "")
        max_results = args.get("max_results", 5)
        if not isinstance(max_results, int) or isinstance(max_results, bool):
            max_results = 5
        # Scoped by default. Reads are restricted to the configured
        # workspace/project pair, so recall cannot pull another project's notes
        # into the Hermes turn. recall_scope="global" opts in to cross-agent
        # recall across every project on the server (GET /api/v1/search with
        # no scope; project recall goes through MCP memory_query).
        #
        # Scope is all-or-nothing: ai-memory resolves a project *within* a
        # workspace, so a half scope either fails to resolve or silently widens.
        scope_global = self._config.recall_scope == SCOPE_GLOBAL
        results = self._client.search(
            query=query,
            limit=max_results,
            workspace=None if scope_global else self._config.workspace,
            project=None if scope_global else self._config.project,
            global_search=scope_global,
        )
        return {"ok": True, "results": results}

    def _write(self, args: dict[str, Any]) -> dict[str, Any]:
        result = self._client.write_page(
            path=args.get("path", ""),
            body=args.get("body", ""),
            tags=args.get("tags"),
            workspace=self._config.workspace,
            project=self._config.project,
        )
        if not (result.get("ok", False) or result.get("page_id")):
            return result
        return {"ok": True, "written": args.get("path")}

    def _status(self) -> dict[str, Any]:
        return self._client.status()
