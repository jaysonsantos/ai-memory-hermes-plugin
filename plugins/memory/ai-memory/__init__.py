from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_init_dir = str(Path(__file__).resolve().parent)
if _init_dir not in sys.path:
    sys.path.insert(0, _init_dir)

# Hermes loads __init__.py as a standalone module (not a package), so
# relative imports (from .foo import Bar) fail. Bare imports + sys.path
# ensure sibling modules are found regardless of how the plugin is loaded.
from config import AiMemoryConfig, normalize_recall_scope  # noqa: E402
from provider import AiMemoryProvider  # noqa: E402

__all__ = ["register"]


def register(ctx: object) -> None:
    hermes_home = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
    config = AiMemoryConfig()

    config_path = os.path.join(hermes_home, "ai-memory.json")
    if os.path.isfile(config_path):
        try:
            with open(config_path) as f:
                overrides = json.load(f)
            for key in (
                "server_url",
                "api_key",
                "auth_token",
                "workspace",
                "project",
                "recall_scope",
            ):
                if key in overrides:
                    setattr(config, key, overrides[key])
        except (json.JSONDecodeError, OSError):
            pass

    for env_key, attr in (
        ("AI_MEMORY_SERVER_URL", "server_url"),
        ("AI_MEMORY_API_KEY", "api_key"),
        ("AI_MEMORY_AUTH_TOKEN", "auth_token"),
        ("AI_MEMORY_WORKSPACE", "workspace"),
        ("AI_MEMORY_PROJECT", "project"),
        ("AI_MEMORY_RECALL_SCOPE", "recall_scope"),
    ):
        val = os.environ.get(env_key)
        if val:
            setattr(config, attr, val)

    # Recall scope decides whether every project on the server is readable, so
    # normalise it here too: an unknown value must narrow, never widen.
    config.recall_scope = normalize_recall_scope(config.recall_scope)

    provider = AiMemoryProvider(config=config)
    ctx.register_memory_provider(provider)  # type: ignore[union-attr]
