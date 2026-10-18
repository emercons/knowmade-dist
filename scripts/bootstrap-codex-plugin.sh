#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/codex-config.sh
. "$ROOT/scripts/lib/codex-config.sh"
SCOPE="user"
PROJECT_ROOT="${PWD}"
USER_HOME="${HOME}"
PRINT_ONLY=0
VERIFY=0
ALLOW_COEXISTING=0
# How long `codex plugin add` is retried in total when Windows refuses to
# replace the cache entry (see codex_plugin_add below): the sum of the waits
# between attempts, in seconds. The waits double from 1 s and are capped at
# CACHE_RETRY_CAP_SECONDS, so the default 30 s budget is about 8 attempts. 0
# disables retrying. A scanner that held the entry for more than ~10 s outlasted
# the old fixed 5-attempt schedule (knowmade#86).
CACHE_RETRY_SECONDS="${KNOWMADE_CODEX_CACHE_RETRY_SECONDS:-30}"
CACHE_RETRY_CAP_SECONDS=5
# Exit status when the budget ran out while Codex still could not replace the
# entry (EX_TEMPFAIL). Callers such as tests/test-codex-runtime.sh use it to
# tell "something outside this repository held a file" from a real failure.
EXIT_CACHE_HELD=75
case "$CACHE_RETRY_SECONDS" in
  ''|*[!0-9]*)
    echo "KNOWMADE_CODEX_CACHE_RETRY_SECONDS must be a whole number of seconds, got '$CACHE_RETRY_SECONDS'" >&2
    exit 1
    ;;
esac

usage() {
  cat <<'EOF'
Usage: ./scripts/bootstrap-codex-plugin.sh [options]

Register this repo as a local Codex marketplace source, install @wiki and the
bundled $wiki-query preset into the Codex plugin cache, and enable the plugin
in the user config. Running it again refreshes the installed cache entry.

Options:
  --scope user           Plugin enablement scope (default: user). The current
                         Codex plugin CLI does not load project enablement.
  --project-root <dir>   Working directory for the optional verification probe
  --user-home <dir>      HOME used for Codex marketplace registration and
                         user-scope config writes (default: current HOME)
  --print                Print the managed TOML block without writing it
  --verify               Run scripts/verify-codex-plugin.sh after writing
  --allow-coexisting     Install even when another enabled plugin named wiki
                         (upstream wiki@llm-wiki) would make @wiki ambiguous
  -h, --help             Show this help

Environment:
  KNOWMADE_CODEX_CACHE_RETRY_SECONDS
                         On Windows a scanner or indexer can hold a file in the
                         Codex plugin cache and block the install's directory
                         rename. The install is retried (waits of 1, 2, 4, then
                         5 s) for up to this many seconds in total (default 30,
                         0 = no retry). When it runs out the helper names the
                         held file and exits 75.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --scope)
      [[ $# -ge 2 ]] || { echo "Missing value for --scope" >&2; exit 1; }
      SCOPE="$2"
      shift 2
      ;;
    --project-root)
      [[ $# -ge 2 ]] || { echo "Missing value for --project-root" >&2; exit 1; }
      PROJECT_ROOT="$2"
      shift 2
      ;;
    --user-home)
      [[ $# -ge 2 ]] || { echo "Missing value for --user-home" >&2; exit 1; }
      USER_HOME="$2"
      shift 2
      ;;
    --print)
      PRINT_ONLY=1
      shift
      ;;
    --verify)
      VERIFY=1
      shift
      ;;
    --allow-coexisting)
      ALLOW_COEXISTING=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

case "$SCOPE" in
  user) ;;
  project)
    echo "Project-scoped plugin enablement is not supported by the current Codex plugin CLI." >&2
    echo "Use --scope user; plugin hooks can still be enabled or disabled separately." >&2
    exit 1
    ;;
  *)
    echo "Invalid scope: $SCOPE (expected user)" >&2
    exit 1
    ;;
esac

if [[ "$PRINT_ONLY" -eq 1 && "$VERIFY" -eq 1 ]]; then
  echo "--print and --verify cannot be combined" >&2
  exit 1
fi

PROJECT_ROOT="$(cd "$PROJECT_ROOT" && pwd)"
USER_HOME="$(cd "$USER_HOME" && pwd)"
USER_CONFIG="$USER_HOME/.codex/config.toml"
# Codex keeps one directory per marketplace under plugins/cache and one entry
# per plugin inside it, with the installed version as the leaf directory.
PLUGIN_CACHE_ROOT="$USER_HOME/.codex/plugins/cache/$MARKETPLACE_NAME"
PLUGIN_CACHE_ENTRY="$PLUGIN_CACHE_ROOT/$PLUGIN_NAME"

MANAGED_BLOCK="$(python3 - "$PLUGIN_KEY" <<'PY'
import sys

plugin_key = sys.argv[1]

print(f'[plugins."{plugin_key}"]')
print('enabled = true')
PY
)"

if [[ "$PRINT_ONLY" -eq 1 ]]; then
  printf '%s\n' "$MANAGED_BLOCK"
  exit 0
fi

if ! command -v codex >/dev/null 2>&1; then
  echo "codex binary not found in PATH" >&2
  exit 1
fi

if ! PLUGIN_ADD_HELP="$(codex_in_home "$USER_HOME" plugin add --help 2>&1)"; then
  echo "This Codex version does not support non-interactive plugin installation." >&2
  echo "Upgrade Codex, then rerun this helper." >&2
  exit 1
fi

mkdir -p "$USER_HOME/.codex"

# Codex derives the skill id from the plugin name only, so an enabled upstream
# wiki@llm-wiki and wiki@knowmade both answer to wiki:wiki, @wiki and
# $wiki-query, and both plugins' hooks record every event (knowmade#124).
OTHER_WIKI_PLUGINS="$(other_enabled_plugins "$USER_CONFIG")"
if [[ -n "$OTHER_WIKI_PLUGINS" ]]; then
  if [[ "$ALLOW_COEXISTING" -eq 1 ]]; then
    echo "WARNING: also enabled, so @wiki stays ambiguous:" >&2
    sed 's/^/  /' <<<"$OTHER_WIKI_PLUGINS" >&2
  else
    echo "Another enabled Codex plugin is also named '$PLUGIN_NAME':" >&2
    sed 's/^/  /' <<<"$OTHER_WIKI_PLUGINS" >&2
    echo "With both enabled, @wiki and \$wiki-query resolve ambiguously and session hooks fire twice." >&2
    echo "Remove it (codex plugin remove <key>) or set enabled = false under its" >&2
    echo "[plugins.\"<key>\"] table in $USER_CONFIG, then rerun this helper." >&2
    echo "To install anyway, pass --allow-coexisting." >&2
    exit 1
  fi
fi

# Codex writes the registered source in whichever TOML quote style fits the
# path and, on Windows, in extended-length form; marketplace_source reads all
# of those and same_codex_path compares identities, so a second run recognises
# its own registration instead of re-adding it or refusing it as foreign.
MARKETPLACE_SOURCE="$(marketplace_source "$USER_CONFIG" "$MARKETPLACE_NAME")"

if [[ -z "$MARKETPLACE_SOURCE" ]]; then
  codex_in_home "$USER_HOME" plugin marketplace add "$ROOT"
elif ! same_codex_path "$MARKETPLACE_SOURCE" "$ROOT"; then
  echo "Codex marketplace '${MARKETPLACE_NAME}' already points at:" >&2
  echo "  $MARKETPLACE_SOURCE" >&2
  echo "This helper will not overwrite another checkout automatically." >&2
  echo "Use that checkout, or remove/re-add the marketplace in this Codex home first." >&2
  exit 1
fi

# Codex refreshes an installed entry by directory renames: it copies the plugin
# into a plugin-install-* staging directory beside the entry, moves the old
# entry to plugin-backup-*, and moves the staged tree into place. Windows
# refuses to rename a directory while any process holds a handle on a file
# beneath it, and the files Codex has just copied are exactly what a virus
# scanner or the search indexer opens next - so a repeated install failed with
# "failed to activate updated plugin cache entry: ... (os error 5)", or with
# the same error on the back-up step (knowmade#45). Codex restores the previous
# entry before it exits, so the install is safe to retry once the handle is
# gone; a failed attempt only leaves its staging directory behind, which
# sweep_cache_leftovers removes. The read-only bit travels with a copied file
# on Windows and would stop Codex from removing an old version, so the entry
# is made writable before every attempt.
sweep_cache_leftovers() {  # sweep_cache_leftovers [quiet]: quiet suppresses the still-in-use warning
  local leftover
  [[ -d "$PLUGIN_CACHE_ROOT" ]] || return 0
  for leftover in "$PLUGIN_CACHE_ROOT"/plugin-install-* "$PLUGIN_CACHE_ROOT"/plugin-backup-*; do
    [[ -e "$leftover" ]] || continue
    if ! rm -rf "$leftover" 2>/dev/null && [[ "${1:-}" != quiet ]]; then
      echo "warning: stale Codex cache directory is still in use and was left behind:" >&2
      echo "  $leftover" >&2
    fi
  done
}

make_cache_writable() {
  [[ -d "$PLUGIN_CACHE_ENTRY" ]] || return 0
  chmod -R u+w "$PLUGIN_CACHE_ENTRY" 2>/dev/null || true
}

is_cache_replace_error() {
  grep -Eq 'os error (5|32)\b|failed to (activate|back up) (updated )?plugin cache entry|failed to remove existing plugin cache entry' <<<"$1"
}

# find_held_file: best-effort name of the file that keeps Codex from replacing
# the entry. Windows refuses to rename a file another process holds open, so a
# rename to a probe name and back identifies it; on POSIX nothing is ever held
# and nothing is reported. Prints the first held path and succeeds, or fails.
find_held_file() {
  local root candidate probe
  for root in "$PLUGIN_CACHE_ROOT"/plugin-install-* "$PLUGIN_CACHE_ROOT"/plugin-backup-* "$PLUGIN_CACHE_ENTRY"; do
    [[ -d "$root" ]] || continue
    while IFS= read -r candidate; do
      probe="$candidate.knowmade-probe"
      if mv -- "$candidate" "$probe" 2>/dev/null; then
        mv -- "$probe" "$candidate" 2>/dev/null || true
      else
        printf '%s\n' "$candidate"
        return 0
      fi
    done < <(find "$root" -type f 2>/dev/null | head -n 2000)
  done
  return 1
}

# codex_plugin_add <codex plugin add arguments...>: run the install, retrying
# while Codex cannot replace the cache entry, for up to CACHE_RETRY_SECONDS of
# waiting (exponential backoff from 1 s, capped at CACHE_RETRY_CAP_SECONDS).
# Prints the stdout of the successful attempt (the JSON result when --json was
# given). When the budget runs out it names the held file where it can and
# returns EXIT_CACHE_HELD; any other failure returns Codex's own status.
codex_plugin_add() {
  local attempt=1 output errors status delay=1 waited=0 pause held
  local err_file
  err_file="$(mktemp)"
  while :; do
    sweep_cache_leftovers quiet
    make_cache_writable
    status=0
    output="$(codex_in_home "$USER_HOME" plugin add "$@" 2>"$err_file")" || status=$?
    errors="$(cat "$err_file")"
    if [[ "$status" -eq 0 ]]; then
      rm -f "$err_file"
      [[ -z "$errors" ]] || printf '%s\n' "$errors" >&2
      sweep_cache_leftovers
      printf '%s\n' "$output"
      return 0
    fi
    if ! is_cache_replace_error "$errors"; then
      rm -f "$err_file"
      [[ -z "$output" ]] || printf '%s\n' "$output" >&2
      printf '%s\n' "$errors" >&2
      return "$status"
    fi
    if [[ "$waited" -ge "$CACHE_RETRY_SECONDS" ]]; then
      rm -f "$err_file"
      [[ -z "$output" ]] || printf '%s\n' "$output" >&2
      printf '%s\n' "$errors" >&2
      echo "Codex could not replace the installed plugin cache entry after $attempt attempt(s) and ${waited}s of retrying:" >&2
      echo "  $PLUGIN_CACHE_ENTRY" >&2
      if held="$(find_held_file)"; then
        echo "A file under the plugin cache is held open by another process (virus scanner, search indexer, editor, a running Codex):" >&2
        echo "  $held" >&2
      else
        echo "No single held file could be identified; something may hold a directory under it." >&2
      fi
      echo "Close whatever holds it, or exclude $PLUGIN_CACHE_ROOT from scanning/indexing, then rerun." >&2
      echo "To wait longer, set KNOWMADE_CODEX_CACHE_RETRY_SECONDS (now $CACHE_RETRY_SECONDS)." >&2
      return "$EXIT_CACHE_HELD"
    fi
    pause="$delay"
    if [[ "$pause" -gt $((CACHE_RETRY_SECONDS - waited)) ]]; then
      pause=$((CACHE_RETRY_SECONDS - waited))
    fi
    echo "Codex could not replace the plugin cache entry (attempt $attempt); retrying in ${pause}s (${waited}s of ${CACHE_RETRY_SECONDS}s waited)" >&2
    sleep "$pause"
    waited=$((waited + pause))
    delay=$((delay * 2))
    if [[ "$delay" -gt "$CACHE_RETRY_CAP_SECONDS" ]]; then
      delay="$CACHE_RETRY_CAP_SECONDS"
    fi
    attempt=$((attempt + 1))
  done
}

# `codex plugin add` is the supported materialization path. It copies the
# plugin into ~/.codex/plugins/cache and writes the supported user-scope enable
# block. Marketplace registration alone does not install the plugin.
if grep -q -- '--json' <<<"$PLUGIN_ADD_HELP"; then
  INSTALL_OUTPUT="$(codex_plugin_add --json "$PLUGIN_KEY")"
  INSTALLED_PATH="$(python3 -c 'import json, sys; print(json.load(sys.stdin)["installedPath"])' <<<"$INSTALL_OUTPUT")"
else
  codex_plugin_add "$PLUGIN_KEY"
  INSTALLED_VERSION="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["version"])' "$ROOT/plugins/llm-wiki/.codex-plugin/plugin.json")"
  INSTALLED_PATH="$PLUGIN_CACHE_ENTRY/$INSTALLED_VERSION"
fi
TARGET="$USER_CONFIG"

echo "Installed Codex plugin:"
echo "  $PLUGIN_KEY"
echo "Installed cache:"
echo "  $INSTALLED_PATH"
echo "Enabled scope:"
echo "  $SCOPE"
echo "Codex plugin config:"
echo "  $TARGET"
echo "Source repo:"
echo "  $ROOT"
echo "Codex home:"
echo "  $USER_HOME"

if [[ "$VERIFY" -eq 1 ]]; then
  "$ROOT/scripts/verify-codex-plugin.sh" --scope user --project-root "$PROJECT_ROOT" --user-home "$USER_HOME"
fi
