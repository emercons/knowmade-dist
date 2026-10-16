#!/usr/bin/env python3
"""Stamp the hub-resolution prelude into every command that carries it.

``references/hub-resolution.md`` holds the one template, between
``<!-- hub-prelude-template:start -->`` and ``<!-- hub-prelude-template:end -->``.
Each command that needs a wiki carries a marked block::

    <!-- hub-prelude:start -->
    ...generated, do not edit...
    <!-- hub-prelude:end -->

This script rewrites every marked block under ``claude-plugin/commands/`` from
the template so the copies cannot drift.

    scripts/sync-command-prelude.py            # rewrite stale blocks in place
    scripts/sync-command-prelude.py --check    # exit 1 when any block differs
    scripts/sync-command-prelude.py --list     # print the stamped command names

``--check`` also fails on a malformed marker pair (unmatched, nested, or more
than one pair per file). Both plugin sync scripts call this script; the lane
asserts ``--check`` in ``tests/test-plugin-validate.sh``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = Path("claude-plugin")
TEMPLATE_SOURCE = PLUGIN / "skills" / "wiki-manager" / "references" / "hub-resolution.md"
COMMANDS = PLUGIN / "commands"

TEMPLATE_START = "<!-- hub-prelude-template:start -->"
TEMPLATE_END = "<!-- hub-prelude-template:end -->"
START = "<!-- hub-prelude:start -->"
END = "<!-- hub-prelude:end -->"


class PreludeError(Exception):
    """A template or marker problem that stamping cannot repair."""


def read_template(root: Path) -> str:
    path = root / TEMPLATE_SOURCE
    text = path.read_text(encoding="utf-8")
    if text.count(TEMPLATE_START) != 1 or text.count(TEMPLATE_END) != 1:
        raise PreludeError(f"{TEMPLATE_SOURCE}: expected exactly one template marker pair")
    head, rest = text.split(TEMPLATE_START, 1)
    body, _tail = rest.split(TEMPLATE_END, 1)
    template = body.strip("\n")
    if not template.strip():
        raise PreludeError(f"{TEMPLATE_SOURCE}: the prelude template is empty")
    if START in template or END in template:
        raise PreludeError(f"{TEMPLATE_SOURCE}: the template must not contain command markers")
    return template


def stamp(text: str, template: str, name: str) -> str:
    """Return ``text`` with its marked block replaced by ``template``."""
    starts, ends = text.count(START), text.count(END)
    if (starts, ends) != (1, 1):
        raise PreludeError(f"{name}: expected one {START} / {END} pair, found {starts} / {ends}")
    head, rest = text.split(START, 1)
    if END not in rest:
        raise PreludeError(f"{name}: {END} comes before {START}")
    _old, tail = rest.split(END, 1)
    return f"{head}{START}\n{template}\n{END}{tail}"


def stamped_commands(root: Path) -> list[Path]:
    found = []
    for path in sorted((root / COMMANDS).glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if START in text or END in text:
            found.append(path)
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="exit 1 when any block differs; write nothing")
    parser.add_argument("--list", action="store_true", help="print the stamped command names")
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root (default: this checkout)")
    args = parser.parse_args(argv)
    root = args.root.resolve()

    try:
        template = read_template(root)
        paths = stamped_commands(root)
        if args.list:
            for path in paths:
                print(path.stem)
            return 0
        stale = []
        for path in paths:
            # newline="" keeps the file's own line endings; the template and the
            # commands are LF, and a CRLF checkout must fail --check, not be hidden.
            with path.open(encoding="utf-8", newline="") as handle:
                current = handle.read()
            wanted = stamp(current, template, path.name)
            if wanted != current:
                stale.append(path)
                if not args.check:
                    with path.open("w", encoding="utf-8", newline="\n") as handle:
                        handle.write(wanted)
    except PreludeError as exc:
        print(f"sync-command-prelude: {exc}", file=sys.stderr)
        return 1

    if args.check:
        if stale:
            for path in stale:
                print(f"stale prelude block: {path.relative_to(root).as_posix()}", file=sys.stderr)
            print("run scripts/sync-command-prelude.py and commit the result", file=sys.stderr)
            return 1
        print(f"{len(paths)} command prelude block(s) match the template")
        return 0

    if stale:
        for path in stale:
            print(f"restamped {path.relative_to(root).as_posix()}")
    print(f"Stamped hub-resolution prelude into {len(paths)} command(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
