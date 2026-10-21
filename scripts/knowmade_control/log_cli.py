"""The ``log`` namespace: ``log append`` (emercons/knowmade#83).

Writes one entry to a wiki's ``log.md`` in the format ``wiki-format.md``
(§ log.md Format) gives the agent: ``## [YYYY-MM-DD] <kind> | <message>``, one
line, appended. An existing log is only ever appended to (the spec's rule:
never read-modify-write, so concurrent sessions interleave without losing an
entry), keeping its newline style and a BOM it may carry; a missing log is
created atomically with the spec's header. The append is a single small
write, like ``lint --fix``'s own log entry.

A public (non-``-PRIVATE``) wiki is write-guarded: the message may not carry
a secret, a machine-local path or a ``-PRIVATE`` path, judged by the same
critical rows the checkpoint scanner applies (``redaction`` pattern table).
Every wiki refuses a message that is not one line.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

from .checkpoint_scan import scan_checkpoint_text
from .common import PRIVATE_SUFFIX, add_scope_arguments
from .fsio import atomic_write_text
from .markdown import append_text_preserving, today_date
from .privacy_boundary import is_private_path, split_real
from .roots import resolve_wiki_root_args

SCHEMA = "knowmade/log-append/v1"
HEADER = "# Wiki Activity Log\n\n"
# The operations ``wiki-format.md`` lists; the set is open (``dispatch`` and
# ``schema`` entries exist too), so any grep-friendly token is accepted.
KNOWN_KINDS = (
    "init", "ingest", "ingest-collection", "compile", "query", "lint", "research", "output",
    "refresh", "librarian", "audit", "plan", "idea", "project", "inventory", "dataset",
    "schema", "archive", "retract", "ll", "assess",
)
DEFAULT_KIND = "note"
_KIND_RE = re.compile(r"^[a-z][a-z0-9-]*$")


def entry_line(date: str, kind: str, message: str) -> str:
    return f"## [{date}] {kind} | {message}"


def is_public(root: Path) -> bool:
    """True when no component of the wiki's real path is a ``<slug>-PRIVATE`` name."""
    return not is_private_path(split_real(root))


def guard_message(message: str, *, public: bool) -> str | None:
    """Why ``message`` may not be logged, or ``None`` when it may."""
    if "\n" in message or "\r" in message:
        return "the message must be one line (no newline)"
    if not message.strip():
        return "the message is empty"
    if public:
        categories = sorted(
            {
                finding.category
                for finding in scan_checkpoint_text(message, "log.md")
                if finding.severity == "critical"
            }
        )
        if categories:
            return (
                "this wiki is public and the message carries private or machine-local material "
                f"({', '.join(categories)}); log it in the -PRIVATE sibling instead"
            )
    return None


def _ends_with_newline(path: Path) -> bool:
    size = path.stat().st_size
    if size == 0:
        return True
    with open(path, "rb") as handle:
        handle.seek(size - 1)
        return handle.read(1) == b"\n"


def append_entry(log: Path, line: str) -> bool:
    """Append ``line`` to ``log``; returns whether the file was created."""
    if log.exists():
        append_text_preserving(log, ("" if _ends_with_newline(log) else "\n") + line + "\n")
        return False
    atomic_write_text(log, HEADER + line + "\n")
    return True


def run_append(args: argparse.Namespace) -> int:
    message = args.message.strip()
    kind = args.kind
    if not _KIND_RE.match(kind):
        print(f"log append: --kind must be lowercase letters, digits and hyphens, not {kind!r}", file=sys.stderr)
        return 2
    if args.date:
        try:
            date = dt.date.fromisoformat(args.date).isoformat()
        except ValueError:
            print(f"log append: --date must be YYYY-MM-DD, not {args.date!r}", file=sys.stderr)
            return 2
        if date != args.date:
            print(f"log append: --date must be YYYY-MM-DD, not {args.date!r}", file=sys.stderr)
            return 2
    else:
        date = today_date().isoformat()
    root = resolve_wiki_root_args(args).resolve()
    if not root.is_dir():
        print(f"log append: no wiki root at {root}", file=sys.stderr)
        return 2
    public = is_public(root)
    refusal = guard_message(message, public=public)
    if refusal:
        print(f"log append: refused: {refusal}", file=sys.stderr)
        return 2
    line = entry_line(date, kind, message)
    log = root / "log.md"
    created = append_entry(log, line)
    if args.json:
        print(
            json.dumps(
                {
                    "schema": SCHEMA,
                    "root": str(root),
                    "path": "log.md",
                    "entry": line,
                    "created": created,
                    "public": public,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(f"{'Created' if created else 'Appended to'} {log}: {line}")
    return 0


def register_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Register the ``log`` namespace on a top-level subparsers action."""
    log = subparsers.add_parser(
        "log",
        help="Append an entry to a wiki's log.md.",
        description="Deterministic log.md writer (no LLM) in the format wiki-format.md gives the agent.",
    )
    sub = log.add_subparsers(dest="log_command", required=True)
    append = sub.add_parser(
        "append",
        help="Append one '## [YYYY-MM-DD] kind | message' line to log.md.",
        description=(
            "Appends one line, never rewriting the file, and creates log.md with its header if it is "
            "missing. Exit 0 on success; exit 2, writing nothing, when the message is empty or spans "
            "lines, when the wiki is public and the message carries a secret, a machine-local path or "
            "a -PRIVATE path, or when the wiki root, --kind or --date is invalid."
        ),
    )
    append.add_argument("message", help="The entry text after the '|'; one line.")
    append.add_argument(
        "--kind",
        default=DEFAULT_KIND,
        metavar="KIND",
        help=(
            f"Operation word before the '|' (default {DEFAULT_KIND}); lowercase letters, digits and hyphens. "
            f"Known: {', '.join(KNOWN_KINDS)}."
        ),
    )
    append.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        help="Entry date (default: today, or LLM_WIKI_TODAY).",
    )
    append.add_argument("--path", help="Wiki root path whose log.md to append to.")
    add_scope_arguments(
        append,
        local_help="Append to .wiki/log.md in the current directory.",
        public_help=f"Resolve --wiki <slug>{PRIVATE_SUFFIX} to the public sibling <slug>.",
        include_archived_help="Allow appending to an explicitly targeted archived wiki.",
    )
    append.add_argument("--json", action="store_true", help="Print a machine-readable JSON result.")
    log.set_defaults(func=run)
    return log


def run(args: argparse.Namespace) -> int:
    """Dispatch a parsed ``log`` namespace."""
    return run_append(args)
