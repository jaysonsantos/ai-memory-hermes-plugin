# Reference

## Configuration

### `AiMemoryConfig`

Dataclass in `config.py`. Fields:

| Field | Type | Default | Description |
|---|---|---|---|
| `server_url` | `str` | `http://127.0.0.1:49374` | ai-memory HTTP API endpoint |
| `api_key` | `str` | `""` | API key (Bearer token) |
| `auth_token` | `str` | `""` | Auth token alias |
| `workspace` | `str` | `"hermes"` | ai-memory workspace name |
| `project` | `str` | `"hermes-default"` | ai-memory project name |
| `recall_scope` | `str` | `"project"` | `project` searches only the configured workspace/project; `global` reads every project on the server |

Precedence: **env vars > file config > defaults**

### Config File Location

`$HERMES_HOME/ai-memory.json`

### Config Schema (for `hermes memory setup`)

| Key | Secret | Env Only | Env Var | Default |
|---|---|---|---|---|
| `server_url` | no | no | — | `http://127.0.0.1:49374` |
| `api_key` | yes | yes | `AI_MEMORY_API_KEY` | `""` |
| `auth_token` | yes | yes | `AI_MEMORY_AUTH_TOKEN` | `""` |
| `workspace` | no | no | — | `"hermes"` |
| `project` | no | no | — | `"hermes-default"` |
| `recall_scope` | no | no | `AI_MEMORY_RECALL_SCOPE` | `"project"` |

**Env Only** fields are never written to `ai-memory.json`. They must be set via environment variables.

## `AiMemoryClient`

Typed HTTP wrapper in `client.py`. Accepts `AiMemoryConfig`. Persistent `httpx.Client` with connection pooling.

### Methods

#### `search(query, workspace=None, project=None, limit=3, global_search=False) → list[dict]`

Two explicit scopes. The client never infers a scope.

| Call | HTTP | Server search |
|---|---|---|
| `search(q, workspace=ws, project=proj)` | `POST /mcp` → `memory_query` with `workspace` and `project` | Hybrid ranker: FTS5, entity, graph, vector when an embedder exists. Also returns `_global` preference hits. |
| `search(q, global_search=True)` | `GET /api/v1/search?q=<query>&limit=<n>` with no scope parameters | Cross-project FTS5 (`SearchMode::Global`). Same query and reranking as `memory_query(global=true)`. |

**Timeout:** 6s (`SEARCH_TIMEOUT`, inside the 8s Hermes prefetch budget)  
**Raises:** `httpx.HTTPStatusError` on non-2xx, `RuntimeError` on an MCP error
envelope, `ValueError` on a half scope or on a scope next to `global_search`.

Global hits carry `workspace`, `project`, `path`, `title`, `kind`, `snippet`
and `rank`. Project hits carry `id`, `path`, `title`, `snippet` and `rank`.

`/api/v1` exists only when ai-memory runs with `--enable-web`. If the route
answers 404, the client falls back to `memory_query(global=true)` and reads
its `global_hits` list. It renames `workspace_name` and `project_name` to
`workspace` and `project`, so the hit shape stays the same.

#### `write_page(path, body, tags=None, tier=None, pinned=False, workspace=None, project=None) → dict`

**HTTP:** `POST /admin/write-page`  
**Timeout:** 10s  
**Raises:** `httpx.HTTPStatusError` on non-2xx

Returns `{"ok": true, "path": "..."}` or error dict.

#### `status() → dict`

**HTTP:** `GET /admin/status`  
**Timeout:** 10s

Returns `{"pages": N, "sessions": N, ...}`.

#### `send_hook(event, session_id, payload=None, workspace=None, project=None) → None`

**HTTP:** `POST /hook?event=<event>&session_id=<sid>&workspace=<ws>&project=<proj>`  
**Timeout:** 0.5s  
**Errors:** Swallowed (logged at exception level)

#### `fetch_handoff(agent="hermes", cwd=None, workspace=None, project=None) → str | None`

**HTTP:** `GET /handoff?agent=<agent>&cwd=<cwd>`  
**Timeout:** 10s  
**Returns:** `None` on 404, raises on other errors

## `AiMemoryProvider`

Implements `MemoryProvider` ABC in `provider.py`.

### Properties

- `name → "ai-memory"`

### Methods

- `is_available() → bool` — checks `server_url` is non-empty
- `initialize(session_id, **kwargs)` — resolves workspace/project, reloads config
- `get_config_schema() → list[dict]`
- `save_config(values, hermes_home) → list[str]` — returns list of skipped secret keys
- `get_tool_schemas() → list[dict]`
- `handle_tool_call(name, args) → str` (returns JSON string)
- `system_prompt_block() → str`
- `prefetch(query, *, session_id="") → str`
- `queue_prefetch(query)` — spawns daemon thread
- `sync_turn(user, assistant, *, session_id="", **kwargs)` — daemon thread
- `on_session_end(messages, **kwargs)` — daemon thread
- `on_memory_write(action, target, content, metadata=None)`
- `shutdown()`

## CLI (`register_cli`)

Registered via `register_cli(subparsers)` in `cli.py`.

### Subcommands

#### `hermes memory status`

Checks ai-memory server reachability. Prints page/session counts or error.

#### `hermes memory config`

Displays active config values (secrets show their source: env var or not set).

#### `hermes memory config-set <key> <value>`

Sets a config value. Secrets (`api_key`, `auth_token`) are rejected with instructions to use the corresponding environment variable instead.

#### `hermes memory link`

Creates a symlink `$HERMES_HOME/plugins/ai-memory → <plugin_dir>`.

## `plugin.yaml`

```yaml
name: ai-memory
version: 0.1.0
description: "ai-memory wiki-backed long-term memory provider"
entry_point: __init__.py
pip_dependencies:
  - httpx
hooks:
  - on_session_end
  - sync_turn
  - on_memory_write
```

## `register(ctx)`

Entry point in `__init__.py`:

1. Resolves config: file (`$HERMES_HOME/ai-memory.json`) → env var overrides (env wins for secrets)
2. Creates `AiMemoryConfig` with merged values (secrets env-only, never persisted)
3. Instantiates `AiMemoryProvider(config=config)`
4. Calls `ctx.register_memory_provider(provider)`

## Install Scripts

| Platform | Script | Requirements | Behavior |
|---|---|---|---|
| Linux/macOS | `scripts/install.sh` | `curl`, `tar` | Symlinks plugin from local repo; downloads only a pinned commit (`AI_MEMORY_PLUGIN_REF`) |
| Windows | `scripts/install.ps1` | PowerShell 5.1+ with .NET | Creates junction from local repo; downloads only a pinned commit (`AI_MEMORY_PLUGIN_REF`) |
| Linux/macOS | `scripts/uninstall.sh` | `bash` | Removes plugin directory/symlink; disables plugin in Hermes if CLI exists |
| Windows | `scripts/uninstall.ps1` | PowerShell 5.1+ | Removes plugin directory/junction; disables plugin in Hermes if CLI exists |
| Linux/macOS | `scripts/update.sh` | `curl`, `tar` | Downloads a pinned commit, backs up old install, preserves config |
| Windows | `scripts/update.ps1` | PowerShell 5.1+ with .NET | Downloads a pinned commit, backs up old install, preserves config |

All scripts run **pre-flight checks** before making changes:

1. Hermes CLI availability (warn if missing)
2. Hermes process status (warn if running — restart needed)
3. ai-memory server reachability (try `/admin/status` endpoint)
4. Existing plugin state (installed? symlink/copy? empty?)
5. Write permissions to `$HERMES_HOME/plugins/`
6. Config file existence and contents
7. Wrong nested path detection (`plugins/memory/ai-memory/`)

### Script Flags

| Flag | Bash | PowerShell | Env var | Description |
|---|---|---|---|---|
| Dry run | `--dry-run` | `-DryRun` | — | Show what would happen without making changes |
| Skip prompts | `--yes` | `-Yes` | `FORCE=true` | Skip confirmation prompts (for CI/automation) |
| Help | `--help` | — | — | Show usage information |

When piped (non-interactive), scripts detect the missing TTY, print a warning, and proceed. Set `FORCE=true` or pass `--yes`/`-Yes` to silence the warning.

### Environment Variables

| Variable | Used By | Description |
|---|---|---|
| `HERMES_HOME` | install/uninstall | Hermes profile directory (default: `~/.hermes` / `%USERPROFILE%\.hermes`) |
| `AI_MEMORY_SERVER_URL` | install | Initial `server_url` written to `ai-memory.json` |
| `AI_MEMORY_PLUGIN_REF` | install, update | REQUIRED to download: full 40-character commit SHA. No branch heads. |
| `AI_MEMORY_PLUGIN_SHA256` | install, update | Optional sha256 of the downloaded archive; mismatch aborts |
| `AI_MEMORY_PLUGIN_REPO` | install, update | `owner/repo` to download from (default: `jaysonsantos/ai-memory-hermes-plugin`) |
| `REMOVE_CONFIG` | uninstall (bash) | Set to `true` to delete `$HERMES_HOME/ai-memory.json` |
| `FORCE` | all scripts | Set to `true` to skip confirmation prompts (same as `--yes` / `-Yes`) |
| `DRY_RUN` | all scripts | Set to `true` to enable dry-run mode (same as `--dry-run` / `-DryRun`) |

## Quality Gates

| Check | Command | Target |
|---|---|---|
| Lint | `uv run ruff check .` | 0 errors |
| Format | `uv run ruff format --check .` | 0 files to change |
| Types | `uv run mypy .` | 0 issues |
| Tests | `uv run pytest --cov` | 191 tests, ≥89% coverage |
| Dependency audit | `uv run pip-audit --strict -r requirements-audit.txt` | 0 known vulnerabilities |
| Workflow audit | `uv run zizmor .github/workflows` | 0 findings |
| Shell scripts | `shellcheck --severity=warning scripts/*.sh` | 0 warnings |

CI runs every check on each push and pull request. The test job runs on
Python 3.10, 3.11, 3.12, 3.13, and 3.14. A separate job runs
`tests/test_hermes_contract.py` against a pinned Hermes Agent checkout.
