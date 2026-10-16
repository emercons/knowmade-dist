#!/usr/bin/env bash
# Emit the generated build-identity module knowmade_control/_build.py.
#
# Called by scripts/sync-codex-plugin.sh and scripts/sync-opencode-plugin.sh
# with one argument per destination file. Every destination receives the same
# content, computed once from the canonical sources of this checkout:
#
#   VERSION      claude-plugin/.claude-plugin/plugin.json "version"
#   COMMIT       git rev-parse --short HEAD, or "unknown" without git/.git
#   RULE_SET     knowmade_control.rules.CURRENT_RULE_SET (never hardcoded here)
#   GENERATED_BY the sync entry points that own the file
#
# COMMIT is the only non-deterministic line; the mirror-sync tests filter it
# out when comparing regenerated output with the working tree, so a routine
# commit does not dirty the mirrors. See tests/README.md and
# .claude/release-checklist.md.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLAUDE_MANIFEST="$ROOT/claude-plugin/.claude-plugin/plugin.json"
CONTROL_PACKAGE="$ROOT/scripts/knowmade_control"

if [ "$#" -eq 0 ]; then
  echo "usage: $0 <destination _build.py>..." >&2
  exit 2
fi
if [ ! -f "$CLAUDE_MANIFEST" ]; then
  echo "Missing Claude manifest: $CLAUDE_MANIFEST" >&2
  exit 1
fi
if [ ! -f "$CONTROL_PACKAGE/rules.py" ]; then
  echo "Missing rule-set module: $CONTROL_PACKAGE/rules.py" >&2
  exit 1
fi

commit="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || true)"
case "$commit" in
  *[!0-9a-f]* | "") commit="unknown" ;;
esac

# -B: never leave bytecode in the canonical package while generating.
python3 -B - "$CLAUDE_MANIFEST" "$CONTROL_PACKAGE" "$commit" "$@" <<'PY'
import json
import re
import sys
from pathlib import Path

manifest, package, commit, *destinations = sys.argv[1:]
version = json.loads(Path(manifest).read_text(encoding="utf-8"))["version"]
if not isinstance(version, str) or not re.fullmatch(r"[0-9][0-9A-Za-z.+-]*", version):
    raise SystemExit(f"Refusing to embed an unsafe plugin version: {version!r}")

sys.path.insert(0, str(Path(package).parent))
from knowmade_control import rules  # noqa: E402

rule_set = int(rules.CURRENT_RULE_SET)

content = f'''"""Generated build identity of this Knowmade control-plane copy.

GENERATED FILE: do not edit by hand. scripts/sync-codex-plugin.sh and
scripts/sync-opencode-plugin.sh rewrite it, through
scripts/.sync-build-identity.sh, into scripts/knowmade_control/ and into every
packaged copy under claude-plugin/bin/ and plugins/*/bin/.

engine.py prefers VERSION and RULE_SET from this module and falls back to the
upward plugin-manifest search only when the module is missing. COMMIT is used
by packaged runtimes, which have no git checkout of their own; in a source
checkout git still reports the live commit. COMMIT is informational: it is the
short hash of HEAD when the sync ran, so in a release commit it names the
commit before the release commit. The authoritative release identity is
VERSION plus the release tag.

The mirror-sync tests compare this file with the COMMIT line filtered out, so
everything except COMMIT is deterministic for a given checkout.
"""

VERSION = "{version}"
COMMIT = "{commit}"
RULE_SET = {rule_set}
GENERATED_BY = "scripts/sync-codex-plugin.sh, scripts/sync-opencode-plugin.sh"
'''

for destination in destinations:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8"))
PY
