# ai-memory-hermes-plugin

## ai-memory Hermes Memory Provider Plugin
Connects [Hermes Agent](https://hermes-agent.nousresearch.com) to [ai-memory](https://github.com/akitaonrails/ai-memory) as a first-class memory provider.

## Quick Start

```bash
# Install plugin
mkdir -p "$HERMES_HOME/plugins/ai-memory"
cp -r plugins/memory/ai-memory/* "$HERMES_HOME/plugins/ai-memory/"

# Enable
hermes plugins enable ai-memory
hermes memory setup

# Verify
hermes memory status
```

## Development

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest --cov
```

CI (`.github/workflows/ci.yml`) runs these commands on Python 3.10 to 3.14,
plus the Hermes host-contract tests, `pip-audit`, `zizmor`, and `shellcheck`.

## Project Structure

```
plugins/memory/ai-memory/
├── __init__.py   # Plugin entry point
├── plugin.yaml   # Hermes metadata
├── provider.py   # AiMemoryProvider implementation
├── client.py     # AiMemoryClient HTTP wrapper
├── config.py     # Config schema + persistence
└── README.md     # Plugin setup instructions

## Docs
- [README.md](README.md) — overview, features, quick start
- [docs/guide.md](docs/guide.md) — detailed usage, lifecycle, tool guide
- [docs/reference.md](docs/reference.md) — full API reference (config, client, provider, CLI)
- [docs/common-problems.md](docs/common-problems.md) — troubleshooting
- [CHANGELOG.md](CHANGELOG.md) — version history

## Project info
- **Version:** 0.2.2
- **Branch:** main
- **Remote (this fork):** https://github.com/jaysonsantos/ai-memory-hermes-plugin.git
- **Upstream (original author, MrLuciano):** https://github.com/MrLuciano/ai-memory-hermes-plugin.git
- **Attribution:** see [NOTICE.md](NOTICE.md)



```
