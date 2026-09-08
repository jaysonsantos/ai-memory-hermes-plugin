#!/usr/bin/env bash
# install.sh — Install ai-memory Hermes plugin on Linux/macOS
#
# Works when run:
#   • locally from a cloned repo (scripts/install.sh or ./scripts/install.sh)
#   • from a checkout: ./scripts/install.sh
#
# Options:
#   --dry-run   Show what would be done without making changes
#   --yes, -y   Skip confirmation prompts
#
# Env vars:
#   FORCE=true            Skip confirmation prompts (same as --yes)
#   HERMES_HOME           Hermes profile directory (default: ~/.hermes)
#   AI_MEMORY_SERVER_URL  Server URL for config (default: http://127.0.0.1:49374)
#   AI_MEMORY_PLUGIN_REF     REQUIRED for download: 40-character commit SHA
#   AI_MEMORY_PLUGIN_SHA256  Optional sha256 of the downloaded tarball
#   AI_MEMORY_PLUGIN_REPO    owner/repo to download from (default: the fork)
set -euo pipefail

# --- Flag parsing ---
DRY_RUN=false
YES=false
FORCE="${FORCE:-false}"

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    --yes|-y)  YES=true ;;
    --help|-h)
      echo "Usage: $0 [--dry-run] [--yes]"
      echo ""
      echo "Options:"
      echo "  --dry-run   Show what would be done without making changes"
      echo "  --yes, -y   Skip confirmation prompts"
      echo ""
      echo "Env vars:"
      echo "  FORCE=true            Skip confirmation prompts"
      echo "  HERMES_HOME           Hermes profile directory (default: ~/.hermes)"
      echo "  AI_MEMORY_SERVER_URL  Server URL (default: http://127.0.0.1:49374)"
      echo "  AI_MEMORY_PLUGIN_REF     REQUIRED for download: 40-character commit SHA"
      echo "  AI_MEMORY_PLUGIN_SHA256  Optional sha256 of the downloaded tarball"
      echo "  AI_MEMORY_PLUGIN_REPO    owner/repo to download from (default: the fork)"
      exit 0
      ;;
  esac
done

AI_MEMORY_SERVER_URL="${AI_MEMORY_SERVER_URL:-http://127.0.0.1:49374}"

# --- Helper functions ---

# Print a line with optional color prefix
info()  { echo "  $*"; }
warn()  { echo "  WARNING: $*" >&2; }
err()   { echo "ERROR: $*" >&2; }
ok()    { echo "  OK: $*"; }

# --- Pinned source resolution ---------------------------------------------
# Downloads are pinned to an immutable commit. A branch-head tarball
# (archive/refs/heads/main) changes without notice, is not checksummed and is
# imported and executed by Hermes, so it is not accepted here.
REPO_SLUG="${AI_MEMORY_PLUGIN_REPO:-jaysonsantos/ai-memory-hermes-plugin}"
PLUGIN_REF="${AI_MEMORY_PLUGIN_REF:-}"
PLUGIN_SHA256="${AI_MEMORY_PLUGIN_SHA256:-}"

require_pinned_ref() {
  if ! printf '%s' "$PLUGIN_REF" | grep -Eq '^[0-9a-f]{40}$'; then
    err "AI_MEMORY_PLUGIN_REF must be a full 40-character commit SHA."
    err "Refusing to download an unpinned branch head."
    err "Example:"
    err "  AI_MEMORY_PLUGIN_REF=<40-hex-sha> $0"
    err "Or install through Hermes, which records the pin for you:"
    err "  hermes plugins install https://github.com/$REPO_SLUG.git#plugins/memory/ai-memory \\"
    err "      --ref <40-hex-sha> --force --enable"
    exit 1
  fi
  REPO_TARBALL_URL="https://codeload.github.com/$REPO_SLUG/tar.gz/$PLUGIN_REF"
}

# Download the pinned tarball into $1 and verify AI_MEMORY_PLUGIN_SHA256 when set.
fetch_pinned_tarball() {
  local dest="$1"
  curl -fsSL "$REPO_TARBALL_URL" -o "$dest"
  if [ -n "$PLUGIN_SHA256" ]; then
    if ! command -v sha256sum &>/dev/null; then
      err "sha256sum is required to verify AI_MEMORY_PLUGIN_SHA256."
      exit 1
    fi
    local actual
    actual="$(sha256sum "$dest" | cut -d" " -f1)"
    if [ "$actual" != "$PLUGIN_SHA256" ]; then
      err "checksum mismatch for $REPO_TARBALL_URL"
      err "  expected $PLUGIN_SHA256"
      err "  actual   $actual"
      exit 1
    fi
    ok "checksum verified"
  else
    warn "AI_MEMORY_PLUGIN_SHA256 is not set; the commit pin is the only integrity check."
  fi
}

# Run a command, or print it in dry-run mode
run() {
  if [ "$DRY_RUN" = "true" ]; then
    echo "  [DRY RUN] $*"
    return 0
  fi
  "$@"
}

# Prompt for confirmation. Returns 0 if proceed, 1 if abort.
confirm() {
  local msg="$1"
  if [ "$DRY_RUN" = "true" ]; then return 0; fi
  if [ "$YES" = "true" ] || [ "$FORCE" = "true" ]; then return 0; fi
  if [ ! -t 0 ]; then
    echo ""
    echo "  NOTE: non-interactive mode detected (piped input)."
    echo "  To skip this prompt, pass --yes or set FORCE=true."
    echo "  Proceeding with install..."
    return 0
  fi
  echo ""
  echo "$msg"
  read -r -p "  Proceed? [y/N] " answer
  case "$answer" in
    [yY][eE][sS]|[yY]) return 0 ;;
    *) echo "  Aborted."; exit 1 ;;
  esac
}

# --- Pre-flight checks ---
preflight() {
  local checks_passed=true
  echo ""
  echo "==> Pre-flight checks"
  echo ""

  # 1. HERMES_HOME
  info "HERMES_HOME:  $HERMES_HOME"
  if [ ! -d "$HERMES_HOME" ]; then
    warn "$HERMES_HOME does not exist — will be created."
  fi

  # 2. Write permissions
  local check_dir="$HERMES_HOME"
  if [ -d "$HERMES_HOME/plugins" ]; then
    check_dir="$HERMES_HOME/plugins"
  fi
  if [ -w "$check_dir" ] 2>/dev/null; then
    ok "Write permissions to $check_dir"
  else
    warn "No write permissions to $check_dir — install may fail."
    checks_passed=false
  fi

  # 3. Existing plugin
  if [ -f "$PLUGIN_DIR/__init__.py" ]; then
    warn "Plugin already installed at $PLUGIN_DIR"
    info "This will overwrite all plugin files."
    info "Config ($CONFIG_FILE) will NOT be touched."
    local version=""
    if command -v hermes &>/dev/null; then
      version=$(hermes plugins list 2>/dev/null | grep ai-memory | head -1 || true)
      if [ -n "$version" ]; then
        info "Current: $version"
      fi
    fi
  else
    ok "No existing plugin at $PLUGIN_DIR (fresh install)"
  fi

  # 4. Wrong path
  if [ -e "$WRONG_PLUGIN_DIR" ]; then
    warn "Found $WRONG_PLUGIN_DIR — this is the wrong path."
    info "User-installed plugins belong in $PLUGIN_DIR"
  fi

  # 5. Hermes CLI
  if command -v hermes &>/dev/null; then
    ok "Hermes CLI found: $(which hermes)"
  else
    warn "Hermes CLI not found — you will need to enable the plugin manually."
  fi

  # 6. Hermes process
  if pgrep -f "hermes" >/dev/null 2>&1; then
    warn "Hermes process appears to be running."
    info "You will need to restart Hermes after install for changes to take effect."
  fi

  # 7. ai-memory server
  info "Checking ai-memory server at $AI_MEMORY_SERVER_URL ..."
  if curl -sf --connect-timeout 3 "$AI_MEMORY_SERVER_URL/admin/status" >/dev/null 2>&1; then
    ok "ai-memory server is reachable"
  else
    warn "ai-memory server at $AI_MEMORY_SERVER_URL is not reachable."
    info "The plugin will install but will not function until the server is running."
  fi

  echo ""
  if [ "$checks_passed" = "false" ]; then
    echo "  Some checks failed. Review the warnings above before proceeding."
  else
    echo "  All checks passed."
  fi
}

# --- Main ---
echo "==> ai-memory Hermes plugin installer"
echo ""
if [ "$DRY_RUN" = "true" ]; then
  echo "  *** DRY RUN MODE — no changes will be made ***"
fi

# Locate this script's directory (works with symlinks)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PLUGIN_SRC="$PROJECT_DIR/plugins/memory/ai-memory"
DOWNLOAD_DIR=""

# If the local repo isn't present (e.g. curl one-liner via process substitution),
# download the plugin source from GitHub.
if [ ! -f "$PLUGIN_SRC/__init__.py" ]; then
  if ! command -v curl &>/dev/null || ! command -v tar &>/dev/null; then
    err "plugin source not found locally and curl/tar are required to download it."
    err "Either clone the repository or install curl and tar, then re-run."
    exit 1
  fi

  require_pinned_ref
  DOWNLOAD_DIR="$(mktemp -d)"
  info "Downloading plugin from $REPO_TARBALL_URL ..."
  if [ "$DRY_RUN" = "true" ]; then
    info "[DRY RUN] Would download from $REPO_TARBALL_URL"
  else
    fetch_pinned_tarball "$DOWNLOAD_DIR/repo.tar.gz"
    tar -xzf "$DOWNLOAD_DIR/repo.tar.gz" -C "$DOWNLOAD_DIR" --strip-components=1
  fi
  PLUGIN_SRC="$DOWNLOAD_DIR/plugins/memory/ai-memory"

  if [ ! -f "$PLUGIN_SRC/__init__.py" ]; then
    err "downloaded plugin source not found at $PLUGIN_SRC"
    rm -rf "$DOWNLOAD_DIR"
    exit 1
  fi
fi

# Resolve HERMES_HOME
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGIN_DIR="$HERMES_HOME/plugins/ai-memory"
CONFIG_FILE="$HERMES_HOME/ai-memory.json"
WRONG_PLUGIN_DIR="$HERMES_HOME/plugins/memory/ai-memory"

echo "  Source:      $PLUGIN_SRC"
echo "  Target:      $PLUGIN_DIR"
echo "  Server URL:  $AI_MEMORY_SERVER_URL"

# Run pre-flight checks
preflight

# Show planned actions
echo ""
echo "==> Planned actions"
echo ""
echo "  1. Create $HERMES_HOME/plugins/ (if needed)"
if [ -n "$DOWNLOAD_DIR" ]; then
  echo "  2. Copy plugin files from downloaded source → $PLUGIN_DIR"
else
  echo "  2. Symlink plugin: $PLUGIN_SRC → $PLUGIN_DIR"
fi
if [ ! -f "$CONFIG_FILE" ]; then
  echo "  3. Create config: $CONFIG_FILE"
else
  echo "  3. Config: $CONFIG_FILE (already exists, untouched)"
fi

# Confirmation
confirm "This will install the ai-memory plugin to:
  $PLUGIN_DIR

  Config file ($CONFIG_FILE) will not be modified if it already exists."

# --- Install ---
echo ""
echo "==> Installing"

# Create target directory
run mkdir -p "$HERMES_HOME/plugins"

# If the target exists but is empty, treat it as not installed
if [ -d "$PLUGIN_DIR" ] && [ ! -f "$PLUGIN_DIR/__init__.py" ]; then
  info "Target exists but is empty; removing and re-installing."
  run rm -rf "$PLUGIN_DIR"
fi

# Symlink or copy
if [ -n "$DOWNLOAD_DIR" ]; then
  run rm -rf "$PLUGIN_DIR"
  run cp -r "$PLUGIN_SRC" "$PLUGIN_DIR"
  info "Install:     copy (from downloaded source)"
elif command -v ln &>/dev/null; then
  run ln -sfn "$PLUGIN_SRC" "$PLUGIN_DIR"
  info "Install:     symlink"
else
  run cp -r "$PLUGIN_SRC" "$PLUGIN_DIR"
  info "Install:     copy"
fi

# Write initial config if none exists
if [ ! -f "$CONFIG_FILE" ]; then
  if [ "$DRY_RUN" = "true" ]; then
    info "[DRY RUN] Would create $CONFIG_FILE with server_url=$AI_MEMORY_SERVER_URL"
  else
    cat > "$CONFIG_FILE" <<EOF
{
  "server_url": "$AI_MEMORY_SERVER_URL",
  "workspace": "hermes",
  "project": "hermes-default"
}
EOF
  fi
  info "Config:      $CONFIG_FILE (created)"
else
  info "Config:      $CONFIG_FILE (exists, untouched)"
fi

# Cleanup temp download if used
if [ -n "$DOWNLOAD_DIR" ]; then
  run rm -rf "$DOWNLOAD_DIR"
  info "Cleanup:     removed temporary download"
fi

# Verify install
if [ "$DRY_RUN" != "true" ] && [ ! -f "$PLUGIN_DIR/__init__.py" ]; then
  echo ""
  err "install verification failed — $PLUGIN_DIR/__init__.py is missing."
  exit 1
fi

echo ""
if [ "$DRY_RUN" = "true" ]; then
  echo "==> Dry run complete. No changes were made."
  echo "    Re-run without --dry-run to apply."
else
  echo "==> Done. Run these commands to enable:"
  echo ""
  echo "    hermes plugins enable ai-memory"
  echo "    hermes memory setup"
  echo "    hermes memory status"
fi
