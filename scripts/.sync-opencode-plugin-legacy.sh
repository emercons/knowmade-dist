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
SOURCE_SKILL="$ROOT/claude-plugin/skills/wiki-manager"
QUERY_SOURCE="$SOURCE_SKILL/references/query-lite.md"
TARGET_PLUGIN_REL="plugins/llm-wiki-opencode"
TARGET_PLUGIN="$OUT/$TARGET_PLUGIN_REL"
TARGET_SKILL="$TARGET_PLUGIN/skills/wiki-manager"
TARGET_QUERY="$TARGET_PLUGIN/skills/wiki-query"
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

mkdir -p "$TARGET_PLUGIN/skills" "$TARGET_SKILL"
# references/ is a symlink into the canonical source. It must never be copied
# over: copying the source references/ onto its own symlink would write into
# the canonical tree. Mirror every other entry, then handle the link itself.
find "$TARGET_SKILL" -mindepth 1 -maxdepth 1 ! -name references -exec rm -rf {} +
for entry in "$SOURCE_SKILL"/* "$SOURCE_SKILL"/.[!.]*; do
  [ -e "$entry" ] || continue
  case "$(basename "$entry")" in
    references) continue ;;
  esac
  cp -R "$entry" "$TARGET_SKILL/"
done

# Recreate the references symlink only when it is missing or wrong. Rewriting a
# correct link every run is what breaks checkouts on platforms where ln -s
# silently produces something other than a symlink (Windows without the
# privilege to create them), turning a good tree into a dirty one.
#
# There are two correct representations. On a platform that has symlinks, the
# entry is one. Where core.symlinks is false — git's own default on Windows —
# git materialises a tracked symlink as a *regular file* holding the link
# target, and that file is exactly what the repository wants committed. Treating
# it as "not a link" is what made every sync destroy a healthy checkout: the
# script removed the placeholder, failed to create a real link, and left a
# directory copy in its place.
REFERENCES_LINK="../../../../claude-plugin/skills/wiki-manager/references"

references_is_intact() {
  local path="$TARGET_SKILL/references"
  if [ -L "$path" ]; then
    [ "$(readlink "$path")" = "$REFERENCES_LINK" ]
    return
  fi
  # Placeholder form: a regular file that git tracks with symlink mode 120000
  # and whose whole content is the link target. The tracking question is always
  # asked of the checkout, by repository-relative path, so it still holds when
  # KNOWMADE_SYNC_OUT points the output tree outside the repository.
  [ -f "$path" ] || return 1
  [ "$(git -C "$ROOT" ls-files -s -- "$TARGET_PLUGIN_REL/skills/wiki-manager/references" 2>/dev/null | cut -d' ' -f1)" = "120000" ] || return 1
  [ "$(cat "$path")" = "$REFERENCES_LINK" ]
}

if references_is_intact; then
  :
else
  rm -rf "$TARGET_SKILL/references"
  ln -s "$REFERENCES_LINK" "$TARGET_SKILL/references" 2>/dev/null || true
  if [ ! -L "$TARGET_SKILL/references" ]; then
    # No symlink support: write the same placeholder git itself checks out, so
    # the result is byte-identical to what the repository tracks and the tree
    # stays clean. A directory copy would both dirty the tree and duplicate the
    # canonical references.
    rm -rf "$TARGET_SKILL/references"
    printf '%s' "$REFERENCES_LINK" > "$TARGET_SKILL/references"
    echo "note: this platform cannot create symlinks; wrote the tracked link placeholder instead." >&2
  fi
fi

# Best-effort OpenCode query preset. It is instruction-only and shares the
# runtime-neutral query contract, but no provider-specific live model gate is
# claimed because OpenCode does not select a model on behalf of the user.
rm -rf "$TARGET_QUERY"
mkdir -p "$TARGET_QUERY"
cat > "$TARGET_QUERY/SKILL.md" <<'EOF'
---
name: wiki-query
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
name: wiki-manager
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
        "You manage a Knowmade practical knowledge base. Source material is staged in a\ntopic `inbox/`, or first in `HUB/inbox/` when it is not classified yet. After\nextraction and provenance capture, processed immutable artifacts are written to\n`raw/` and incrementally compiled into interconnected markdown articles. OpenCode\nis both compiler and query engine; Obsidian is an optional viewer.\n\n## OpenCode Integration Notes\n\nThis skill is loaded as an instruction file. OpenCode does not have Claude-style `/wiki:*` slash commands or Codex-style `@wiki` invocations. Treat those examples as shorthand for the equivalent natural-language request. For example, `/wiki:compile` means the user is asking you to compile the wiki.\n\nOpenCode's built-in tools (`read`, `write`, `edit`, `glob`, `grep`, `bash`, `webfetch`, `websearch`) map directly to the tools this skill requires. Web search requires `OPENCODE_ENABLE_EXA=1` in the environment.\n\n**Permissions**: OpenCode sandboxes file access to the project directory. The configured Knowmade hub is external. Add `external_directory` permissions in `opencode.json` for the hub and `{ \"~/.config/llm-wiki/**\": \"allow\" }`; for the preferred default, allow `~/knowmade/**`. If your hub uses another absolute path (for example iCloud Drive), add that path instead. Alternatively, use `--local` mode to keep everything in `.wiki/` inside the project.\n",
    ),
    (
        "**Dual-linking for Obsidian + agents.** Cross-references use both `[[wikilink]]` (for Obsidian graph view) and standard markdown `[text](path)` (for agent navigation) on the same line: `[[slug|Name]] ([Name](../category/slug.md))`. Bidirectional when it makes sense.",
        "**Dual-linking for Obsidian + OpenCode.** Cross-references use both `[[wikilink]]` (for Obsidian graph view) and standard markdown `[text](path)` (for OpenCode navigation) on the same line: `[[slug|Name]] ([Name](../category/slug.md))`. Bidirectional when it makes sense.",
    ),
    (
        "When this skill activates outside an explicit wiki invocation:",
        "When this skill activates outside of an explicit wiki-related request:",
    ),
    (
        '4. If no relevant content → answer normally, optionally suggest: "This could be added to your wiki with `/wiki:ingest`"',
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

# references/ is a symlink to claude-plugin/skills/wiki-manager/references and
# is shared verbatim — no per-file replacements needed. Source references use
# runtime-neutral wording ("the agent") so they read correctly under all runtimes.
PY

echo "Synced OpenCode plugin skill from canonical Knowmade source."
echo "Source: $SOURCE_SKILL"
echo "Target: $TARGET_SKILL"
echo "Best-effort query preset: $TARGET_QUERY"
