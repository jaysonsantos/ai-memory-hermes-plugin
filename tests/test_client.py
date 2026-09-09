from __future__ import annotations

import json
from unittest.mock import ANY, MagicMock

import httpx
import pytest
from client import HOOK_TIMEOUT, SEARCH_TIMEOUT, AiMemoryClient
from config import AiMemoryConfig


@pytest.fixture
def client() -> AiMemoryClient:
    return AiMemoryClient(
        AiMemoryConfig(
            server_url="http://localhost:49374",
            auth_token="test-token",
        )
    )


def _mcp_response(hits: object) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": json.dumps({"hits": hits})}]},
        },
    )


def test_client_search_success(client: AiMemoryClient) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/mcp"
        assert request.headers["Authorization"] == "Bearer test-token"
        assert request.headers["Accept"] == "application/json, text/event-stream"
        body = json.loads(request.content)
        assert body["method"] == "tools/call"
        assert body["params"] == {
            "name": "memory_query",
            "arguments": {
                "query": "test query",
                "limit": 3,
                "workspace": "hermes",
                "project": "hermes-test",
            },
        }
        return _mcp_response([{"path": "test.md"}])

    client._transport = httpx.MockTransport(handler)
    results = client.search("test query", workspace="hermes", project="hermes-test", limit=3)
    assert len(results) == 1
    assert results[0]["path"] == "test.md"


def test_client_search_raises_on_http_error(client: AiMemoryClient) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client._transport = httpx.MockTransport(handler)
    with pytest.raises(httpx.HTTPStatusError):
        client.search("test query", global_search=True)


def test_client_write_page_success(client: AiMemoryClient) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body["path"] == "notes/test.md"
        assert body["body"] == "# Hello"
        return httpx.Response(200, json={"ok": True, "path": "notes/test.md"})

    client._transport = httpx.MockTransport(handler)
    result = client.write_page("notes/test.md", "# Hello")
    assert result["ok"] is True


def test_client_write_page_with_tags(client: AiMemoryClient) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["tags"] == ["test", "docs"]
        return httpx.Response(200, json={"ok": True})

    client._transport = httpx.MockTransport(handler)
    result = client.write_page("notes/test.md", "# Hello", tags=["test", "docs"])
    assert result["ok"] is True


def test_client_status_success(client: AiMemoryClient) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "pages": 10})

    client._transport = httpx.MockTransport(handler)
    result = client.status()
    assert result["ok"] is True
    assert result["pages"] == 10


def test_client_status_raises_on_connection_error(client: AiMemoryClient) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client._transport = httpx.MockTransport(handler)
    with pytest.raises(httpx.ConnectError):
        client.status()


def test_client_send_hook(client: AiMemoryClient) -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"ok": True})

    client._transport = httpx.MockTransport(handler)
    client.send_hook(
        event="user-prompt",
        session_id="test-session",
        payload={"user": "hello", "assistant": "hi"},
        workspace="hermes",
        project="hermes-test",
    )
    assert len(sent) == 1
    assert "event=user-prompt" in str(sent[0].url)


def test_client_send_hook_does_not_raise(client: AiMemoryClient) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client._transport = httpx.MockTransport(handler)
    client.send_hook("user-prompt", "test-session")


def test_client_fetch_handoff_returns_summary(client: AiMemoryClient) -> None:
    # ai-memory 1.28.1 serves the handoff as a markdown block.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="  Continue working on X  ")

    client._transport = httpx.MockTransport(handler)
    result = client.fetch_handoff()
    assert result == "Continue working on X"


def test_client_fetch_handoff_returns_none_on_404(client: AiMemoryClient) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client._transport = httpx.MockTransport(handler)
    result = client.fetch_handoff()
    assert result is None


def test_client_fetch_handoff_raises_on_500(client: AiMemoryClient) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client._transport = httpx.MockTransport(handler)
    with pytest.raises(httpx.HTTPStatusError):
        client.fetch_handoff()


def test_client_no_auth_header_when_no_token() -> None:
    client = AiMemoryClient(AiMemoryConfig(server_url="http://localhost:49374"))
    assert "authorization" not in client._client.headers


def test_client_search_passes_workspace_project(client: AiMemoryClient) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        args = json.loads(request.content)["params"]["arguments"]
        assert args["workspace"] == "custom-ws"
        assert args["project"] == "custom-proj"
        return _mcp_response([])

    client._transport = httpx.MockTransport(handler)
    client.search("q", workspace="custom-ws", project="custom-proj")


def _ok_response(json_data: object) -> httpx.Response:
    return httpx.Response(
        200,
        json=json_data,
        request=httpx.Request("GET", "http://test"),
    )


def test_client_search_uses_search_timeout(client: AiMemoryClient) -> None:
    client._request = MagicMock()
    client._request.return_value = _ok_response([])
    client.search("test query", global_search=True)
    request_kwargs = client._request.call_args.kwargs
    assert client._request.call_args.args == ("GET", "/api/v1/search")
    assert request_kwargs["timeout"] == SEARCH_TIMEOUT
    assert request_kwargs["params"] == {"q": "test query", "limit": 3}


def test_client_send_hook_uses_hook_timeout(client: AiMemoryClient) -> None:
    client._request = MagicMock()
    client._request.return_value = _ok_response({"ok": True})
    client.send_hook("user-prompt", "test-session")
    client._request.assert_called_once_with(
        "POST", "/hook", params=ANY, json=ANY, timeout=HOOK_TIMEOUT
    )


def test_client_search_handles_non_list_response(client: AiMemoryClient) -> None:
    client._transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"error": "not a hit list"})
    )
    results = client.search("test query", global_search=True)
    assert results == []


def test_client_search_normalizes_api_hits_and_applies_limit(client: AiMemoryClient) -> None:
    client._transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, json=[{"path": "first.md"}, "invalid", {"path": "second.md"}]
        )
    )
    assert client.search("test query", limit=1, global_search=True) == [{"path": "first.md"}]


# ------------------------------------------------------------ global search
# ai-memory 2.1.1: GET /api/v1/search with no workspace/project/scopes is the
# cross-project FTS5 search (SearchMode::Global). It runs the same pages_fts
# query and authority reranking as MCP memory_query(global=true) and each hit
# carries its workspace and project. Verified equal on the deployed server.


def test_client_search_global_sends_no_scope_params(client: AiMemoryClient) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/v1/search"
        assert request.headers["Authorization"] == "Bearer test-token"
        assert dict(request.url.params) == {"q": "shared fact", "limit": "2"}
        return httpx.Response(
            200,
            json=[
                {
                    "workspace": "personal",
                    "project": "dotfiles",
                    "path": "decisions/0002.md",
                    "title": "Skill hub",
                    "kind": "decision",
                    "snippet": "<mark>shared</mark> fact",
                    "rank": -6.5,
                },
                {"workspace": "hermes", "project": "orchestrator", "path": "b.md"},
            ],
        )

    client._transport = httpx.MockTransport(handler)
    hits = client.search("shared fact", limit=2, global_search=True)
    assert [(h["workspace"], h["project"], h["path"]) for h in hits] == [
        ("personal", "dotfiles", "decisions/0002.md"),
        ("hermes", "orchestrator", "b.md"),
    ]


@pytest.mark.parametrize("scope", [{"workspace": "ws"}, {"project": "p"}])
def test_client_search_global_rejects_a_scope(
    client: AiMemoryClient, scope: dict[str, str]
) -> None:
    """A scope next to global_search is a caller bug. Refuse locally: the REST
    route would either 400 or silently narrow to one project."""

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not reach the server")

    client._transport = httpx.MockTransport(handler)
    with pytest.raises(ValueError, match="cannot be combined"):
        client.search("q", global_search=True, **scope)


def test_client_search_global_falls_back_to_mcp_on_404(client: AiMemoryClient) -> None:
    """/api/v1 exists only when ai-memory runs with --enable-web. Without it the
    client uses MCP memory_query(global=true), whose hits sit under
    ``global_hits`` (``hits`` is always present and empty there)."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/api/v1/search":
            return httpx.Response(404)
        assert request.url.path == "/mcp"
        body = json.loads(request.content)
        assert body["params"]["arguments"] == {"query": "q", "limit": 3, "global": True}
        text = json.dumps(
            {
                "hits": [],
                "global_hits": [
                    {
                        "workspace_name": "personal",
                        "project_name": "dotfiles",
                        "path": "a.md",
                        "title": "A",
                        "snippet": "s",
                        "rank": -1.0,
                    }
                ],
            }
        )
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"content": [{"type": "text", "text": text}]},
            },
        )

    client._transport = httpx.MockTransport(handler)
    hits = client.search("q", global_search=True)
    assert [r.url.path for r in seen] == ["/api/v1/search", "/mcp"]
    assert hits == [
        {
            "workspace": "personal",
            "project": "dotfiles",
            "path": "a.md",
            "title": "A",
            "snippet": "s",
            "rank": -1.0,
        }
    ]


def test_client_search_global_does_not_fall_back_on_other_errors(
    client: AiMemoryClient,
) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(401)

    client._transport = httpx.MockTransport(handler)
    with pytest.raises(httpx.HTTPStatusError):
        client.search("q", global_search=True)
    assert seen == ["/api/v1/search"]


def test_client_write_page_with_tier_and_pinned(client: AiMemoryClient) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["tier"] == "semantic"
        assert body["pinned"] is True
        return httpx.Response(200, json={"ok": True})

    client._transport = httpx.MockTransport(handler)
    result = client.write_page("notes/test.md", "# Hello", tier="semantic", pinned=True)
    assert result["ok"] is True


# ---------------------------------------------------------------- read-page
# ai-memory 2.1.0: GET /admin/read-page?workspace=&project=&path=
# The mirror needs it to read-modify-write a page instead of overwriting it.


def test_client_read_page_returns_body(client: AiMemoryClient) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/admin/read-page"
        assert dict(request.url.params) == {
            "workspace": "hermes",
            "project": "orchestrator",
            "path": "notes/a.md",
        }
        return httpx.Response(200, json={"path": "notes/a.md", "body": "# Hello"})

    client._transport = httpx.MockTransport(handler)
    assert client.read_page("notes/a.md", "hermes", "orchestrator") == "# Hello"


def test_client_read_page_returns_none_for_missing_page(client: AiMemoryClient) -> None:
    """The server answers 404 for a page that does not exist yet."""
    client._transport = httpx.MockTransport(
        lambda request: httpx.Response(404, json={"error": "No such file or directory"})
    )
    assert client.read_page("notes/missing.md", "hermes", "orchestrator") is None


def test_client_read_page_requires_a_full_scope(client: AiMemoryClient) -> None:
    """A half scope has no meaning: ai-memory resolves a project within a
    workspace. Refuse locally rather than send an ambiguous request."""

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not reach the server")

    client._transport = httpx.MockTransport(handler)
    assert client.read_page("notes/a.md", "", "orchestrator") is None
    assert client.read_page("notes/a.md", "hermes", "") is None
    assert client.read_page("", "hermes", "orchestrator") is None


def test_client_read_page_tolerates_an_unexpected_shape(client: AiMemoryClient) -> None:
    client._transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=["not", "a", "page"])
    )
    assert client.read_page("notes/a.md", "hermes", "orchestrator") is None


def test_client_read_page_tolerates_a_missing_body(client: AiMemoryClient) -> None:
    client._transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"path": "notes/a.md", "body": None})
    )
    assert client.read_page("notes/a.md", "hermes", "orchestrator") is None


def test_client_timeouts_suit_a_remote_server() -> None:
    """The defaults were tuned for loopback. Against the configured HTTPS
    server a cold connect measured 2.28s, so a 0.5s hook timeout lost the turn
    in silence. Search must still finish inside Hermes' 8.0s prefetch budget."""
    import client as client_mod

    assert client_mod.HOOK_TIMEOUT >= 2.0
    assert client_mod.HANDOFF_TIMEOUT >= 3.0
    assert client_mod.SEARCH_TIMEOUT < 8.0
