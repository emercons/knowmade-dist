#!/usr/bin/env bash
# Regenerate the Codex runtime mirror (plugins/llm-wiki/) and the shared
# claude-plugin/bin/ copies from the canonical Knowmade source.
#
# KNOWMADE_SYNC_OUT=<dir> redirects every generated file under <dir> using the
# same repository-relative paths (<dir>/plugins/llm-wiki/..., and
# <dir>/claude-plugin/bin/...). Sources are always read from this checkout.
# Unset, the mirrors are regenerated in place exactly as before; the mirror
# tests use the override to compare a scratch regeneration against the working
# tree without touching it.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -n "${KNOWMADE_SYNC_OUT:-}" ]; then
  mkdir -p "$KNOWMADE_SYNC_OUT"
  OUT="$(cd "$KNOWMADE_SYNC_OUT" && pwd)"
  # Hand the legacy layer the resolved absolute path so both layers agree.
  export KNOWMADE_SYNC_OUT="$OUT"
else
  OUT="$ROOT"
fi
LEGACY="$ROOT/scripts/.sync-codex-plugin-legacy.sh"
CONTROL_PACKAGE="$ROOT/scripts/knowmade_control"
BUILD_IDENTITY="$ROOT/scripts/.sync-build-identity.sh"

if [ ! -x "$LEGACY" ]; then
  echo "Missing v0.24.3 Codex sync implementation: $LEGACY" >&2
  exit 1
fi

# Preserve the exact v0.24.3 prerequisite/fail-closed ordering first.
"$LEGACY" "$@"

# Stamp the hub-resolution prelude into the canonical command specs (after the
# legacy prerequisite checks, which must stay first) so the copies cannot drift
# from the template in references/hub-resolution.md. A scratch regeneration
# (KNOWMADE_SYNC_OUT) must not touch the checkout's own sources;
# tests/test-plugin-validate.sh asserts the blocks there instead.
if [ -z "${KNOWMADE_SYNC_OUT:-}" ]; then
  python3 -B "$ROOT/scripts/sync-command-prelude.py"
  # The same goes for the redaction table stamped into the session script
  # from scripts/knowmade_control/redaction.py. The legacy layer above has
  # already copied the session script, so the stamped source is copied again.
  python3 -B "$ROOT/scripts/sync-session-vendor.py"
  cp "$ROOT/scripts/llm-wiki-session" "$OUT/claude-plugin/bin/llm-wiki-session"
  cp "$ROOT/scripts/llm-wiki-session" "$OUT/plugins/llm-wiki/hooks/llm_wiki_session.py"
fi

if [ ! -d "$CONTROL_PACKAGE" ]; then
  echo "Missing deterministic control-plane package: $CONTROL_PACKAGE" >&2
  exit 1
fi
if [ ! -x "$BUILD_IDENTITY" ]; then
  echo "Missing build-identity generator: $BUILD_IDENTITY" >&2
  exit 1
fi

for target in "$OUT/claude-plugin/bin" "$OUT/plugins/llm-wiki/bin"; do
  mkdir -p "$target"
  rm -rf "$target/knowmade_control"
  cp -R "$CONTROL_PACKAGE" "$target/knowmade_control"
  # Python may have created ignored bytecode in the source package while local
  # checks ran. Runtime mirrors are source-only and must not inherit it: on
  # Windows a later sync can otherwise fail while removing copied caches.
  find "$target/knowmade_control" -type d -name __pycache__ -prune -exec rm -rf {} +
done

# The generated build identity (version, commit, rule set) is written into the
# source package and every copy made above, all under $OUT, so a scratch run
# never touches the checkout's own scripts/knowmade_control/_build.py.
"$BUILD_IDENTITY" \
  "$OUT/scripts/knowmade_control/_build.py" \
  "$OUT/claude-plugin/bin/knowmade_control/_build.py" \
  "$OUT/plugins/llm-wiki/bin/knowmade_control/_build.py"

echo "Synced modular deterministic control plane into Claude and Codex runtime bins."
