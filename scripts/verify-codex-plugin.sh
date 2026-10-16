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

Verify that Codex resolves the @wiki plugin and installs the explicit-only
$wiki-query skill from this repo.

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
SOURCE_SKILL_PATH="$SOURCE_PLUGIN_ROOT/skills/wiki/SKILL.md"
SOURCE_QUERY_SKILL_PATH="$SOURCE_PLUGIN_ROOT/skills/wiki-query/SKILL.md"
EXPECTED_VERSION="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["version"])' "$SOURCE_PLUGIN_ROOT/.codex-plugin/plugin.json")"
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
codex_in_home "$USER_HOME" -C "$PROJECT_ROOT" debug prompt-input '@wiki test' >"$TMP_OUTPUT"

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

EXPECTED_CACHE_SKILL="$PLUGIN_CACHE_ENTRY/$INSTALLED_VERSION/skills/wiki/SKILL.md"
EXPECTED_CACHE_QUERY_SKILL="$PLUGIN_CACHE_ENTRY/$INSTALLED_VERSION/skills/wiki-query/SKILL.md"
if [[ ! -f "$EXPECTED_CACHE_SKILL" ]]; then
  echo "FAIL: installed Codex cache is missing the wiki skill:" >&2
  echo "  $EXPECTED_CACHE_SKILL" >&2
  exit 1
fi
if [[ ! -f "$EXPECTED_CACHE_QUERY_SKILL" ]]; then
  echo "FAIL: installed Codex cache is missing the wiki-query skill:" >&2
  echo "  $EXPECTED_CACHE_QUERY_SKILL" >&2
  exit 1
fi

# Every wiki:wiki skill Codex lists, one physical path a line. Another enabled
# plugin named wiki (upstream wiki@llm-wiki) adds its own line; the check below
# looks for this checkout's install among them instead of trusting whichever
# Codex happens to list last (knowmade#124).
ACTUAL_SKILL_PATHS="$(python3 - "$TMP_OUTPUT" <<'PY'
import json
import re
import sys
from pathlib import Path

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))

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
        if "- wiki:wiki:" in line:
            match = re.search(r"\(file: ([^)]+/skills/wiki/SKILL\.md)\)", line)
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
  echo "FAIL: Codex did not expose wiki:wiki in the headless prompt." >&2
  echo "Run ./scripts/bootstrap-codex-plugin.sh --scope $SCOPE again." >&2
  exit 1
fi

# Prints the paths that are not this install; exits 1 when none of them is.
if ! OTHER_SKILL_PATHS="$(codex_python "$EXPECTED_CACHE_SKILL" "$SOURCE_SKILL_PATH" "$INSTALLED_VERSION" "$MARKETPLACE_NAME" "$ACTUAL_SKILL_PATHS" <<'PY'
import re
import sys
from pathlib import Path

expected = {normalise_path(path) for path in sys.argv[1:3]}
version, marketplace = sys.argv[3], sys.argv[4]


def ours(path):
    if normalise_path(path) in expected:
        return True
    # Codex desktop may expose enabled plugin skills through an rN logical
    # mount instead of their physical cache path. The cache/config/content
    # checks above still prove the clean install; accept only the exact
    # marketplace/plugin/version suffix, so upstream's wiki 0.25.1 under
    # cache/llm-wiki never counts as this install.
    return bool(re.search(
        rf"(?:^r\d+|/{re.escape(marketplace)})/wiki/{re.escape(version)}/skills/wiki/SKILL\.md$",
        Path(path).as_posix(),
    ))


paths = [line for line in sys.argv[5].splitlines() if line]
for path in paths:
    if not ours(path):
        print(path)
raise SystemExit(0 if any(ours(path) for path in paths) else 1)
PY
)"; then
  echo "FAIL: Codex did not resolve wiki:wiki from this install." >&2
  echo "Resolved skill path(s):" >&2
  sed 's/^/  /' <<<"$ACTUAL_SKILL_PATHS" >&2
  echo "Expected installed cache path:" >&2
  echo "  $EXPECTED_CACHE_SKILL" >&2
  exit 1
fi
if [[ -n "$OTHER_SKILL_PATHS" ]]; then
  echo "WARNING: another enabled plugin also answers to wiki:wiki, so @wiki is ambiguous:" >&2
  sed 's/^/  /' <<<"$OTHER_SKILL_PATHS" >&2
  echo "Remove or disable it (README.md, Install: OpenAI Codex)." >&2
fi

if ! python3 - "$EXPECTED_CACHE_QUERY_SKILL" "$SOURCE_QUERY_SKILL_PATH" <<'PY'
import sys
from pathlib import Path

cached, source = (Path(path).read_text(encoding="utf-8") for path in sys.argv[1:])
raise SystemExit(0 if cached == source else 1)
PY
then
  echo "FAIL: cached wiki-query skill differs from the generated source:" >&2
  echo "  $EXPECTED_CACHE_QUERY_SKILL" >&2
  exit 1
fi

if grep -Fq '/skills/wiki-query/SKILL.md' "$TMP_OUTPUT"; then
  echo "FAIL: explicit-only wiki-query leaked into the implicit skill list." >&2
  exit 1
fi

echo "OK: Codex resolves @wiki and installs explicit-only \$wiki-query from $PLUGIN_KEY version $INSTALLED_VERSION."
echo "Skill paths:"
echo "  $EXPECTED_CACHE_SKILL"
echo "  $EXPECTED_CACHE_QUERY_SKILL"
