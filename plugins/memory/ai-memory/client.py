from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

import httpx

# Ensure sibling modules are findable when this file is loaded standalone
# (Hermes pre-loads submodules before executing __init__.py).
_PLUGIN_DIR = str(Path(__file__).resolve().parent)
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

from config import AiMemoryConfig  # noqa: E402

log = logging.getLogger("ai-memory")

# Timeouts are sized for a REMOTE HTTPS server, not for loopback.
# Measured against https://ai-memory.home.jayson.com.br on 2026-09-08:
# cold connect (TCP + TLS + request) 2.28s, warm request ~0.11s.
#
# Search must finish inside the Hermes external-prefetch budget
# (_EXTERNAL_PREFETCH_TIMEOUT_S = 8.0s in agent/memory_manager.py). A client
# timeout above that budget lets Hermes give up first and park the provider as
# "stuck" until the call returns, so keep it below 8.0s.
SEARCH_TIMEOUT = 6.0
# Hooks run on daemon threads and swallow failures, so a too-short timeout
# loses the turn in silence. 5.0s covers a cold TLS handshake.
HOOK_TIMEOUT = 5.0
WRITE_TIMEOUT = 10.0
READ_TIMEOUT = 6.0
# Handoff is fetched once, synchronously, on session start. 8.0s tolerates a
# cold connection while still bounding startup.
HANDOFF_TIMEOUT = 8.0

# Cross-project search. ai-memory mounts /api/v1 only with --enable-web; the
# MCP endpoint is always mounted and serves the fallback.
GLOBAL_SEARCH_PATH = "/api/v1/search"
MCP_PATH = "/mcp"


def _hit_list(value: Any, limit: int) -> list[dict[str, Any]]:
    """Keep the dict entries of a hit list, bounded by ``limit``."""
    if not isinstance(value, list):
        return []
    return [hit for hit in value if isinstance(hit, dict)][:limit]


def _normalize_global_hit(hit: dict[str, Any]) -> dict[str, Any]:
    """Rename MCP ``global_hits`` scope keys to the /api/v1/search names."""
    out = dict(hit)
    for src_key, dst_key in (("workspace_name", "workspace"), ("project_name", "project")):
        if src_key in out and dst_key not in out:
            out[dst_key] = out.pop(src_key)
    return out


class AiMemoryClient:
    def __init__(self, config: AiMemoryConfig) -> None:
        self.config = config
        self._base = config.server_url.rstrip("/")
        headers: dict[str, str] = {"Content-Type": "application/json"}
        token = config.auth_token or config.api_key
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._transport: Any = None
        self._client = httpx.Client(headers=headers)

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self._base}{path}"
        extra_headers = kwargs.pop("headers", {})
        # Merge case-insensitively. httpx's default headers contain lowercase
        # ``accept: */*``; adding a separate ``Accept`` key produced two values,
        # and ai-memory correctly rejected the MCP request with HTTP 406.
        headers = {str(key).lower(): str(value) for key, value in self._client.headers.items()}
        headers.update({str(key).lower(): str(value) for key, value in extra_headers.items()})
        timeout = kwargs.pop("timeout", SEARCH_TIMEOUT)
        if self._transport:
            with httpx.Client(transport=self._transport, timeout=timeout) as c:
                return c.request(method, url, headers=headers, **kwargs)
        return self._client.request(method, url, headers=headers, timeout=timeout, **kwargs)

    def search(
        self,
        query: str,
        workspace: str | None = None,
        project: str | None = None,
        limit: int = 3,
        global_search: bool = False,
    ) -> list[dict[str, Any]]:
        """Search the wiki in one of two explicit scopes. Scope is never inferred.

        ``global_search=True`` reads every workspace and project through
        ``GET /api/v1/search`` with no scope parameters. ai-memory 2.1.1 treats
        a request on that route without ``workspace``, ``project`` or ``scopes``
        as the cross-project search (``SearchMode::Global`` in
        ``ai-memory-web/src/routes/api.rs``). It runs the same ``pages_fts``
        query and authority reranking as MCP ``memory_query(global=true)``
        (``ReaderPool::search_pages`` versus ``search_pages_with_meta``), and
        every hit carries ``workspace`` and ``project``. A scope passed together
        with ``global_search`` is a caller bug and raises.

        Project search sends the complete workspace/project pair to MCP
        ``memory_query``. That path runs the hybrid ranker (FTS5, entity and
        graph streams, plus vector when the server has an embedder) and unions
        the ``_global`` preferences scope. The REST route is FTS5-only for a
        project, so project recall stays on MCP.

        On MCP an omitted scope resolves to the server's active project, never
        to global. This client never sends an MCP query without a scope.
        """
        if global_search:
            if workspace or project:
                raise ValueError("global search cannot be combined with workspace/project")
            return self._search_global(query, limit)
        if not (workspace and project):
            raise ValueError("project search requires workspace and project")
        data = self._memory_query(
            {"query": query, "limit": limit, "workspace": workspace, "project": project}
        )
        return _hit_list(data.get("hits"), limit)

    def _search_global(self, query: str, limit: int) -> list[dict[str, Any]]:
        params = {"q": query, "limit": limit}
        r = self._request("GET", GLOBAL_SEARCH_PATH, params=params, timeout=SEARCH_TIMEOUT)
        if r.status_code == 404:
            # /api/v1 is mounted only when ai-memory runs with --enable-web.
            # MCP memory_query(global=true) reads the same FTS5 index, so it is
            # an equivalent fallback; its hits live under ``global_hits``.
            log.info("GET %s answered 404; using MCP memory_query global=true", GLOBAL_SEARCH_PATH)
            data = self._memory_query({"query": query, "limit": limit, "global": True})
            return [_normalize_global_hit(hit) for hit in _hit_list(data.get("global_hits"), limit)]
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list):
            log.warning(
                "%s response has unexpected type: %s", GLOBAL_SEARCH_PATH, type(data).__name__
            )
            return []
        return _hit_list(data, limit)

    def _memory_query(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call the MCP ``memory_query`` tool and return its JSON result body."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "memory_query", "arguments": arguments},
        }
        r = self._request(
            "POST",
            MCP_PATH,
            json=payload,
            headers={"Accept": "application/json, text/event-stream"},
            timeout=SEARCH_TIMEOUT,
        )
        r.raise_for_status()
        envelope = r.json()
        if not isinstance(envelope, dict) or envelope.get("error"):
            raise RuntimeError(f"memory_query failed: {envelope!r}")
        result = envelope.get("result")
        if not isinstance(result, dict) or result.get("isError"):
            raise RuntimeError(f"memory_query failed: {result!r}")
        content = result.get("content")
        if not isinstance(content, list):
            log.warning("memory_query response has no content list")
            return {}
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            try:
                data = json.loads(item.get("text", ""))
            except (TypeError, ValueError):
                continue
            if isinstance(data, dict):
                return data
        log.warning("memory_query response contained no JSON object")
        return {}

    def write_page(
        self,
        path: str,
        body: str,
        tags: list[str] | None = None,
        tier: str | None = None,
        pinned: bool = False,
        workspace: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"path": path, "body": body}
        if tags:
            payload["tags"] = tags
        if tier:
            payload["tier"] = tier
        if pinned:
            payload["pinned"] = pinned
        if workspace:
            payload["workspace"] = workspace
        if project:
            payload["project"] = project
        r = self._request("POST", "/admin/write-page", json=payload, timeout=WRITE_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def read_page(
        self,
        path: str,
        workspace: str,
        project: str,
    ) -> str | None:
        """Return a page body, or ``None`` when the page does not exist.

        ai-memory 2.1.0 exposes ``GET /admin/read-page`` and requires all three
        of ``workspace``, ``project`` and ``path``; a missing page answers 404.
        Callers use this to read-modify-write a page instead of overwriting it.
        """
        if not (path and workspace and project):
            return None
        params = {"workspace": workspace, "project": project, "path": path}
        r = self._request("GET", "/admin/read-page", params=params, timeout=READ_TIMEOUT)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict):
            log.warning("read-page response has unexpected type: %s", type(data).__name__)
            return None
        body = data.get("body")
        return body if isinstance(body, str) else None

    def status(self) -> dict[str, Any]:
        r = self._request("GET", "/admin/status", timeout=SEARCH_TIMEOUT)
        r.raise_for_status()
        return r.json()

    def send_hook(
        self,
        event: str,
        session_id: str,
        payload: dict[str, Any] | None = None,
        workspace: str | None = None,
        project: str | None = None,
    ) -> None:
        params: dict[str, str] = {
            "event": event,
            "agent": "hermes",
        }
        if workspace:
            params["workspace"] = workspace
        if project:
            params["project"] = project
        if session_id:
            params["session_id"] = session_id
        body: dict[str, Any] = {}
        if payload:
            body = payload
        try:
            r = self._request("POST", "/hook", params=params, json=body, timeout=HOOK_TIMEOUT)
            # ai-memory answers 202 for anything it queues, including a
            # payload whose fields it does not recognise, so a bad shape
            # used to look identical to a good one. raise_for_status at
            # least surfaces transport- and route-level failures.
            r.raise_for_status()
        except Exception:
            log.warning("ai-memory hook failed for event=%s", event, exc_info=True)

    def fetch_handoff(
        self,
        agent: str = "hermes",
        cwd: str | None = None,
        workspace: str | None = None,
        project: str | None = None,
    ) -> str | None:
        params: dict[str, str] = {"agent": agent}
        if cwd:
            params["cwd"] = cwd
        if workspace:
            params["workspace"] = workspace
        if project:
            params["project"] = project
        r = self._request("GET", "/handoff", params=params, timeout=HANDOFF_TIMEOUT)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        # ai-memory 1.28.1 returns the handoff as a markdown block, not a
        # JSON envelope: r.json() raised here on every call.
        text = (r.text or "").strip()
        return text or None
