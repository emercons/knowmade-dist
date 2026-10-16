# shellcheck shell=bash
# Shared Codex configuration helpers for scripts/bootstrap-codex-plugin.sh and
# scripts/verify-codex-plugin.sh (knowmade#45, #67 R2.4).
#
# Source it after resolving ROOT:
#
#     . "$ROOT/scripts/lib/codex-config.sh"
#
# It is a library, not an entry point: tracked 100644, never executed directly
# (tests/test-executable-modes.sh pins the mode). It sets no shell options and
# only defines the names below.
#
# Shell:
#   MARKETPLACE_NAME, PLUGIN_NAME, PLUGIN_KEY
#                                  the one Codex marketplace/plugin identity
#   codex_home_for <user-home>     CODEX_HOME value for that home, native form
#   codex_in_home <user-home> ...  run `codex ...` with HOME and CODEX_HOME set
#   codex_python [args] <<'PY'     run a Python program with the helpers below
#                                  defined first (sys.argv[1:] are the args)
#   marketplace_source <config.toml> <name>
#                                  print the configured source of marketplace
#                                  <name>, or nothing when it is not registered
#   same_codex_path <a> <b>        succeed when both paths name the same place
#   other_enabled_plugins <config.toml>
#                                  print every enabled plugin key that shares
#                                  PLUGIN_NAME but is not PLUGIN_KEY, one a line
#
# Python (available inside codex_python programs):
#   marketplace_source(config, name) -> str
#   other_enabled_plugins(config, plugin_key) -> list[str]
#   normalise_path(text) -> pathlib.Path
#   same_path(actual, expected) -> bool
#   strip_extended_prefix(text) -> str

if [ -n "${KNOWMADE_CODEX_CONFIG_LOADED:-}" ]; then
  return 0
fi
KNOWMADE_CODEX_CONFIG_LOADED=1

MARKETPLACE_NAME="knowmade"
PLUGIN_NAME="wiki"
PLUGIN_KEY="${PLUGIN_NAME}@${MARKETPLACE_NAME}"

# Codex resolves its own home from CODEX_HOME, falling back to the OS home - and
# on Windows that fallback is USERPROFILE, not HOME. Setting HOME alone therefore
# left every sandboxed invocation writing into the real user profile: the runtime
# test installed a marketplace and a plugin into the developer's own Codex
# config instead of its scratch directory. Export CODEX_HOME too, in the form the
# native binary can use.
codex_home_for() {
  local home="$1/.codex"
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$home"
  else
    printf '%s' "$home"
  fi
}

# codex_in_home <user-home> <codex arguments...>: every Codex call the helpers
# make goes through here, so the scratch home of a test is the only home Codex
# ever sees.
codex_in_home() {
  local user_home="$1"
  shift
  HOME="$user_home" CODEX_HOME="$(codex_home_for "$user_home")" codex "$@"
}

# The one Python definition of how Knowmade reads Codex's config.toml.
#
# Codex records a marketplace source in whichever TOML string form fits the
# path: a POSIX path lands in a double-quoted basic string, a Windows path
# (which contains backslashes) in a single-quoted literal string, and on
# Windows the path itself is the extended-length spelling (\\?\C:\...). A
# reader that accepts only one quote style, or compares the raw text, reports a
# correct install as a mismatch - that is how a second bootstrap on Windows
# either re-added the marketplace or refused with "already points at another
# checkout" (knowmade#45). The config is read as UTF-8 explicitly because
# Python's locale default on Windows is a legacy code page that cannot decode
# a non-ASCII path. tomllib (Python 3.11+) does the parsing; the regex is the
# fallback for older interpreters and for a config tomllib rejects.
CODEX_CONFIG_PY="$(cat <<'PY'
import re
from pathlib import Path

_UNC_PREFIX = "\\\\?\\UNC\\"
_DRIVE_PREFIX = "\\\\?\\"


def strip_extended_prefix(text):
    """Drop the Windows extended-length prefix from a path spelling."""
    text = str(text)
    if text.startswith(_UNC_PREFIX):
        return "\\\\" + text[len(_UNC_PREFIX):]
    if text.startswith(_DRIVE_PREFIX):
        return text[len(_DRIVE_PREFIX):]
    return text


def normalise_path(text):
    """Resolve a path by identity, not by spelling."""
    return Path(strip_extended_prefix(text)).expanduser().resolve()


def same_path(actual, expected):
    if not actual or not expected:
        return False
    return normalise_path(actual) == normalise_path(expected)


def _unescape_basic(value):
    return re.sub(r'\\(["\\])', r"\1", value)


def _marketplace_source_regex(text, name):
    table = re.search(
        r"(?ms)^\[marketplaces\." + re.escape(name) + r"\][ \t]*\n(.*?)(?=^\[|\Z)",
        text,
    )
    if not table:
        return ""
    match = re.search(
        r"""(?m)^source[ \t]*=[ \t]*(?:"((?:[^"\\]|\\.)*)"|'([^']*)')[ \t]*(?:#.*)?$""",
        table.group(1),
    )
    if not match:
        return ""
    if match.group(1) is not None:
        return _unescape_basic(match.group(1))
    return match.group(2)


def marketplace_source(config, name):
    """Return the configured source of marketplace `name` in `config`, or "".

    Both TOML quote styles are accepted and the Windows extended-length prefix
    is stripped, so the result can be printed or compared with same_path().
    """
    config = Path(config)
    if not config.is_file():
        return ""
    text = config.read_text(encoding="utf-8")
    source = None
    try:
        import tomllib

        marketplaces = tomllib.loads(text).get("marketplaces") or {}
        entry = marketplaces.get(name)
        if isinstance(entry, dict):
            source = entry.get("source")
    except (ImportError, ValueError):
        source = None
    if source is None:
        source = _marketplace_source_regex(text, name)
    if not isinstance(source, str):
        return ""
    return strip_extended_prefix(source)


def other_enabled_plugins(config, plugin_key):
    """Return the enabled plugin keys named like plugin_key from another marketplace.

    Codex derives the skill id from the plugin name alone, so `wiki@llm-wiki`
    (upstream) and `wiki@knowmade` both expose `wiki:wiki` and `@wiki` becomes
    ambiguous (knowmade#124). A plugin table without `enabled` counts as enabled.
    """
    config = Path(config)
    if not config.is_file():
        return []
    name = plugin_key.split("@", 1)[0]
    text = config.read_text(encoding="utf-8")
    try:
        import tomllib

        plugins = tomllib.loads(text).get("plugins") or {}
        tables = {
            key: value.get("enabled", True)
            for key, value in plugins.items()
            if isinstance(value, dict)
        }
    except (ImportError, ValueError):
        tables = {}
        for match in re.finditer(
            r'(?ms)^\[plugins\."([^"]+)"\][ \t]*\n(.*?)(?=^\[|\Z)', text
        ):
            enabled = re.search(r"(?m)^enabled[ \t]*=[ \t]*(true|false)\b", match.group(2))
            tables[match.group(1)] = not enabled or enabled.group(1) == "true"
    return sorted(
        key
        for key, enabled in tables.items()
        if enabled is not False and key != plugin_key and key.split("@", 1)[0] == name
    )
PY
)"

# codex_python [args...] <<'PY' ... PY: run the program on stdin with the
# helpers above defined first; the arguments arrive as sys.argv[1:]. PYTHONUTF8
# keeps a non-ASCII path printable through a pipe on Windows, where Python's
# console encoding would otherwise be a legacy code page.
codex_python() {
  { printf '%s\n' "$CODEX_CONFIG_PY"; cat; } | PYTHONUTF8=1 python3 - "$@"
}

marketplace_source() {
  codex_python "$1" "$2" <<'PY'
import sys

print(marketplace_source(sys.argv[1], sys.argv[2]))
PY
}

other_enabled_plugins() {
  codex_python "$1" "$PLUGIN_KEY" <<'PY'
import sys

for key in other_enabled_plugins(sys.argv[1], sys.argv[2]):
    print(key)
PY
}

same_codex_path() {
  codex_python "$1" "$2" <<'PY'
import sys

raise SystemExit(0 if same_path(sys.argv[1], sys.argv[2]) else 1)
PY
}
