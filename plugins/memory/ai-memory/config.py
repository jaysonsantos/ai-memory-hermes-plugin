from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Ensure sibling modules are findable when this file is loaded standalone
# (Hermes pre-loads submodules before executing __init__.py).
_PLUGIN_DIR = str(Path(__file__).resolve().parent)
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

DEFAULT_SERVER_URL = "http://127.0.0.1:49374"

# Recall scope. "project" restricts every search to the configured
# workspace/project pair; "global" opts in to reading every project on the
# server. Global recall injects other projects' notes into the Hermes turn, so
# it stays opt-in.
SCOPE_PROJECT = "project"
SCOPE_GLOBAL = "global"
DEFAULT_RECALL_SCOPE = SCOPE_PROJECT


@dataclass
class AiMemoryConfig:
    server_url: str = DEFAULT_SERVER_URL
    api_key: str = ""
    auth_token: str = ""
    workspace: str = "hermes"
    project: str = "hermes-default"
    recall_scope: str = DEFAULT_RECALL_SCOPE


def get_config_schema() -> list[dict[str, Any]]:
    return [
        {
            "key": "server_url",
            "description": "ai-memory server URL",
            "default": DEFAULT_SERVER_URL,
            "required": False,
        },
        {
            "key": "api_key",
            "description": "ai-memory API key (optional for local mode)",
            "secret": True,
            "env_only": True,
            "required": False,
            "env_var": "AI_MEMORY_API_KEY",
        },
        {
            "key": "auth_token",
            "description": "ai-memory auth token (optional for local mode)",
            "secret": True,
            "env_only": True,
            "required": False,
            "env_var": "AI_MEMORY_AUTH_TOKEN",
        },
        {
            "key": "workspace",
            "description": "ai-memory workspace name",
            "default": "hermes",
            "required": False,
        },
        {
            "key": "project",
            "description": "ai-memory project name",
            "default": "hermes-default",
            "required": False,
        },
        {
            "key": "recall_scope",
            "description": (
                "Recall scope: 'project' searches only the configured "
                "workspace/project; 'global' reads every project on the server"
            ),
            "default": DEFAULT_RECALL_SCOPE,
            "choices": [SCOPE_PROJECT, SCOPE_GLOBAL],
            "required": False,
        },
    ]


def normalize_recall_scope(value: Any) -> str:
    """Return a known recall scope. Anything unrecognised falls back to the
    scoped default, so a typo cannot silently widen recall to every project."""
    scope = str(value or "").strip().lower()
    if scope == SCOPE_GLOBAL:
        return SCOPE_GLOBAL
    if scope and scope != SCOPE_PROJECT:
        log.warning("unknown recall_scope %r; falling back to %r", value, SCOPE_PROJECT)
    return SCOPE_PROJECT


def _secret_keys() -> dict[str, str]:
    """Return {config_key: env_var_name} for all secret/env-only fields."""
    schema = get_config_schema()
    result: dict[str, str] = {}
    for item in schema:
        if item.get("secret"):
            env_var = item.get("env_var", f"AI_MEMORY_{item['key'].upper()}")
            result[item["key"]] = env_var
    return result


def save_config(values: dict[str, Any], hermes_home: str) -> list[str]:
    """Save non-secret config values to disk. Returns list of skipped secret keys."""
    secrets = _secret_keys()
    skipped: list[str] = []
    safe_values: dict[str, Any] = {}
    for k, v in values.items():
        if k in secrets:
            log.info("secret %s not written to disk — set %s instead", k, secrets[k])
            skipped.append(k)
            continue
        safe_values[k] = v

    p = Path(hermes_home) / "ai-memory.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if p.exists():
        with contextlib.suppress(Exception):
            existing = json.loads(p.read_text())

    # Also strip any secrets already persisted in the file
    for secret_key in secrets:
        existing.pop(secret_key, None)

    existing.update(safe_values)
    p.write_text(json.dumps(existing, indent=2))
    return skipped


def load_config(hermes_home: str) -> AiMemoryConfig:
    p = Path(hermes_home) / "ai-memory.json"
    overrides: dict[str, Any] = {}

    if p.exists():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            overrides = json.loads(p.read_text())

    env_map: dict[str, str] = {
        "AI_MEMORY_SERVER_URL": "server_url",
        "AI_MEMORY_AUTH_TOKEN": "auth_token",
        "AI_MEMORY_API_KEY": "api_key",
        # NOTE: workspace and project are deliberately NOT env-overridable.
        # The ai-memory CLI ships shell wiring that exports AI_MEMORY_* into
        # interactive shells, while the Hermes gateway runs under systemd and
        # sees no such shell. An env override would let `hermes` started from a
        # terminal write to a different project than the daemon, splitting the
        # memory of one profile across two scopes with no visible signal.
        # Scope comes from ai-memory.json and the kwargs Hermes passes.
        "AI_MEMORY_RECALL_SCOPE": "recall_scope",
    }
    for env_key, attr in env_map.items():
        val = os.environ.get(env_key)
        if val:
            overrides[attr] = val

    overrides.setdefault("server_url", DEFAULT_SERVER_URL)
    overrides["recall_scope"] = normalize_recall_scope(
        overrides.get("recall_scope", DEFAULT_RECALL_SCOPE)
    )

    return AiMemoryConfig(**{k: v for k, v in overrides.items() if hasattr(AiMemoryConfig, k)})
