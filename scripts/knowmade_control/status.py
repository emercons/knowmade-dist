"""The ``status`` namespace: a read-only one-screen overview of a topic wiki.

``llm-wiki status`` answers "where does this wiki stand" without the agent
counting files by hand (emercons/knowmade#83): raw sources and articles per
type, open inbox items, the last ``log.md`` entry, a lint summary and a
freshness summary. It computes nothing the other modules do not already
compute. The counts come from the document set lint loads
(``lint_docs.load_documents``), the lint summary from a lint run that cannot
write (``lint.read_only_lint``), the freshness summary from
``freshness.report_payload``. Nothing is repaired, planned or logged; a hub
root is not a topic and is refused.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from . import freshness as _freshness
from . import lint as _lint
from . import rules as _rules
from .common import PRIVATE_SUFFIX, add_scope_arguments
from .lint_hub import is_private_placeholder
from .lint_report import LintContext
from .roots import is_hub, resolve_wiki_root_args

SCHEMA = "knowmade/status/v1"

# The layers whose files are counted, in report order, and the ones broken
# down by their first sub-directory (the raw ``type``, the article category).
LAYERS = ("raw", "wiki", "inventory", "datasets")
BROKEN_DOWN = {"raw", "wiki", "inventory"}

LOG_ENTRY = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})\][ \t]*([^|\n]*?)[ \t]*(?:\|[ \t]*(.*?))?[ \t]*$")


def document_counts(ctx: LintContext) -> dict[str, dict[str, Any]]:
    """Per layer, the number of content files and (raw, wiki, inventory) the count per directory."""
    totals: Counter[str] = Counter()
    buckets: dict[str, Counter[str]] = {layer: Counter() for layer in LAYERS}
    for doc in ctx.documents.values():
        try:
            parts = doc.path.resolve().relative_to(ctx.root).parts
        except ValueError:
            continue
        if not parts or parts[0] not in LAYERS:
            continue
        totals[parts[0]] += 1
        if parts[0] in BROKEN_DOWN and len(parts) > 2:
            buckets[parts[0]][parts[1]] += 1
    return {
        layer: {"total": totals[layer], "by_type": dict(sorted(buckets[layer].items()))}
        for layer in LAYERS
    }


def open_inbox_items(root: Path) -> int:
    """Files waiting in ``inbox/``: everything outside the dot directories (``.processed``, ``.unknown``, ``.imports``)."""
    inbox = root / "inbox"
    if not inbox.is_dir():
        return 0
    count = 0
    for path in inbox.rglob("*"):
        parts = path.relative_to(inbox).parts
        if path.is_file() and not any(part.startswith(".") for part in parts):
            count += 1
    return count


def last_log_entry(root: Path) -> dict[str, str] | None:
    """The last ``## [YYYY-MM-DD] operation | message`` heading of ``log.md``, or ``None``."""
    try:
        text = (root / "log.md").read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None
    found = None
    for line in text.splitlines():
        match = LOG_ENTRY.match(line)
        if match:
            found = {"date": match.group(1), "operation": match.group(2), "message": match.group(3) or ""}
    return found


def lint_summary(ctx: LintContext) -> dict[str, Any]:
    counts = ctx.counts()
    return {
        "counts": counts,
        "fixable": sum(1 for issue in ctx.active_issues() if issue.fixable),
        "status": "fail" if counts["critical"] or counts["warning"] or counts["suggestion"] else "pass",
    }


def freshness_summary(root: Path) -> dict[str, Any]:
    report = _freshness.report_payload(root)
    return {
        "stale": len(report["articles"]),
        "undated": len(report["undated"]),
        "snapshots_skipped": report["snapshots_skipped"],
        "windows": {name: row["days"] for name, row in report["windows"].items()},
        "problems": report["problems"],
    }


def status_payload(root: Path) -> dict[str, Any]:
    """The whole overview of the topic wiki at ``root``; reads, never writes."""
    ctx = _lint.read_only_lint(root)
    fields = _rules.config_fields(root) or {}
    counts = document_counts(ctx)
    return {
        "schema": SCHEMA,
        "root": str(ctx.root),
        "title": str(fields.get("title") or root.name),
        "private": ctx.root.parent.name.endswith(PRIVATE_SUFFIX) or ctx.root.name.endswith(PRIVATE_SUFFIX),
        "sources": counts["raw"],
        "articles": counts["wiki"],
        "inventory": counts["inventory"],
        "datasets": counts["datasets"],
        "inbox_open": open_inbox_items(ctx.root),
        "last_log": last_log_entry(ctx.root),
        "lint": lint_summary(ctx),
        "freshness": freshness_summary(ctx.root),
    }


def _breakdown(row: dict[str, Any]) -> str:
    by_type = row["by_type"]
    return f" ({', '.join(f'{name} {number}' for name, number in by_type.items())})" if by_type else ""


def print_status(payload: dict[str, Any]) -> None:
    lint = payload["lint"]
    counts = lint["counts"]
    fresh = payload["freshness"]
    log = payload["last_log"]
    print(f"Knowmade status: {payload['title']}{' [private]' if payload['private'] else ''}")
    print(f"Root: {payload['root']}")
    print(f"Sources:   {payload['sources']['total']}{_breakdown(payload['sources'])}")
    print(f"Articles:  {payload['articles']['total']}{_breakdown(payload['articles'])}")
    print(f"Inventory: {payload['inventory']['total']}{_breakdown(payload['inventory'])}")
    print(f"Datasets:  {payload['datasets']['total']}")
    print(f"Inbox:     {payload['inbox_open']} open")
    print(f"Last log:  {log['date'] + ' ' + log['operation'] + (' | ' + log['message'] if log['message'] else '') if log else 'none'}")
    print(
        f"Lint:      {counts['critical']} critical, {counts['warning']} warnings, "
        f"{counts['suggestion']} suggestions, {counts['info']} info "
        f"({lint['fixable']} fixable) - {lint['status'].upper()}"
    )
    print(
        f"Freshness: {fresh['stale']} stale, {fresh['undated']} undated "
        f"(windows hot {fresh['windows']['hot']}, warm {fresh['windows']['warm']}, "
        f"cold {fresh['windows']['cold']} days)"
    )
    for problem in fresh["problems"]:
        print(f"  freshness problem: {problem}")
    if lint["counts"]["critical"] or lint["counts"]["warning"]:
        print("Run `llm-wiki lint` for the findings.")


def run(args: argparse.Namespace) -> int:
    root = resolve_wiki_root_args(args)
    if not root.is_dir():
        print(f"status: no wiki root at {root}", file=sys.stderr)
        return 2
    if is_hub(root):
        print(
            f"status: {root} is a hub, not a topic wiki; pass --wiki <slug> or a topic path "
            "(`llm-wiki archive list` and `llm-wiki hub index` cover the hub)",
            file=sys.stderr,
        )
        return 2
    if is_private_placeholder(root):
        print(f"status: {root} is a private placeholder, not checked out here", file=sys.stderr)
        return 2
    payload = status_payload(root)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print_status(payload)
    return 0


STATUS_HELP = "Read-only one-screen overview of a topic wiki: counts, inbox, last log entry, lint and freshness."


def register_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Register ``status`` on a parent ``llm-wiki`` parser; dispatch through :func:`run`."""
    status = subparsers.add_parser(
        "status",
        help=STATUS_HELP,
        description=(
            "Read-only. Sources per raw type, articles per category, inventory and dataset counts, "
            "open inbox items (files outside the dot directories), the last log.md entry, a lint "
            "summary (the same checks as `lint`, run without any repair) and the freshness summary "
            "(`freshness report`). Exit 0, or 2 when the root is missing, is a hub or is a private "
            "placeholder."
        ),
    )
    status.add_argument("path", nargs="?", help="Topic wiki root to summarize.")
    add_scope_arguments(
        status,
        local_help="Summarize .wiki/ in the current directory.",
        public_help=f"Resolve --wiki <slug>{PRIVATE_SUFFIX} to the public sibling <slug>.",
        include_archived_help="Allow summarizing an explicitly targeted archived wiki.",
    )
    status.add_argument("--json", action="store_true", help="Print a machine-readable JSON overview.")
    status.set_defaults(func=run)
    return status
