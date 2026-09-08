from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

# Plugin is loaded as a standalone module (not a package) by Hermes,
# so tests import sibling modules via bare names + sys.path to match
# the production loading model exactly. It stays at position 0: the Hermes
# tree below also ships top-level `cli.py` and `client.py`, and the plugin's
# own modules must win.
sys.path.insert(
    0,
    str(Path(__file__).parent.parent / "plugins/memory/ai-memory"),
)


def _hermes_root() -> Path | None:
    """Installed Hermes source tree, or None when Hermes is absent."""
    import os

    home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    for candidate in (home / "hermes-agent", home.parent / "hermes-agent"):
        if (candidate / "agent" / "memory_provider.py").is_file():
            return candidate
    return None


# APPENDED, never inserted: the provider imports the real
# agent.memory_provider ABC when Hermes is installed, so the contract tests
# check the plugin against the host instead of against its own fallback.
HERMES_ROOT = _hermes_root()
if HERMES_ROOT is not None and str(HERMES_ROOT) not in sys.path:
    sys.path.append(str(HERMES_ROOT))

from client import AiMemoryClient  # noqa: E402
from config import AiMemoryConfig  # noqa: E402
from provider import AiMemoryProvider  # noqa: E402


@pytest.fixture
def mock_home(tmp_path: Path) -> Path:
    return tmp_path / ".hermes"


@pytest.fixture
def config() -> AiMemoryConfig:
    return AiMemoryConfig(
        server_url="http://localhost:49374",
        auth_token="test-token",
        workspace="hermes",
        project="hermes-test",
    )


@pytest.fixture
def mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/admin/search" in request.url.path:
            return httpx.Response(
                200,
                json=[
                    {"path": "notes/test.md", "snippet": "test content", "score": 0.95},
                ],
            )
        if "/admin/write-page" in request.url.path:
            return httpx.Response(200, json={"ok": True, "path": "notes/test.md"})
        if "/admin/status" in request.url.path:
            return httpx.Response(
                200,
                json={"ok": True, "pages": 10, "sessions": 5},
            )
        if "/hook" in request.url.path:
            return httpx.Response(200, json={"ok": True})
        if "/handoff" in request.url.path:
            # ai-memory 1.28.1 returns markdown, not a JSON envelope.
            return httpx.Response(200, text="test handoff")
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.fixture
def mock_client(config: AiMemoryConfig, mock_transport: httpx.MockTransport) -> AiMemoryClient:
    client = AiMemoryClient(config)
    client._transport = mock_transport
    return client


@pytest.fixture
def provider(config: AiMemoryConfig) -> AiMemoryProvider:
    return AiMemoryProvider(config=config)


@pytest.fixture
def saved_config(mock_home: Path) -> dict[str, Any]:
    data: dict[str, Any] = {
        "server_url": "http://localhost:49374",
        "auth_token": "saved-token",
        "workspace": "hermes",
        "project": "hermes-custom",
    }
    p = mock_home / "ai-memory.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data))
    return data
