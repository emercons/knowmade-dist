#!/usr/bin/env python3
"""Stamp the hub-resolution prelude into every command that carries it.

``references/hub-resolution.md`` holds the one template, between
``<!-- hub-prelude-template:start -->`` and ``<!-- hub-prelude-template:end -->``.
Each command that needs a wiki carries a marked block (kind ``full``)::

    <!-- hub-prelude:start -->
    ...generated, do not edit...
    <!-- hub-prelude:end -->

A command that needs only the hub (``session``, ``specialist``) carries a
second kind, ``hub-only``, between ``<!-- hub-only-prelude:start -->`` and
``<!-- hub-only-prelude:end -->``. There is no second literal template: the
hub-only text is item 1 of the full template (its leading ``1. `` removed), so
the two kinds cannot drift apart. A file carries one pair of one kind, never
both.

This script rewrites every marked block under ``claude-plugin/commands/`` from
the template so the copies cannot drift.

    scripts/sync-command-prelude.py            # rewrite stale blocks in place
    scripts/sync-command-prelude.py --check    # exit 1 when any block differs
    scripts/sync-command-prelude.py --list     # print each stamped command and its kind

``--check`` also fails on a malformed marker pair (unmatched, nested, or more
than one pair per file) and on a file that carries both kinds. Both plugin sync
scripts call this script; the lane asserts ``--check`` in
``tests/test-plugin-validate.sh``.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = Path("claude-plugin")
TEMPLATE_SOURCE = PLUGIN / "skills" / "knowmade" / "references" / "hub-resolution.md"
COMMANDS = PLUGIN / "commands"

TEMPLATE_START = "<!-- hub-prelude-template:start -->"
TEMPLATE_END = "<!-- hub-prelude-template:end -->"
START = "<!-- hub-prelude:start -->"
END = "<!-- hub-prelude:end -->"
HUB_START = "<!-- hub-only-prelude:start -->"
HUB_END = "<!-- hub-only-prelude:end -->"
FULL, HUB_ONLY = "full", "hub-only"


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
    for marker in (START, END, HUB_START, HUB_END):
        if marker in template:
            raise PreludeError(f"{TEMPLATE_SOURCE}: the template must not contain command markers")
    return template


def hub_only_template(template: str) -> str:
    """The hub-only text: item 1 of the full template, without its number."""
    match = re.search(r"^1\. (HUB: .*)$", template, re.M)
    if not match:
        raise PreludeError(f"{TEMPLATE_SOURCE}: the template has no numbered item 1 starting with 'HUB:'")
    return match.group(1)


def kind_of(text: str, name: str) -> str | None:
    """The block kind a command carries, or None; both kinds is an error."""
    full = START in text or END in text
    hub = HUB_START in text or HUB_END in text
    if full and hub:
        raise PreludeError(f"{name}: carries both a hub-prelude and a hub-only-prelude block; pick one")
    return FULL if full else HUB_ONLY if hub else None


def stamp(text: str, template: str, name: str, kind: str = FULL) -> str:
    """Return ``text`` with its marked block replaced by the kind's text."""
    start, end = (START, END) if kind == FULL else (HUB_START, HUB_END)
    body = template if kind == FULL else hub_only_template(template)
    starts, ends = text.count(start), text.count(end)
    if (starts, ends) != (1, 1):
        raise PreludeError(f"{name}: expected one {start} / {end} pair, found {starts} / {ends}")
    head, rest = text.split(start, 1)
    if end not in rest:
        raise PreludeError(f"{name}: {end} comes before {start}")
    _old, tail = rest.split(end, 1)
    return f"{head}{start}\n{body}\n{end}{tail}"


def stamped_commands(root: Path) -> list[tuple[Path, str]]:
    found = []
    for path in sorted((root / COMMANDS).glob("*.md")):
        kind = kind_of(path.read_text(encoding="utf-8"), path.name)
        if kind:
            found.append((path, kind))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="exit 1 when any block differs; write nothing")
    parser.add_argument("--list", action="store_true", help="print each stamped command name and its kind")
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root (default: this checkout)")
    args = parser.parse_args(argv)
    root = args.root.resolve()

    try:
        template = read_template(root)
        paths = stamped_commands(root)
        if args.list:
            for path, kind in paths:
                print(f"{path.stem} {kind}")
            return 0
        stale = []
        for path, kind in paths:
            # newline="" keeps the file's own line endings; the template and the
            # commands are LF, and a CRLF checkout must fail --check, not be hidden.
            with path.open(encoding="utf-8", newline="") as handle:
                current = handle.read()
            wanted = stamp(current, template, path.name, kind)
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
        hub_only = sum(1 for _path, kind in paths if kind == HUB_ONLY)
        print(f"{len(paths)} command prelude block(s) match the template ({hub_only} hub-only)")
        return 0

    if stale:
        for path in stale:
            print(f"restamped {path.relative_to(root).as_posix()}")
    print(f"Stamped hub-resolution prelude into {len(paths)} command(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
