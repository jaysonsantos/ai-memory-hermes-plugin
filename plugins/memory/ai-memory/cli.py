from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Ensure sibling modules are findable when this file is loaded standalone
# (Hermes pre-loads submodules before executing __init__.py).
_PLUGIN_DIR = str(Path(__file__).resolve().parent)
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

from client import AiMemoryClient  # noqa: E402
from config import _secret_keys, load_config, save_config  # noqa: E402

PLUGIN_DIR = Path(__file__).resolve().parent

# Canonical fork this plugin is maintained in. Used only to print the pinned
# install command; nothing here downloads code.
REPO_URL = "https://github.com/jaysonsantos/ai-memory-hermes-plugin.git"
PLUGIN_SUBDIR = "plugins/memory/ai-memory"


def _hermes_home(args: argparse.Namespace) -> str:
    """Resolve the active Hermes profile directory.

    Hermes dispatches plugin commands with ``args.func(args)`` on a plain
    namespace (hermes_cli/main.py) and never sets ``hermes_home``, so reading
    ``args.hermes_home`` unconditionally raises AttributeError in every
    subcommand. Prefer an explicit attribute (tests set it), then the Hermes
    helper, then the environment.
    """
    explicit = getattr(args, "hermes_home", "")
    if explicit:
        return str(explicit)
    try:
        from hermes_constants import get_hermes_home  # type: ignore[import-not-found]

        return str(get_hermes_home())
    except Exception:
        return os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))


def cmd_status(args: argparse.Namespace) -> None:
    config = load_config(_hermes_home(args))
    client = AiMemoryClient(config)
    try:
        result = client.status()
        # ai-memory 2.1.0 nests the counters under "counts"; older servers
        # exposed them at the top level.
        counts = result.get("counts")
        counts = counts if isinstance(counts, dict) else {}
        pages = counts.get("pages_latest", result.get("pages", "?"))
        sessions = counts.get("sessions", result.get("sessions", "?"))
        print("ai-memory server: reachable")
        print(f"  Version:  {result.get('version', '?')}")
        print(f"  Pages:    {pages}")
        print(f"  Sessions: {sessions}")
    except Exception as e:
        print(f"ai-memory server: unreachable ({e})")


def cmd_config(args: argparse.Namespace) -> None:
    config = load_config(_hermes_home(args))
    secrets = _secret_keys()
    print("ai-memory configuration:")
    print(f"  server_url: {config.server_url}")
    for key, env_var in secrets.items():
        value = getattr(config, key, "")
        if os.environ.get(env_var):
            status = f"(set via env: {env_var})"
        elif value:
            status = "(set — migrate to env var)"
        else:
            status = "(not set)"
        print(f"  {key}: {status}")
    print(f"  workspace:    {config.workspace}")
    print(f"  project:      {config.project}")
    print(f"  recall_scope: {config.recall_scope}")


def cmd_config_set(args: argparse.Namespace) -> None:
    secrets = _secret_keys()
    key = args.key
    value = args.value

    if key in secrets:
        env_var = secrets[key]
        # Never echo the value. It would reach scrollback, terminal logs and
        # any session recording. The caller already holds it.
        print(f"NOT WRITTEN TO DISK: {key} is a secret — set it as an environment variable:")
        print(f"  export {env_var}=<value>")
        print("Add it to your shell profile, your .env, or systemd for persistence.")
        return

    hermes_home = _hermes_home(args)
    skipped = save_config({key: value}, hermes_home)
    if skipped:
        for s in skipped:
            print(f"  skipped: {s} (use env var)")
    print(f"  saved: {key} -> {Path(hermes_home) / 'ai-memory.json'}")


def cmd_link(args: argparse.Namespace) -> None:
    target_dir = Path(_hermes_home(args)) / "plugins" / "ai-memory"
    target_dir.parent.mkdir(parents=True, exist_ok=True)

    if target_dir.exists():
        if os.path.islink(str(target_dir)):
            print(f"Already linked: {target_dir} -> {PLUGIN_DIR}")
        else:
            print(f"Target exists but is not a symlink: {target_dir}")
        return

    try:
        os.symlink(str(PLUGIN_DIR), str(target_dir), target_is_directory=True)
        print(f"Linked: {target_dir} -> {PLUGIN_DIR}")
    except FileExistsError:
        print(f"Already linked: {target_dir}")
    except OSError as e:
        print(f"Failed to link: {e}")


def cmd_update(args: argparse.Namespace) -> None:
    """Print the pinned update procedure. This command downloads nothing.

    The previous implementation fetched
    ``<repo>/archive/refs/heads/main.zip`` and extracted it over the installed
    plugin, which Hermes then imports and runs. That pulled unreviewed branch
    HEAD with no commit pin, no checksum and no signature, and the source URL
    was overridable through the environment. Updates now go through Hermes
    plugin tooling, which records the exact commit in
    ``$HERMES_HOME/plugins/.install-metadata.json``.
    """
    hermes_home = Path(_hermes_home(args))
    plugin_dir = hermes_home / "plugins" / "ai-memory"

    print("==> ai-memory Hermes plugin update")
    print("")
    print(f"  Installed at: {plugin_dir}{'' if plugin_dir.exists() else '  (not installed)'}")
    print("")
    print("  This command does not download code. Self-update from a branch HEAD")
    print("  ran unreviewed remote code inside Hermes, so it was removed.")
    print("")
    print("  Update to a reviewed commit:")
    print("")
    print(f"    hermes plugins install {REPO_URL}#{PLUGIN_SUBDIR} \\")
    print("        --ref <40-character-commit-sha> --force --enable")
    print("")
    print("  Hermes verifies the ref and records it in")
    print(f"    {hermes_home / 'plugins' / '.install-metadata.json'}")
    print("")
    print("  Restart Hermes afterwards; the provider is built at agent start.")


# Subcommand table: (name, help, handler, [(flag, kwargs), ...]).
_SUBCOMMANDS: tuple[tuple[str, str, Callable[[argparse.Namespace], None], tuple[Any, ...]], ...] = (
    ("status", "Show ai-memory server connection status", cmd_status, ()),
    ("config", "Show ai-memory plugin configuration", cmd_config, ()),
    (
        "config-set",
        "Set a config value (secrets are env-only)",
        cmd_config_set,
        (("key", {"help": "Config key to set"}), ("value", {"help": "Value to set"})),
    ),
    ("link", "Link plugin into the Hermes plugin directory", cmd_link, ()),
    ("update", "Show the pinned update procedure (downloads nothing)", cmd_update, ()),
)

_HANDLERS = {name: handler for name, _help, handler, _args in _SUBCOMMANDS}


def ai_memory_command(args: argparse.Namespace) -> None:
    """Route ``hermes ai-memory <subcommand>``; bare invocation shows status."""
    sub = getattr(args, "ai_memory_command", None)
    handler = cmd_status if sub is None else _HANDLERS.get(sub)
    if handler is None:
        print(f"  Unknown ai-memory command: {sub}")
        print(f"  Available: {', '.join(name for name, *_ in _SUBCOMMANDS)}")
        return
    handler(args)


# Hermes resolves the top-level handler by the literal attribute
# "<provider>_command" (plugins/memory/__init__.py), and the provider is named
# "ai-memory" — not a valid Python identifier, so it is bound here.
globals()["ai-memory_command"] = ai_memory_command


def register_cli(parser: argparse.ArgumentParser) -> None:
    """Build the ``hermes ai-memory`` subcommand tree.

    Hermes passes the ``hermes ai-memory`` ArgumentParser itself
    (``_attach_plugin_cli_command`` in hermes_cli/main.py calls
    ``setup_fn(plugin_parser)``), not a ``_SubParsersAction``. Calling
    ``.add_parser()`` on it raised AttributeError, which aborted the whole
    plugin CLI registration loop and cost other plugins their commands too.
    """
    subs = parser.add_subparsers(dest="ai_memory_command")
    for name, help_text, _handler, arguments in _SUBCOMMANDS:
        sub = subs.add_parser(name, help=help_text)
        for flag, kwargs in arguments:
            sub.add_argument(flag, **kwargs)
    parser.set_defaults(func=ai_memory_command)
