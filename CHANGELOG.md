# Changelog

## [Unreleased]

### Added

- GitHub Actions CI (`.github/workflows/ci.yml`): ruff lint and format,
  mypy, pytest with the coverage gate on Python 3.10 to 3.14, the Hermes
  host-contract tests against a pinned Hermes Agent checkout, `pip-audit`
  on the locked dependencies, `zizmor` on the workflows, and `shellcheck`
  on the installer scripts. Actions are pinned to commit SHAs and each job
  has read-only permissions.
- Dependabot for GitHub Actions and Python dependencies.
- `NOTICE.md` with the upstream attribution, the fork point, and the
  license status. `pyproject.toml` now records the original author, the
  fork maintainer, and the repository URLs.
- Ruff rule sets `B` (bugbear), `S` (bandit), `C4`, and `RUF`.

### Changed

- `config.py` uses `contextlib.suppress` for the two ignored read errors.
  No behaviour change.
- `scripts/update.sh` drops an unused variable that shellcheck reported.

## [0.2.2] — 2026-09-09

### Changed — global recall through the REST API

- Global recall (`recall_scope: global`) now calls `GET /api/v1/search` with
  no scope parameters. ai-memory 2.1.1 treats a request without `workspace`,
  `project` or `scopes` on that route as the cross-project search
  (`SearchMode::Global` in `ai-memory-web/src/routes/api.rs`). It runs the
  same `pages_fts` query and authority reranking as MCP
  `memory_query(global=true)` (`ReaderPool::search_pages` versus
  `search_pages_with_meta`). Each hit carries `workspace`, `project` and
  `kind`. Three live queries against the deployed 2.1.1 server returned
  identical hits from both paths.
- Project recall stays on MCP `memory_query` with the full workspace/project
  pair. That path runs the hybrid ranker (FTS5, entity, graph and vector
  streams) and unions the `_global` preferences scope. The REST route is
  FTS5-only for one project, so a move would lose result quality.
- `AiMemoryClient.search` raises `ValueError` when a `workspace` or `project`
  is passed together with `global_search=True`.

### Fixed

- Global recall returned no hits. `memory_query(global=true)` answers
  `hits: []` and puts the results under `global_hits`, and the client read
  `hits` only. The MCP path now reads `global_hits`. It is the fallback when
  `GET /api/v1/search` answers 404, which happens when the server runs
  without `--enable-web`. The fallback renames `workspace_name` and
  `project_name` to `workspace` and `project`, so both paths return the same
  hit shape.

## [0.2.1] — 2026-09-09

- Route provider recall through the canonical MCP `memory_query` operation.
- Send `global: true` explicitly for global recall instead of relying on omitted scope fields.

## [0.2.0] — 2026-09-08

Compatibility with Hermes Agent 0.21.1 and ai-memory 2.1.0, plus two security
fixes. Forked to `jaysonsantos/ai-memory-hermes-plugin` from upstream
`087e31014fca810f47310b7a0c1a3f93e927fcae`.

### Fixed — Hermes 0.21.1 compatibility

- **Tool schemas now use `parameters`, not `input_schema`** (blocking). Hermes
  hands each schema to the active model adapter. The openai-codex preflight
  raised `tools[N] is missing valid parameters`, and one bad tool aborts the
  whole request, so every model call in a session failed. On Anthropic the
  tools reached the model with no properties, so the model could not pass a
  query.
- **`queue_prefetch` accepts keyword-only `session_id`.** Hermes always passes
  it. The old signature raised `TypeError`, which Hermes caught as a non-fatal
  warning, so background recall never ran.
- **`on_memory_write` mirrors the actions Hermes actually sends.** It accepted
  `write` and `append`; Hermes sends `add`, `replace` and `remove`, so no write
  was ever mirrored. The mirror now reads the page, applies the action and
  writes it back: `add` appends, `replace` swaps the entry matching
  `metadata["old_text"]`, `remove` drops it. The previous code passed the single
  entry as the whole page body, which would have erased every other entry. An
  absent or ambiguous `old_text` leaves the page untouched.
- **`recall_status()` implemented.** It returns the Hermes `RecallStatus` for
  the last prefetch, so the recall indicator appears.
- **Plugin CLI registration repaired.** `register_cli` now takes the
  `ArgumentParser` Hermes passes, not a `_SubParsersAction`. The old code called
  `add_parser()` on it and raised `AttributeError`, which aborted the whole
  plugin CLI loop — other plugins lost their commands too. The module also
  exposes the `ai-memory_command` handler Hermes looks up by name.
- **CLI subcommands resolve `hermes_home` themselves.** Hermes dispatches
  `args.func(args)` on a namespace with no `hermes_home`, so every subcommand
  raised `AttributeError`.

### Fixed — ai-memory 2.1.0 compatibility

- `hermes ai-memory status` reads `counts.pages_latest` and `counts.sessions`.
  2.1.0 nests the counters, so the previous flat keys printed `Pages: ?`.
- Timeouts sized for a remote HTTPS server. A cold connect measured 2.28 s
  against the configured server, and the 0.5 s hook timeout lost turns in
  silence. `HOOK_TIMEOUT` 0.5 → 5.0, `HANDOFF_TIMEOUT` 2.0 → 8.0,
  `SEARCH_TIMEOUT` 10.0 → 6.0 (Hermes bounds external prefetch at 8.0 s, so the
  client must fail first).
- New `AiMemoryClient.read_page()` for `GET /admin/read-page`, used by the
  memory mirror.

### Changed — recall scope

- **Recall is scoped to the configured workspace/project by default.** It used
  to search every project on the server and inject the text into the Hermes
  turn. Cross-project recall is still available through the new `recall_scope`
  config key (`project` by default, `global` to opt in, also settable with
  `AI_MEMORY_RECALL_SCOPE`). An unrecognised value narrows to `project`.
- `queue_prefetch` now caches its result and `prefetch` consumes it, so the
  common recall path costs no network round-trip inside the turn.

### Security

- **Removed the unpinned self-update.** `hermes ai-memory update` fetched
  `archive/refs/heads/main.zip`, extracted it over the installed plugin and let
  Hermes import and run it — branch HEAD, no commit pin, no checksum, no
  signature, and the source URL was overridable through the environment. The
  command now downloads nothing and prints the pinned
  `hermes plugins install --ref <sha>` procedure instead.
- **Install and update scripts refuse an unpinned download.** They require
  `AI_MEMORY_PLUGIN_REF` to be a full 40-character commit SHA and verify
  `AI_MEMORY_PLUGIN_SHA256` when it is set. `REPO_TARBALL_URL` was removed.
- **`hermes ai-memory config-set` no longer echoes a secret value.** It printed
  `export AI_MEMORY_AUTH_TOKEN='<value>'`, putting the secret into scrollback,
  terminal logs and any session recording. It now prints the variable name only.
- Removed the `curl | bash` and `iex` one-liners from the README and docs. Both
  fetched branch HEAD and ran it.

### Security (follow-up)

- **`workspace` and `project` are not environment-overridable.** An interim
  version of this change read `AI_MEMORY_WORKSPACE` and `AI_MEMORY_PROJECT`
  from the environment. The ai-memory CLI exports `AI_MEMORY_*` into
  interactive shells, while the Hermes gateway runs under systemd and sees no
  such shell, so a stray export would send `hermes` started from a terminal to
  a different project than the daemon — one profile's memory split across two
  scopes with no visible signal. Scope now comes only from `ai-memory.json` and
  the kwargs Hermes passes. `AI_MEMORY_RECALL_SCOPE` stays env-settable: it
  changes reads only, never where writes land.
- `docs/guide.md` documented `AI_MEMORY_PROJECT` as a working override. No
  version of the provider ever read it. Corrected.

### Added

- `tests/test_hermes_contract.py` — contract tests against the INSTALLED Hermes
  Agent. They drive the provider through the real `MemoryManager`,
  `normalize_tool_schema`, the codex preflight and
  `_attach_plugin_cli_command`. 20 of them fail against upstream `087e310`. The
  previous suite passed 123 tests while the plugin was incompatible, because it
  asserted the plugin's own shape. They skip when Hermes is absent.
- Coverage is scoped to the plugin (`tool.coverage.run.source`); the contract
  tests import the Hermes tree, which otherwise sank the gate.
- `tool.uv.dev-dependencies` moved to `dependency-groups.dev` (deprecated).


## [Unreleased]

### Added

- `scripts/update.sh` — updates the plugin from GitHub by default; backs up the old install to `$HERMES_HOME/.ai-memory-backups/ai-memory.bak.<timestamp>` (outside the Hermes plugins directory so it is not discovered as a plugin); preserves `ai-memory.json`; supports `UPDATE_FROM_LOCAL=true` and `REPO_TARBALL_URL` overrides.
- `scripts/update.ps1` — Windows equivalent with the same defaults and backup behavior.
- `hermes ai-memory update` CLI command — downloads the latest plugin from GitHub, backs up the old install, and replaces the plugin files.
- `scripts/uninstall.sh` — removes `$HERMES_HOME/plugins/ai-memory`, disables the plugin in Hermes if the CLI is available, and optionally removes `$HERMES_HOME/ai-memory.json` when `REMOVE_CONFIG=true`.
- `scripts/uninstall.ps1` — Windows equivalent; removes `$HERMES_HOME\plugins\ai-memory`, disables the plugin in Hermes if the CLI is available, and optionally removes `$HERMES_HOME\ai-memory.json` with `-RemoveConfig`.
- `hermes ai-memory config-set` CLI command — sets config values; rejects secrets with env-var instructions.
- Config schema now marks `api_key` and `auth_token` as `env_only: true` (env vars only, never persisted to disk).
- `save_config()` now filters secrets before writing to `ai-memory.json` and strips any existing secrets from the file. Returns list of skipped secret keys.
- `cmd_config` shows the source of each secret: `(set via env: AI_MEMORY_AUTH_TOKEN)` or `(not set)`.
- **Pre-flight checks** for all install/uninstall/update scripts: verifies Hermes CLI, Hermes process, ai-memory server reachability, plugin state, write permissions, and wrong-path detection before making any changes.
- **Dry-run mode** (`--dry-run` / `-DryRun`): all scripts show what would happen without making changes.
- **Confirmation prompts**: all scripts prompt before destructive actions. Use `--yes`/`-Yes` or `FORCE=true` to skip (for CI/automation).
- **Non-interactive detection**: when piped, scripts detect missing TTY, print a warning, and proceed. `FORCE=true` silences the warning.

### Fixed

- Install/update scripts and `hermes ai-memory update` now detect an empty `$HERMES_HOME/plugins/ai-memory/` directory and re-install instead of treating it as already installed.
- Install/update scripts and CLI command now verify that `__init__.py` exists after install/update and fail loudly if it is missing.
- Install/update scripts now warn if the plugin is found at the wrong nested path `$HERMES_HOME/plugins/memory/ai-memory/`.
- Update backups are now stored in `$HERMES_HOME/.ai-memory-backups/` instead of `$HERMES_HOME/plugins/`, preventing Hermes from discovering backup directories as additional `ai-memory` memory-provider plugins.
- `scripts/install.sh` one-liner (`bash <(curl -sL ...)`) now works when the script is streamed via process substitution. It falls back to downloading the plugin from GitHub and copying it into `$HERMES_HOME/plugins/ai-memory`.
- `scripts/install.ps1` one-liner (`iex ((Invoke-WebRequest ...).Content)`) now works when the script runs in memory. It falls back to downloading the plugin from GitHub and copying it into `$HERMES_HOME\plugins\ai-memory`.
- `AiMemoryProvider.is_available()` now returns `True` whenever `server_url` is configured, instead of requiring an auth token. Hermes only activates a memory provider when `is_available()` is `True`; requiring auth made the plugin appear inactive for default local installs.
- Secrets (`api_key`, `auth_token`) are no longer written to `ai-memory.json`. Existing secrets in the config file are stripped on load. Users must set secrets via environment variables.
- `scripts/update.sh` and `hermes ai-memory update` now strip secrets from backed-up config files when restoring.

### Documentation

- Updated README, docs/guide.md, docs/reference.md, and docs/common-problems.md to document install/uninstall scripts, fallback behavior, config-removal flags, the `REPO_TARBALL_URL` override variable, env-only secret handling, and pre-flight/dry-run/confirmation features.

## [0.1.0] — 2026-07-03

### Added

- **Phase 1 — Config** (REQ-001–REQ-006):
  - `AiMemoryConfig` dataclass with typed fields
  - Config schema for `hermes memory setup` wizard
  - JSON file persistence (`$HERMES_HOME/ai-memory.json`)
  - Env-var fallback (`AI_MEMORY_SERVER_URL`, `AI_MEMORY_API_KEY`, `AI_MEMORY_AUTH_TOKEN`)
  - Extra-key filtering on config load (future-proof)
  - Roundtrip and corrupt-file handling

- **Phase 2 — Client** (REQ-007–REQ-010):
  - `AiMemoryClient` typed HTTP wrapper using `httpx.Client`
  - `search()` — `GET /admin/search` with workspace/project scoping
  - `write_page()` — `POST /admin/write-page` with tier, pinned, tags
  - `status()` — `GET /admin/status`
  - `send_hook()` — `POST /hook` with event/session/payload
  - `fetch_handoff()` — `GET /handoff` with 404 → None fallback
  - Persistent session (connection pooling) + per-request timeouts
  - Auth header injection via Bearer token

- **Phase 3 — Provider** (REQ-011–REQ-019):
  - `AiMemoryProvider` implementing Hermes `MemoryProvider` ABC
  - `is_available()` — checks credentials (not server URL default)
  - `initialize()` — resolves workspace/project from kwargs, reloads config
  - `prefetch()` — synchronous search before each model turn
  - `queue_prefetch()` — daemon-thread background search
  - `sync_turn()` — daemon-thread turn capture (swallows errors)
  - `on_session_end()` — daemon-thread session finalization
  - `on_memory_write()` — mirrors Hermes built-in memory to ai-memory wiki
  - `handle_tool_call()` — dispatches search/write/status
  - `system_prompt_block()` — model context injection
  - Return-type alignment (`str` not `str | None`)
  - Kwargs absorption on all hook methods
  - `metadata` param on `on_memory_write`

- **Phase 4 — Entry Point** (REQ-020–REQ-022):
  - `register()` using `ctx.register_memory_provider(instance)`
  - Config file loading from `$HERMES_HOME` + env-var overrides
  - `sys.path` insertion for Hermes loader compatibility
  - `plugin.yaml` with hooks declaration (`on_session_end`, `sync_turn`, `on_memory_write`)
  - `pip_dependencies` in plugin metadata (`httpx`)

- **Phase 5 — CLI** (REQ-023–REQ-027):
  - `register_cli(subparsers)` — Hermes CLI integration
  - `cmd_status` — server reachability with page/session counts
  - `cmd_config` — config display (secrets masked)
  - `cmd_link` — symlink plugin into Hermes profile
  - Uses `AiMemoryClient` directly (no provider dependency)

- **Code Review Fixes — Round 1** (2026-07-03):
  - `is_available()` — fixed tautology (was `return True`)
  - Production import loading via `sys.path.insert` in `__init__.py`
  - Unified config precedence: env > file > defaults
  - Thread-safe config capture in daemon thread closures
  - `httpx` declared in `[project] dependencies`
  - `on_memory_write` wrapped in try/except
  - Persistent httpx.Client (connection pooling)
  - CLI uses AiMemoryClient directly instead of provider
  - `plugin.yaml` declares all 3 hooks
  - Bare `except:` → specific exception types
  - `kwargs.pop` → `.get` pattern
  - Test assertions for `send_hook` call chain

- **Code Review Fixes — Round 2** (2026-07-03):
  - Added `__all__ = ["register"]` to `__init__.py`
  - Documented `sys.path.insert` rationale in both `__init__.py` and `conftest.py`
  - `_write()` now defaults `ok` to `False` instead of `True`
  - `on_memory_write` now logs exception via `log.warning` instead of silent pass
  - `search()` handles non-dict API response gracefully
  - `test_queue_prefetch` verifies `prefetch` is actually called
  - Removed no-op `assert True` from hook error test
  - Added test for env-over-file config precedence
  - Added test for non-dict search response

### Technical

- 91 tests across 5 test files
- 94%+ test coverage
- ruff clean (0 errors)
- mypy clean (0 issues, with documented `ai-memory` exclusion)
- Python 3.10+ with `from __future__ import annotations`
- Dependencies: `httpx>=0.28`
- Dev tooling: pytest, pytest-asyncio, pytest-cov, ruff, mypy, pyyaml
