# ai-memory Hermes Memory Provider Plugin

[![CI](https://github.com/jaysonsantos/ai-memory-hermes-plugin/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/jaysonsantos/ai-memory-hermes-plugin/actions/workflows/ci.yml)

Connects [Hermes Agent](https://github.com/NousResearch/hermes-agent) to [ai-memory](https://github.com/akitaonrails/ai-memory) as a first-class `MemoryProvider` plugin — automatic prefetch, turn sync, session finalization, and wiki search/write tools.

## Origin and attribution

This repository is a fork of
[MrLuciano/ai-memory-hermes-plugin](https://github.com/MrLuciano/ai-memory-hermes-plugin).
Luciano Marinho ([@MrLuciano](https://github.com/MrLuciano)) created the
original plugin. This fork adds Hermes 0.21.1 and ai-memory 2.1 support and
the fixes listed in [CHANGELOG.md](CHANGELOG.md) from version 0.2.0 onward.
The upstream repository has no license file, so the original author keeps
all rights to the upstream work. See [NOTICE.md](NOTICE.md) for the full
attribution and the list of upstream contributors.

## Features

- **Auto-prefetch** — wiki context injected before every model turn
- **Turn capture** — async daemon-thread sync after each completed turn
- **Session finalization** — `session-end` hook on conversation close
- **Memory mirroring** — built-in `MEMORY.md` writes mirrored to ai-memory wiki
- **Hermes CLI integration** — `hermes memory setup`, `hermes memory status`
- **Per-profile isolation** — project scoped by Hermes profile name
- **Scoped recall** — reads stay inside the configured workspace/project; cross-project recall is opt-in
- **3 tool schemas** — `ai_memory_search`, `ai_memory_write`, `ai_memory_status`
- **Host-contract tests** — asserted against the installed Hermes ABC, not against the plugin itself
- **92% test coverage** — linted with ruff, type-checked with mypy, gated in CI on Python 3.10 to 3.14

## Quick Start

```bash
# Install a reviewed commit through Hermes plugin tooling.
# --ref pins the exact commit and Hermes records it in
# $HERMES_HOME/plugins/.install-metadata.json
hermes plugins install https://github.com/jaysonsantos/ai-memory-hermes-plugin.git#plugins/memory/ai-memory \
    --ref <40-character-commit-sha> --enable

# Configure
hermes memory setup

# Verify
hermes memory status
```

Do not pipe an installer from a branch head into a shell. Branch HEAD changes
without notice, and Hermes imports and runs the installed files.

Or download and run `scripts/install.sh` (Linux/macOS) or `scripts/install.ps1` (Windows) from the repository.

## Architecture

```
Hermes Agent → MemoryProvider ABC → AiMemoryProvider → AiMemoryClient → HTTP → ai-memory server
```

| Layer | File | Role |
|---|---|---|
| `__init__.py` | Entry point | `register()` — loads config, creates provider, calls `ctx.register_memory_provider()` |
| `provider.py` | AiMemoryProvider | Implements `MemoryProvider` ABC; lifecycle hooks, tool dispatch |
| `client.py` | AiMemoryClient | Typed HTTP wrapper; search, write page, send hook, fetch handoff |
| `config.py` | AiMemoryConfig | Config dataclass + JSON persistence + env-var fallback + secret filtering |
| `cli.py` | CLI | `status`/`config`/`link` subcommands for `hermes memory` |
| `plugin.yaml` | Manifest | Metadata, hooks declaration, pip dependencies |

## Lifecycle

| Hermes hook | ai-memory call | Threading |
|---|---|---|
| `is_available()` | Checks `server_url` is configured | Sync |
| `initialize()` | Resolves workspace/project from kwargs | Sync |
| `prefetch(query)` | `GET /api/v1/search` (global) or MCP `memory_query` (project) | Sync |
| `queue_prefetch(query)` | Spawns prefetch thread | Daemon thread |
| `sync_turn(user, assistant)` | `POST /hook?event=user-prompt` | Daemon thread |
| `on_session_end(messages)` | `POST /hook?event=session-end` | Daemon thread |
| `on_memory_write(action, target, content)` | `POST /admin/write-page` | Sync |
| `handle_tool_call(name, args)` | Routes to search/write/status | Sync |

## Configuration

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `AI_MEMORY_SERVER_URL` | `http://127.0.0.1:49374` | ai-memory server address |
| `AI_MEMORY_API_KEY` | `""` | API key (sent as Bearer token) — **env-only, never written to disk** |
| `AI_MEMORY_AUTH_TOKEN` | `""` | Auth token (alias for api_key) — **env-only, never written to disk** |

Env vars override file config, which overrides defaults. Secrets (`api_key`, `auth_token`) are **never persisted to `ai-memory.json`** — they must be set via environment variables.

### Config File (`$HERMES_HOME/ai-memory.json`)

Written by `hermes memory setup` wizard. Only non-secret values are stored:

```json
{
  "server_url": "http://127.0.0.1:49374",
  "workspace": "hermes",
  "project": "hermes-default",
  "recall_scope": "project"
}
```

### Recall scope

`recall_scope` controls what recall reads. It does not affect writes, which
always stay in the configured workspace/project.

| Value | Behaviour |
| --- | --- |
| `project` (default) | Search only the configured `workspace`/`project` through MCP `memory_query` (hybrid ranker). |
| `global` | Search every project on the server through `GET /api/v1/search` with no scope parameters (cross-project FTS5, the same search as `memory_query(global=true)`). |

Recalled text is injected into the model turn. `global` therefore exposes every
project's notes to this agent, so it is an explicit opt-in. Set it with
`hermes memory setup`, `hermes ai-memory config-set recall_scope global`, or the
`AI_MEMORY_RECALL_SCOPE` environment variable. An unrecognised value falls back
to `project`.

### Secrets & Security

`api_key` and `auth_token` are treated as **env-only secrets**:
- They are **never written** to `ai-memory.json` by `save_config()` or `hermes memory setup`
- If they already exist in the JSON file (e.g. from a previous version), they are **stripped on next load**
- Set them via environment variables and add to your shell profile or systemd environment:

```bash
# Shell profile (~/.bashrc, ~/.zshrc, etc.)
export AI_MEMORY_API_KEY=sk-your-key-here
export AI_MEMORY_AUTH_TOKEN=your-token-here

# Or systemd
Environment="AI_MEMORY_API_KEY=sk-your-key-here"
```

The CLI shows where each secret is sourced from:

```bash
hermes ai-memory config
# → auth_token: (set via env: AI_MEMORY_AUTH_TOKEN)
# → api_key:    (not set)
```

Attempting to write a secret via `hermes ai-memory config-set` shows the env var to use instead:

```bash
hermes ai-memory config-set auth_token my-secret
# → NOT WRITTEN TO DISK: auth_token is a secret — use an environment variable:
#   export AI_MEMORY_AUTH_TOKEN='my-secret'
```

## Tools

| Tool | Description | Parameters |
|---|---|---|
| `ai_memory_search` | Search the wiki | `query` (req), `max_results` (opt, default 5) |
| `ai_memory_write` | Write a new wiki page | `path` (req), `body` (req), `tags` (opt) |
| `ai_memory_status` | Server health check | none |

## Development

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest --cov
```

CI runs the same commands on every push and pull request. It also runs the
host-contract tests against a pinned Hermes Agent checkout, `pip-audit` on
the locked dependencies, `zizmor` on the workflow files, and `shellcheck` on
the installer scripts. To run the audits locally:

```bash
uv sync --locked --group audit
uv export --format requirements-txt --no-emit-project --no-hashes --no-dev \
    --output-file requirements-audit.txt
uv run pip-audit --strict -r requirements-audit.txt
uv run zizmor .github/workflows
```

Dependabot opens weekly pull requests for GitHub Actions and Python
dependency updates.

### Project Structure

```
plugins/memory/ai-memory/
├── __init__.py       # Entry point — register()
├── plugin.yaml       # Hermes manifest
├── provider.py       # AiMemoryProvider
├── client.py         # AiMemoryClient (httpx)
├── config.py         # AiMemoryConfig + persistence
├── cli.py            # CLI subcommands
└── README.md         # In-plugin readme
tests/
├── test_config.py    # 12 tests
├── test_client.py    # 16 tests
├── test_provider.py  # 29 tests
├── test_entry.py     # 10 tests
└── test_cli.py       # 7 tests
```

## Requirements

- Python 3.10+
- Hermes Agent with plugin support
- ai-memory server (HTTP API at port 49374)
- `httpx >= 0.28`

## Deployment

### Install Scripts

| Platform | Script | Method |
|---|---|---|
| Linux/macOS | [`scripts/install.sh`](scripts/install.sh) | Bash — symlinks or copies plugin into `$HERMES_HOME`, writes initial config |
| Windows | [`scripts/install.ps1`](scripts/install.ps1) | PowerShell — creates NTFS junction or copies, writes initial config |

All scripts support **pre-flight checks**, **dry-run**, and **confirmation prompts**:

| Flag | Bash | PowerShell | Env var | Description |
|---|---|---|---|---|
| Dry run | `--dry-run` | `-DryRun` | — | Show what would happen without making changes |
| Skip prompts | `--yes` | `-Yes` | `FORCE=true` | Skip all confirmation prompts (for CI/automation) |

When piped (non-interactive), scripts detect the missing TTY and proceed with a warning. Set `FORCE=true` or pass `--yes`/`-Yes` to silence the warning.

Pre-flight checks verify: Hermes CLI availability, Hermes process status, ai-memory server reachability, plugin state, write permissions, and existing config.

### One-liner (Linux/macOS)

Run it from a checkout; it copies the plugin into `$HERMES_HOME/plugins/ai-memory`.

```bash
bash scripts/install.sh
```

Preview what the install would do (dry-run):

```bash
bash scripts/install.sh --dry-run
```

Skip confirmation prompts for automation:

```bash
FORCE=true bash scripts/install.sh
```

Set `AI_MEMORY_SERVER_URL` before running to override the default server address:

```bash
AI_MEMORY_SERVER_URL=http://10.0.0.42:49374 bash scripts/install.sh
```

### Windows (PowerShell)

Requires PowerShell 5.1+ with .NET (default on Windows 10/11 and Windows Server 2016+). Run it from a checkout; it copies the plugin into `$HERMES_HOME\plugins\ai-memory`.

```powershell
.\scripts\install.ps1
```

With custom server URL:

```powershell
$env:ServerUrl='http://10.0.0.42:49374'; .\scripts\install.ps1
```

Downloads require a pinned commit: set `$env:AI_MEMORY_PLUGIN_REF` to a full
40-character commit SHA, and optionally `$env:AI_MEMORY_PLUGIN_SHA256`.

### Uninstall

Removes the plugin from `$HERMES_HOME/plugins/ai-memory` and runs `hermes plugins disable ai-memory` if the Hermes CLI is available. The `ai-memory.json` config file is kept unless you pass the removal flag.

Linux/macOS:

```bash
bash scripts/uninstall.sh
# Dry-run (preview only):
bash scripts/uninstall.sh --dry-run
# Skip prompt:
bash scripts/uninstall.sh --yes
# Also remove config:
REMOVE_CONFIG=true bash scripts/uninstall.sh
```

Windows (PowerShell):

```powershell
.\scripts\uninstall.ps1
# Dry-run (preview only):
.\scripts\uninstall.ps1 -DryRun
# Skip prompt:
.\scripts\uninstall.ps1 -Yes
# Also remove config:
.\scripts\uninstall.ps1 -RemoveConfig
```

### Update

Fetches the latest plugin files from GitHub, backs up the current install to `$HERMES_HOME/.ai-memory-backups/ai-memory.bak.<timestamp>`, replaces the plugin, and preserves `ai-memory.json`.

Linux/macOS:

```bash
bash scripts/update.sh
# Dry-run (preview only):
bash scripts/update.sh --dry-run
# Skip prompt:
bash scripts/update.sh --yes
# Update to a reviewed commit through Hermes (recommended):
#   hermes plugins install https://github.com/jaysonsantos/ai-memory-hermes-plugin.git#plugins/memory/ai-memory \
#       --ref <40-character-commit-sha> --force --enable
```

Windows (PowerShell):

```powershell
.\scripts\update.ps1
# Dry-run (preview only):
.\scripts\update.ps1 -DryRun
# Skip prompt:
.\scripts\update.ps1 -Yes
```

Update from a local clone (preserves symlink/junction):

```bash
UPDATE_FROM_LOCAL=true bash scripts/update.sh
```

```powershell
$env:UPDATE_FROM_LOCAL="true"; .\scripts\update.ps1
```

The CLI also provides `hermes ai-memory update`, which downloads and replaces the plugin from GitHub.

### uv pip install (from repo)

If you have `uv` and are deploying from a local clone:

```bash
uv pip install --system -e "$PWD"  # installs httpx dependency
mkdir -p "$HERMES_HOME/plugins/ai-memory"
cp -r plugins/memory/ai-memory/* "$HERMES_HOME/plugins/ai-memory/"
```

More detailed deployment instructions in [docs/guide.md](docs/guide.md#deployment).

## References

- [Hermes Memory Provider Plugin docs](https://hermes-agent.nousresearch.com/docs/developer-guide/memory-provider-plugin)
- [ai-memory repository](https://github.com/akitaonrails/ai-memory)
- [docs/guide.md](docs/guide.md) — usage guide
- [docs/reference.md](docs/reference.md) — full API reference
- [docs/common-problems.md](docs/common-problems.md) — troubleshooting
