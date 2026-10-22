#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/lib/codex-config.sh
. "$ROOT/scripts/lib/codex-config.sh"
SCOPE="user"
PROJECT_ROOT="${PWD}"
USER_HOME="${HOME}"

usage() {
  cat <<'EOF'
Usage: ./scripts/verify-codex-plugin.sh [options]

Verify that Codex resolves the @knowmade plugin and installs the explicit-only
$knowmade-query skill from this repo.

Options:
  --scope user           Verify the supported user install (default: user)
  --project-root <dir>   Working directory for the prompt probe (default: current dir)
  --user-home <dir>      HOME used for Codex config lookup (default: current HOME)
  -h, --help             Show this help
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
    echo "Verify the user-scoped install with --scope user." >&2
    exit 1
    ;;
  *)
    echo "Invalid scope: $SCOPE (expected user)" >&2
    exit 1
    ;;
esac

PROJECT_ROOT="$(cd "$PROJECT_ROOT" && pwd)"
USER_HOME="$(cd "$USER_HOME" && pwd)"
SOURCE_PLUGIN_ROOT="$ROOT/plugins/llm-wiki"
# The skill directories carry the plugin's skill names (knowmade#168).
SKILL_NAME="$PLUGIN_NAME"
QUERY_SKILL_NAME="${PLUGIN_NAME}-query"
SOURCE_SKILL_PATH="$SOURCE_PLUGIN_ROOT/skills/$SKILL_NAME/SKILL.md"
SOURCE_QUERY_SKILL_PATH="$SOURCE_PLUGIN_ROOT/skills/$QUERY_SKILL_NAME/SKILL.md"
EXPECTED_VERSION="$(python3 -I -X utf8 -c 'import json, sys; print(json.load(open(sys.argv[1]))["version"])' "$SOURCE_PLUGIN_ROOT/.codex-plugin/plugin.json")"
TMP_OUTPUT="$(mktemp)"
TMP_LIST="$(mktemp)"
USER_CONFIG="$USER_HOME/.codex/config.toml"
TARGET_CONFIG="$USER_CONFIG"
PLUGIN_CACHE_ENTRY="$USER_HOME/.codex/plugins/cache/$MARKETPLACE_NAME/$PLUGIN_NAME"
cleanup() {
  rm -f "$TMP_OUTPUT" "$TMP_LIST"
}
trap cleanup EXIT

if [[ ! -f "$USER_CONFIG" ]]; then
  echo "Missing user Codex config:" >&2
  echo "  $USER_CONFIG" >&2
  echo "Run ./scripts/bootstrap-codex-plugin.sh first." >&2
  exit 1
fi

if [[ ! -f "$TARGET_CONFIG" ]]; then
  echo "Missing Codex config for scope '$SCOPE':" >&2
  echo "  $TARGET_CONFIG" >&2
  echo "Run ./scripts/bootstrap-codex-plugin.sh --scope $SCOPE first." >&2
  exit 1
fi

if ! grep -Fq "[plugins.\"$PLUGIN_KEY\"]" "$TARGET_CONFIG"; then
  echo "Missing plugin enable block in:" >&2
  echo "  $TARGET_CONFIG" >&2
  echo "Run ./scripts/bootstrap-codex-plugin.sh --scope $SCOPE first." >&2
  exit 1
fi

PLUGIN_LIST_HELP="$(codex_in_home "$USER_HOME" plugin list --help 2>&1)"
if grep -q -- '--json' <<<"$PLUGIN_LIST_HELP"; then
  codex_in_home "$USER_HOME" -C "$PROJECT_ROOT" plugin list --marketplace "$MARKETPLACE_NAME" --json >"$TMP_LIST"
  LIST_FORMAT="json"
else
  codex_in_home "$USER_HOME" -C "$PROJECT_ROOT" plugin list --marketplace "$MARKETPLACE_NAME" >"$TMP_LIST"
  LIST_FORMAT="text"
fi
codex_in_home "$USER_HOME" -C "$PROJECT_ROOT" debug prompt-input "@$PLUGIN_NAME test" >"$TMP_OUTPUT"

if [[ "$LIST_FORMAT" == "json" ]]; then
  # Codex records Windows paths in extended-length form; same_path (from the
  # shared library) compares identities, not spellings.
  INSTALLED_VERSION="$(codex_python "$TMP_LIST" "$PLUGIN_KEY" "$EXPECTED_VERSION" "$SOURCE_PLUGIN_ROOT" "$ROOT" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
plugin_key, expected_version, source_root, marketplace_root = sys.argv[2:]
plugin = next((item for item in data.get("installed", []) if item.get("pluginId") == plugin_key), None)
if plugin is None:
    raise SystemExit(f"FAIL: {plugin_key} is not installed")

errors = []
if not plugin.get("installed"):
    errors.append("plugin is not installed")
if not plugin.get("enabled"):
    errors.append("plugin is not enabled in the selected scope")
if plugin.get("version") != expected_version:
    errors.append(f"installed version {plugin.get('version')!r} != expected {expected_version!r}")

source = plugin.get("source") or {}
if source.get("source") != "local" or not same_path(source.get("path"), source_root):
    errors.append(f"plugin source does not point at {source_root}")
marketplace = plugin.get("marketplaceSource") or {}
if marketplace.get("sourceType") != "local" or not same_path(marketplace.get("source"), marketplace_root):
    errors.append(f"marketplace source does not point at {marketplace_root}")

if errors:
    raise SystemExit("FAIL: " + "; ".join(errors))
print(plugin["version"])
PY
)"
else
  if ! grep -Fq "$PLUGIN_KEY" "$TMP_LIST" || ! grep -Fq '(installed, enabled)' "$TMP_LIST"; then
    echo "FAIL: Codex plugin list does not show $PLUGIN_KEY as installed and enabled:" >&2
    cat "$TMP_LIST" >&2
    exit 1
  fi
  INSTALLED_VERSION="$EXPECTED_VERSION"
fi

# The registered marketplace source must be this checkout. marketplace_source
# accepts both TOML quote styles and the extended-length Windows spelling.
MARKETPLACE_SOURCE="$(marketplace_source "$USER_CONFIG" "$MARKETPLACE_NAME")"
if [[ -z "$MARKETPLACE_SOURCE" ]] || ! same_codex_path "$MARKETPLACE_SOURCE" "$ROOT"; then
  echo "FAIL: marketplace source does not point at $ROOT" >&2
  echo "Configured source: ${MARKETPLACE_SOURCE:-<none>}" >&2
  exit 1
fi

EXPECTED_CACHE_SKILL="$PLUGIN_CACHE_ENTRY/$INSTALLED_VERSION/skills/$SKILL_NAME/SKILL.md"
EXPECTED_CACHE_QUERY_SKILL="$PLUGIN_CACHE_ENTRY/$INSTALLED_VERSION/skills/$QUERY_SKILL_NAME/SKILL.md"
if [[ ! -f "$EXPECTED_CACHE_SKILL" ]]; then
  echo "FAIL: installed Codex cache is missing the $SKILL_NAME skill:" >&2
  echo "  $EXPECTED_CACHE_SKILL" >&2
  exit 1
fi
if [[ ! -f "$EXPECTED_CACHE_QUERY_SKILL" ]]; then
  echo "FAIL: installed Codex cache is missing the $QUERY_SKILL_NAME skill:" >&2
  echo "  $EXPECTED_CACHE_QUERY_SKILL" >&2
  exit 1
fi

# Every knowmade:knowmade skill Codex lists, one physical path a line. Another
# enabled plugin of the same name adds its own line; the check below looks for
# this checkout's install among them instead of trusting whichever Codex
# happens to list last (knowmade#124).
ACTUAL_SKILL_PATHS="$(python3 -I -X utf8 - "$TMP_OUTPUT" "$PLUGIN_NAME" "$SKILL_NAME" <<'PY'
import json
import re
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
plugin, skill = sys.argv[2], sys.argv[3]

def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)

skill_roots = {}
skill_paths = []
for text in strings(data):
    for line in text.splitlines():
        root_match = re.match(r"^- `(r\d+)` = `(.+)`$", line.strip())
        if root_match:
            skill_roots[root_match.group(1)] = root_match.group(2)
        if f"- {plugin}:{skill}:" in line:
            match = re.search(rf"\(file: ([^)]+/skills/{re.escape(skill)}/SKILL\.md)\)", line)
            if match and match.group(1) not in skill_paths:
                skill_paths.append(match.group(1))

for skill_path in skill_paths:
    logical_root, separator, remainder = skill_path.partition("/")
    if separator and logical_root in skill_roots:
        skill_path = str(Path(skill_roots[logical_root]) / remainder)
    print(skill_path)
PY
)"

if [[ -z "$ACTUAL_SKILL_PATHS" ]]; then
  echo "FAIL: Codex did not expose $PLUGIN_NAME:$SKILL_NAME in the headless prompt." >&2
  echo "Run ./scripts/bootstrap-codex-plugin.sh --scope $SCOPE again." >&2
  exit 1
fi

# Prints the paths that are not this install; exits 1 when none of them is.
if ! OTHER_SKILL_PATHS="$(codex_python "$EXPECTED_CACHE_SKILL" "$SOURCE_SKILL_PATH" "$INSTALLED_VERSION" "$MARKETPLACE_NAME" "$ACTUAL_SKILL_PATHS" "$PLUGIN_NAME" "$SKILL_NAME" <<'PY'
import re
import sys
from pathlib import Path

expected = {normalise_path(path) for path in sys.argv[1:3]}
version, marketplace = sys.argv[3], sys.argv[4]
plugin, skill = sys.argv[6], sys.argv[7]


def ours(path):
    if normalise_path(path) in expected:
        return True
    # Codex desktop may expose enabled plugin skills through an rN logical
    # mount instead of their physical cache path. The cache/config/content
    # checks above still prove the clean install; accept only the exact
    # marketplace/plugin/version suffix, so a same-named plugin under another
    # marketplace's cache never counts as this install.
    return bool(re.search(
        rf"(?:^r\d+|/{re.escape(marketplace)})/{re.escape(plugin)}/{re.escape(version)}"
        rf"/skills/{re.escape(skill)}/SKILL\.md$",
        Path(path).as_posix(),
    ))


paths = [line for line in sys.argv[5].splitlines() if line]
for path in paths:
    if not ours(path):
        print(path)
raise SystemExit(0 if any(ours(path) for path in paths) else 1)
PY
)"; then
  echo "FAIL: Codex did not resolve $PLUGIN_NAME:$SKILL_NAME from this install." >&2
  echo "Resolved skill path(s):" >&2
  sed 's/^/  /' <<<"$ACTUAL_SKILL_PATHS" >&2
  echo "Expected installed cache path:" >&2
  echo "  $EXPECTED_CACHE_SKILL" >&2
  exit 1
fi
if [[ -n "$OTHER_SKILL_PATHS" ]]; then
  echo "WARNING: another enabled plugin also answers to $PLUGIN_NAME:$SKILL_NAME, so @$PLUGIN_NAME is ambiguous:" >&2
  sed 's/^/  /' <<<"$OTHER_SKILL_PATHS" >&2
  echo "Remove or disable it (README.md, Install: OpenAI Codex)." >&2
fi

# The plugin's name up to v0.7.0.25.1 (knowmade#168). The bootstrap removes it;
# one that is still enabled keeps answering to @wiki and records every session
# event a second time.
for legacy_key in $LEGACY_PLUGIN_KEYS; do
  if [[ "$(plugin_state "$USER_CONFIG" "$legacy_key")" == enabled ]]; then
    echo "WARNING: $legacy_key, this plugin's name up to v0.7.0.25.1, is still enabled." >&2
    echo "Run codex plugin remove $legacy_key (or rerun ./scripts/bootstrap-codex-plugin.sh)." >&2
  fi
done

if ! python3 -I -X utf8 - "$EXPECTED_CACHE_QUERY_SKILL" "$SOURCE_QUERY_SKILL_PATH" <<'PY'
import sys
from pathlib import Path

cached, source = (Path(path).read_text(encoding="utf-8") for path in sys.argv[1:])
raise SystemExit(0 if cached == source else 1)
PY
then
  echo "FAIL: cached $QUERY_SKILL_NAME skill differs from the generated source:" >&2
  echo "  $EXPECTED_CACHE_QUERY_SKILL" >&2
  exit 1
fi

if grep -Fq "/skills/$QUERY_SKILL_NAME/SKILL.md" "$TMP_OUTPUT"; then
  echo "FAIL: explicit-only $QUERY_SKILL_NAME leaked into the implicit skill list." >&2
  exit 1
fi

echo "OK: Codex resolves @$PLUGIN_NAME and installs explicit-only \$$QUERY_SKILL_NAME from $PLUGIN_KEY version $INSTALLED_VERSION."
echo "Skill paths:"
echo "  $EXPECTED_CACHE_SKILL"
echo "  $EXPECTED_CACHE_QUERY_SKILL"
