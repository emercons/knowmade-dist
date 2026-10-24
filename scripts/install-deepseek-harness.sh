#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="$ROOT/plugins/llm-wiki-dsh/skills/knowmade"
DSH_ROOT="${DSH_HOME:-$HOME/.dsh}"
VERIFY=0
FORCE=0

usage() {
  cat <<'EOF'
Usage: install-deepseek-harness.sh [--dsh-home PATH] [--force] [--verify]

Install the generated Knowmade skill at <dsh-home>/skills/knowmade.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dsh-home) DSH_ROOT="${2:?--dsh-home requires a path}"; shift 2 ;;
    --force) FORCE=1; shift ;;
    --verify) VERIFY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ ! -f "$SOURCE/SKILL.md" ] || [ ! -x "$SOURCE/bin/llm-wiki" ]; then
  echo "Generated DeepSeek Harness skill is missing; run scripts/sync-dsh-plugin.sh." >&2
  exit 1
fi

TARGET="$DSH_ROOT/skills/knowmade"
MARKER="$TARGET/.knowmade-managed"
if [ -e "$TARGET" ] && [ ! -f "$MARKER" ] && [ "$FORCE" -ne 1 ]; then
  echo "Refusing to replace unmanaged skill: $TARGET" >&2
  echo "Move it aside or rerun with --force." >&2
  exit 1
fi

mkdir -p "$DSH_ROOT/skills"
STAGE="$DSH_ROOT/skills/.knowmade-stage-$$"
trap 'rm -rf "$STAGE"' EXIT
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R "$SOURCE/." "$STAGE/"
printf 'managed by Knowmade scripts/install-deepseek-harness.sh\n' > "$STAGE/.knowmade-managed"
rm -rf "$TARGET"
mv "$STAGE" "$TARGET"
trap - EXIT

echo "Installed DeepSeek Harness skill: $TARGET"
echo "Invoke it as /knowmade in DeepSeek Harness."

if [ "$VERIFY" -eq 1 ]; then
  python3 - "$TARGET" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1])
text = (root / "SKILL.md").read_text(encoding="utf-8")
assert text.startswith("---\n")
assert "\nname: knowmade\n" in text
assert "\ndescription:" in text
assert (root / "references" / "wiki-structure.md").is_file()
assert (root / "references" / "refresh-protocol.md").is_file()
assert (root / "bin" / "llm-wiki").is_file()
assert (root / "bin" / "knowmade_control" / "cli.py").is_file()
print(f"Verified DeepSeek Harness skill bundle: {root}")
PY
fi
