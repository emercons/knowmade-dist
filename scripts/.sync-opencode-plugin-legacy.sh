#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# KNOWMADE_SYNC_OUT=<dir> redirects generated output under <dir> with the same
# repository-relative paths; sources are always read from the checkout. See
# the header of scripts/sync-opencode-plugin.sh.
if [ -n "${KNOWMADE_SYNC_OUT:-}" ]; then
  mkdir -p "$KNOWMADE_SYNC_OUT"
  OUT="$(cd "$KNOWMADE_SYNC_OUT" && pwd)"
else
  OUT="$ROOT"
fi
SOURCE_SKILL="$ROOT/claude-plugin/skills/knowmade"
QUERY_SOURCE="$SOURCE_SKILL/references/query-lite.md"
TARGET_PLUGIN_REL="plugins/llm-wiki-opencode"
TARGET_PLUGIN="$OUT/$TARGET_PLUGIN_REL"
# The skills are named like the Codex mirror's: knowmade and knowmade-query
# (emercons/knowmade#182). Releases up to v0.8.0.25.1 wrote skills/wiki-manager
# and skills/wiki-query; those directories are removed below so an in-place
# sync leaves no stale skill.
TARGET_SKILL="$TARGET_PLUGIN/skills/knowmade"
TARGET_QUERY="$TARGET_PLUGIN/skills/knowmade-query"
LEGACY_SKILLS="wiki-manager wiki-query"
CLAUDE_BIN="$OUT/claude-plugin/bin"
CLAUDE_MANIFEST="$ROOT/claude-plugin/.claude-plugin/plugin.json"
LOCAL_HELPER="$ROOT/scripts/llm-wiki"
SESSION_HELPER="$ROOT/scripts/llm-wiki-session"
ADOPT_HELPERS="llm-wiki-adopt llm-wiki-adopt-batch"

if [ ! -d "$SOURCE_SKILL" ]; then
  echo "Missing source skill: $SOURCE_SKILL" >&2
  exit 1
fi

if [ ! -f "$QUERY_SOURCE" ]; then
  echo "Missing query-lite source: $QUERY_SOURCE" >&2
  exit 1
fi

if [ ! -f "$CLAUDE_MANIFEST" ]; then
  echo "Missing Claude manifest: $CLAUDE_MANIFEST" >&2
  exit 1
fi

if [ ! -f "$LOCAL_HELPER" ]; then
  echo "Missing local helper: $LOCAL_HELPER" >&2
  exit 1
fi

if [ ! -f "$SESSION_HELPER" ]; then
  echo "Missing session helper: $SESSION_HELPER" >&2
  exit 1
fi

mkdir -p "$CLAUDE_BIN" "$TARGET_PLUGIN/bin"
cp "$LOCAL_HELPER" "$CLAUDE_BIN/llm-wiki"
cp "$LOCAL_HELPER" "$TARGET_PLUGIN/bin/llm-wiki"
chmod 0755 "$CLAUDE_BIN/llm-wiki" "$TARGET_PLUGIN/bin/llm-wiki"

# The session script ships as a plain command-driven helper (no hooks), the
# same copy the Claude plugin bundles (emercons/knowmade#50).
cp "$SESSION_HELPER" "$CLAUDE_BIN/llm-wiki-session"
cp "$SESSION_HELPER" "$TARGET_PLUGIN/bin/llm-wiki-session"
chmod 0755 "$CLAUDE_BIN/llm-wiki-session" "$TARGET_PLUGIN/bin/llm-wiki-session"

# The brownfield adoption helpers ship alongside the deterministic CLI and
# must not drift from it; the mirror sync tests fail if they do.
for helper in $ADOPT_HELPERS; do
  if [ ! -f "$ROOT/scripts/$helper" ]; then
    echo "Missing local helper: $ROOT/scripts/$helper" >&2
    exit 1
  fi
  cp "$ROOT/scripts/$helper" "$CLAUDE_BIN/$helper"
  cp "$ROOT/scripts/$helper" "$TARGET_PLUGIN/bin/$helper"
  chmod 0755 "$CLAUDE_BIN/$helper" "$TARGET_PLUGIN/bin/$helper"
done

mkdir -p "$TARGET_PLUGIN/skills"
# references/ is copied like every other entry (emercons/knowmade#125), exactly
# as the Codex mirror does. It used to be a tracked symlink into the canonical
# source, which a URL or archive fetch of SKILL.md alone never delivered and
# which git turns into a plain text file on Windows when core.symlinks is false.
# Real copies need no link support anywhere. The old target is removed first, and
# `rm -rf` on a symlink removes the link itself, never the tree behind it, so an
# upgraded checkout whose references entry is still the old link (or its
# placeholder file) is replaced rather than written through into the canonical
# source.
for legacy in $LEGACY_SKILLS; do
  rm -rf "${TARGET_PLUGIN:?}/skills/$legacy"
done
rm -rf "$TARGET_SKILL"
cp -R "$SOURCE_SKILL" "$TARGET_SKILL"

# Best-effort OpenCode query preset. It is instruction-only and shares the
# runtime-neutral query contract, but no provider-specific live model gate is
# claimed because OpenCode does not select a model on behalf of the user.
rm -rf "$TARGET_QUERY"
mkdir -p "$TARGET_QUERY"
cat > "$TARGET_QUERY/SKILL.md" <<'EOF'
---
name: knowmade-query
description: >
  Fast, read-only, index-first Knowmade queries for OpenCode and compatible
  instruction-file harnesses, with exact citations from the selected wiki.
---

EOF
cat "$QUERY_SOURCE" >> "$TARGET_QUERY/SKILL.md"

python3 - "$TARGET_SKILL" <<'PY'
import sys
from pathlib import Path

target_skill = Path(sys.argv[1])

skill_path = target_skill / "SKILL.md"
text = skill_path.read_text(encoding="utf-8")

frontmatter = """---
name: knowmade
description: >
  Manage Knowmade practical knowledge bases in OpenCode: dispatch hub intake,
  ingest/import through topic inboxes, shape/promote Ideas, review portfolios,
  track private goals, inventory/datasets, archives, compile/query/lint/audit,
  research/plan, manage sessions, feedback, private adapters, and personal
  specialists, and generate outputs.
  Activates when the user mentions wiki workflows, knowledge-base management,
  ingestion, collection ingestion, import wiki, collect, catalog, curate,
  find all, idea, turn idea into project, portfolio, business ideas, projects, inventory, source queue,
  candidate list, watch list, backlog, dataset, large data, data registry,
  dataset manifest, compilation, querying, linting, audit, research, librarian,
  scan quality, article quality, content review, output drift, provenance,
  archive wiki, archive topic, restore wiki, private adapter, adapter registry, skill-factory,
  checkpoints,
  personal specialist, specialist skill, specialist reviewer, expert lens,
  adapter route, adapter doctor, adapter run, read, review, or edit a hosted external resource such as a document,
  session capture, capture context, rehydrate,
  resume from session, lessons learned, implementation plan, or uses
  wiki-related shorthand in a repo with .wiki/, ~/wiki/, or a
  configured hub path.
---
"""

start = text.find("---\n")
end = text.find("\n---\n", start + 4)
if start != 0 or end == -1:
    raise SystemExit(f"Unexpected frontmatter in {skill_path}")
text = frontmatter + text[end + 5 :]

replacements = [
    (
        "You manage a Knowmade practical knowledge base. Source material is staged in a\ntopic `inbox/`, or first in `HUB/inbox/` when it is not classified yet. After\nextraction and provenance capture, processed immutable artifacts are written to\n`raw/` and incrementally compiled into interconnected markdown articles. The\nactive agent is both compiler and query engine; Obsidian is an optional viewer.\n",
        "You manage a Knowmade practical knowledge base. Source material is staged in a\ntopic `inbox/`, or first in `HUB/inbox/` when it is not classified yet. After\nextraction and provenance capture, processed immutable artifacts are written to\n`raw/` and incrementally compiled into interconnected markdown articles. OpenCode\nis both compiler and query engine; Obsidian is an optional viewer.\n\n## OpenCode Integration Notes\n\nThis skill is loaded as an instruction file. OpenCode does not have Claude-style `/knowmade:*` slash commands or Codex-style `@knowmade` invocations. Treat those examples as shorthand for the equivalent natural-language request. For example, `/knowmade:compile` means the user is asking you to compile the wiki.\n\nOpenCode's built-in tools (`read`, `write`, `edit`, `glob`, `grep`, `bash`, `webfetch`, `websearch`) map directly to the tools this skill requires. Web search requires `OPENCODE_ENABLE_EXA=1` in the environment.\n\n**Permissions**: OpenCode sandboxes file access to the project directory. The configured Knowmade hub is external. Add `external_directory` permissions in `opencode.json` for the hub and `{ \"~/.config/llm-wiki/**\": \"allow\" }`; for the preferred default, allow `~/knowmade/**`. If your hub uses another absolute path (for example iCloud Drive), add that path instead. Alternatively, use `--local` mode to keep everything in `.wiki/` inside the project.\n",
    ),
    # The Dual-linking principle moved to references/principles.md
    # (emercons/knowmade#81); references are shared verbatim and say "agent".
    (
        "When this skill activates outside an explicit wiki invocation:",
        "When this skill activates outside of an explicit wiki-related request:",
    ),
    (
        '4. If no relevant content → answer normally, optionally suggest: "This could be added to your wiki with `/knowmade:ingest`"',
        '4. If no relevant content → answer normally, optionally suggest: "This could be added to your wiki — just ask me to ingest it."',
    ),
    # Anchors for the Compilation Nudge, Structural Guardian, Concurrency and
    # stale-research-session wording were dropped when those sections moved to
    # references/skill-workflows.md (emercons/knowmade#55). References are shared
    # verbatim, so those rewrites no longer apply to SKILL.md; the neutral wording
    # ("the agent", "/wiki-style shorthand") is covered by the runtime notes above.
]

for old, new in replacements:
    if old not in text:
        raise SystemExit(f"Expected text not found in {skill_path}: {old[:80]!r}")
    text = text.replace(old, new)

skill_path.write_text(text, encoding="utf-8", newline="\n")

# references/ is copied from claude-plugin/skills/knowmade/references and
# is shared verbatim — no per-file replacements needed. Source references use
# runtime-neutral wording ("the agent") so they read correctly under all runtimes.
PY

echo "Synced OpenCode plugin skill from canonical Knowmade source."
echo "Source: $SOURCE_SKILL"
echo "Target: $TARGET_SKILL"
echo "Best-effort query preset: $TARGET_QUERY"
