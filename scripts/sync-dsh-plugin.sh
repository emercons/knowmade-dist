#!/usr/bin/env bash
# Regenerate the DeepSeek Harness (DSH) skill mirror, plugins/llm-wiki-dsh/,
# from the canonical Knowmade source. The mirror is one self-contained
# filesystem skill, skills/knowmade/, that scripts/install-deepseek-harness.sh
# copies to ${DSH_HOME:-~/.dsh}/skills/knowmade. Everything the skill needs sits
# inside it: the references, the deterministic launchers and the
# knowmade_control package, in skills/knowmade/bin/.
#
# KNOWMADE_SYNC_OUT=<dir> redirects every generated file under <dir> using the
# same repository-relative paths (<dir>/plugins/llm-wiki-dsh/...). Sources are
# always read from this checkout. Unset, the mirror is regenerated in place; the
# mirror test uses the override to compare a scratch regeneration against the
# working tree without touching it.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -n "${KNOWMADE_SYNC_OUT:-}" ]; then
  mkdir -p "$KNOWMADE_SYNC_OUT"
  OUT="$(cd "$KNOWMADE_SYNC_OUT" && pwd)"
else
  OUT="$ROOT"
fi
SOURCE_SKILL="$ROOT/claude-plugin/skills/knowmade"
CLAUDE_MANIFEST="$ROOT/claude-plugin/.claude-plugin/plugin.json"
CONTROL_PACKAGE="$ROOT/scripts/knowmade_control"
BUILD_IDENTITY="$ROOT/scripts/.sync-build-identity.sh"
TARGET_PLUGIN="$OUT/plugins/llm-wiki-dsh"
TARGET_SKILL="$TARGET_PLUGIN/skills/knowmade"
LAUNCHERS="llm-wiki llm-wiki-session llm-wiki-adopt llm-wiki-adopt-batch"

# Prerequisites first: nothing in the target tree is touched until every source
# is known to exist.
if [ ! -f "$SOURCE_SKILL/SKILL.md" ]; then
  echo "Missing source skill: $SOURCE_SKILL" >&2
  exit 1
fi
if [ ! -f "$CLAUDE_MANIFEST" ]; then
  echo "Missing Claude manifest: $CLAUDE_MANIFEST" >&2
  exit 1
fi
for helper in $LAUNCHERS; do
  if [ ! -f "$ROOT/scripts/$helper" ]; then
    echo "Missing local helper: $ROOT/scripts/$helper" >&2
    exit 1
  fi
done
if [ ! -d "$CONTROL_PACKAGE" ]; then
  echo "Missing deterministic control-plane package: $CONTROL_PACKAGE" >&2
  exit 1
fi
if [ ! -x "$BUILD_IDENTITY" ]; then
  echo "Missing build-identity generator: $BUILD_IDENTITY" >&2
  exit 1
fi

# Stamp the shared sources exactly as the other mirror syncs do, so the copies
# below cannot drift from them. A scratch regeneration must not touch the
# checkout's own sources.
if [ -z "${KNOWMADE_SYNC_OUT:-}" ]; then
  python3 -B "$ROOT/scripts/sync-command-prelude.py"
  python3 -B "$ROOT/scripts/sync-session-vendor.py"
fi

rm -rf "$TARGET_SKILL"
mkdir -p "$TARGET_SKILL"
cp -R "$SOURCE_SKILL/." "$TARGET_SKILL/"

mkdir -p "$TARGET_SKILL/bin"
for helper in $LAUNCHERS; do
  cp "$ROOT/scripts/$helper" "$TARGET_SKILL/bin/$helper"
  chmod 0755 "$TARGET_SKILL/bin/$helper"
done
cp -R "$CONTROL_PACKAGE" "$TARGET_SKILL/bin/knowmade_control"
# Keep the generated package source-only; ignored bytecode can make a later
# Windows sync fail while it clears the copied cache.
find "$TARGET_SKILL/bin/knowmade_control" -type d -name __pycache__ -prune -exec rm -rf {} +
"$BUILD_IDENTITY" "$TARGET_SKILL/bin/knowmade_control/_build.py"

python3 - "$TARGET_SKILL/SKILL.md" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
start = text.find("---\n")
end = text.find("\n---\n", start + 4)
if start != 0 or end == -1:
    raise SystemExit(f"Unexpected frontmatter in {path}")

frontmatter = """---
name: knowmade
description: >
  Manage Knowmade practical knowledge bases in DeepSeek Harness: dispatch hub
  intake, ingest through topic inboxes, track private goals, inventory and
  datasets, archives, compile/query/lint/audit, research/plan, sessions,
  feedback, private adapters, personal specialists, and outputs. Use for wiki
  workflows, knowledge-base management, source ingestion, compilation,
  provenance, audits, and /knowmade-style requests.
---
"""
text = frontmatter + text[end + 5 :]

replacements = [
    (
        "`raw/` and incrementally compiled into interconnected markdown articles. The\nactive agent is both compiler and query engine; Obsidian is an optional viewer.\n",
        "`raw/` and incrementally compiled into interconnected markdown articles.\nDeepSeek Harness is both compiler and query engine; Obsidian is an optional viewer.\n\n## DeepSeek Harness Notes\n\nThis directory is a native filesystem skill bundle. Invoke it as `/knowmade`, or let the harness load it from a matching request. Treat `/knowmade:*` examples and Codex-style `@knowmade` invocations as workflow shorthand under the loaded `/knowmade` skill. The deterministic helper is bundled at `bin/llm-wiki` inside this skill (beside `bin/knowmade_control/`); run it by that path instead of assuming a global executable.\n",
    ),
    (
        "When this skill activates outside an explicit wiki invocation:",
        "When this skill activates outside of an explicit `/knowmade` invocation:",
    ),
    (
        '4. If no relevant content → answer normally, optionally suggest: "This could be added to your wiki with `/knowmade:ingest`"',
        '4. If no relevant content → answer normally, optionally suggest: "This could be added to your wiki; invoke `/knowmade` and ask to ingest it."',
    ),
]
for old, new in replacements:
    if old not in text:
        raise SystemExit(f"Expected text not found in {path}: {old[:80]!r}")
    text = text.replace(old, new)

with path.open("w", encoding="utf-8", newline="\n") as handle:
    handle.write(text)
PY

cat > "$TARGET_PLUGIN/README.md" <<'EOF'
# Knowmade for DeepSeek Harness

This generated package provides the `/knowmade` filesystem skill for DeepSeek
Harness. Install it with `scripts/install-deepseek-harness.sh`; the installer
copies the self-contained bundle to `${DSH_HOME:-~/.dsh}/skills/knowmade`.

`skills/knowmade/` holds `SKILL.md`, a real copy of the references and the
deterministic helper in `bin/` (`llm-wiki`, `llm-wiki-session`, the adoption
launchers and the `knowmade_control` package). Nothing outside that directory
is needed at run time.

DeepSeek Harness is still a developer preview. This integration deliberately
uses only its documented filesystem-skill contract and does not install a
custom runtime plugin. If session hooks are desired, use the harness's official
Codex-hook compatibility bridge separately.

Do not edit the files here by hand; run `scripts/sync-dsh-plugin.sh`.
EOF

echo "Synced DeepSeek Harness skill from canonical Knowmade source."
echo "Source: $SOURCE_SKILL"
echo "Target: $TARGET_SKILL"
