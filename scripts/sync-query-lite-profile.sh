#!/usr/bin/env bash
# Regenerate profiles/query-lite/SKILL.md from the canonical query-lite reference.
#
# KNOWMADE_SYNC_OUT=<dir> redirects every generated file under <dir> using the
# same repository-relative paths (here: <dir>/profiles/query-lite/SKILL.md).
# Sources are always read from this checkout. Unset, the profile is written in
# place exactly as before; the mirror tests use the override to compare a
# scratch regeneration against the working tree without touching it.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -n "${KNOWMADE_SYNC_OUT:-}" ]]; then
  mkdir -p "$KNOWMADE_SYNC_OUT"
  OUT="$(cd "$KNOWMADE_SYNC_OUT" && pwd)"
else
  OUT="$ROOT"
fi
SOURCE="$ROOT/claude-plugin/skills/wiki-manager/references/query-lite.md"
TARGET="$OUT/profiles/query-lite/SKILL.md"

if [[ ! -f "$SOURCE" ]]; then
  echo "Missing query-lite source: $SOURCE" >&2
  exit 1
fi

mkdir -p "$(dirname "$TARGET")"
cat > "$TARGET" <<'EOF'
---
name: wiki-query
description: >
  Fast, read-only, index-first queries over a Knowmade wiki with bounded file
  reads, exact citations, and honest evidence gaps.
---

EOF
cat "$SOURCE" >> "$TARGET"

echo "Synced portable query-lite profile from Claude reference source."
echo "Source: $SOURCE"
echo "Target: $TARGET"
