#!/usr/bin/env python3
"""Stamp the shared redaction table into ``scripts/llm-wiki-session``.

The session script ships as one file (a Codex hook, a Claude/OpenCode helper)
and cannot import ``knowmade_control``. Its secret patterns used to be a
separately maintained copy that fell behind the checkpoint scanner
(emercons/knowmade#88, #94). Now the body of
``scripts/knowmade_control/redaction.py`` (everything after its imports) is
stamped between two markers in the session script::

    # --- generated from scripts/knowmade_control/redaction.py ... ---
    ...generated, do not edit...
    # --- end generated redaction table ---

    scripts/sync-session-vendor.py            # rewrite a stale block in place
    scripts/sync-session-vendor.py --check    # exit 1 when the block differs

Both plugin sync scripts run this before they copy the session script into
the runtime mirrors; the lane asserts ``--check`` in
``tests/test-session-capture.sh``. The module may import only ``re`` (and
``from __future__``), which the session script already imports.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent
SOURCE = Path("scripts/knowmade_control/redaction.py")
TARGET = Path("scripts/llm-wiki-session")
START = (
    "# --- generated from scripts/knowmade_control/redaction.py by "
    "scripts/sync-session-vendor.py; do not edit ---"
)
END = "# --- end generated redaction table ---"
ALLOWED_IMPORTS = {"re", "__future__"}


class VendorError(Exception):
    """A source or marker problem that stamping cannot repair."""


def module_body(root: Path) -> str:
    """The redaction module without its docstring and imports."""
    text = (root / SOURCE).read_text(encoding="utf-8")
    tree = ast.parse(text)
    last_import_line = 0
    for node in tree.body:
        if isinstance(node, ast.Import):
            names = {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            names = {node.module or ""}
        else:
            continue
        if not names <= ALLOWED_IMPORTS:
            raise VendorError(f"{SOURCE}: may import only re; found {sorted(names)}")
        last_import_line = max(last_import_line, node.end_lineno or node.lineno)
    body = "\n".join(text.splitlines()[last_import_line:]).strip("\n")
    if START in body or END in body:
        raise VendorError(f"{SOURCE}: the module must not contain the session markers")
    return body


def stamped(target_text: str, body: str) -> str:
    if target_text.count(START) != 1 or target_text.count(END) != 1:
        raise VendorError(f"{TARGET}: expected exactly one generated redaction marker pair")
    head, rest = target_text.split(START, 1)
    _old, tail = rest.split(END, 1)
    return f"{head}{START}\n{body}\n{END}{tail}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="Fail when the stamped block is stale.")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        body = module_body(args.root)
        target = args.root / TARGET
        current = target.read_text(encoding="utf-8")
        expected = stamped(current, body)
    except (OSError, VendorError) as exc:
        print(f"sync-session-vendor: {exc}", file=sys.stderr)
        return 1
    if current == expected:
        return 0
    if args.check:
        print(
            f"sync-session-vendor: {TARGET} is stale against {SOURCE}; "
            "run scripts/sync-session-vendor.py (or either plugin sync script)",
            file=sys.stderr,
        )
        return 1
    target.write_bytes(expected.encode("utf-8"))
    print(f"Stamped the redaction table into {TARGET}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
