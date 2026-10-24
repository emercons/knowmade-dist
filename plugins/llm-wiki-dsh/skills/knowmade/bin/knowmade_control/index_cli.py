"""The ``index`` namespace: ``index check`` and ``index rebuild`` (emercons/knowmade#83).

A thin front over the index contract ``lint`` already enforces (C3, plus the
missing-``_index.md`` half of C1): the same directory selection, the same
required entries, dead links and stale rows, the same renderers and the same
fix gate. Nothing here renders or compares an index itself.

``check`` runs the index steps read-only and lists what it finds (exit 1 when
anything is found). ``rebuild`` runs them again as ``lint --fix`` would for
the ``index`` class (and creates a missing ``_index.md`` of a directory that
exists, which lint files under ``create``), prints what changed and exits 1
only when a finding is left that no repair writes. ``--dry-run`` plans the
same writes through ``--preflight``'s gate and writes nothing.

Master-index statistics ("Last compiled", "Last lint", the counts) are not
touched: lint reports a drifting count only as an agent-pass finding
(``index-stats-drift``) and the dates are stamped by the operation that ran,
so neither has a deterministic rewrite to wrap.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .common import PRIVATE_SUFFIX, add_scope_arguments
from .lint_docs import (
    REQUIRED_INDEX_DIRECTORIES,
    check_index_consistency,
    check_index_rows,
    ensure_dir_index,
    load_documents,
    optional_index_directories,
)
from .lint_hub import is_private_placeholder
from .lint_report import LintContext, PlannedFix
from .roots import is_hub, resolve_wiki_root_args

CHECK_SCHEMA = "knowmade/index-check/v1"
REBUILD_SCHEMA = "knowmade/index-rebuild/v1"

# Finding kinds, in the order they are listed. Each is recognised from the
# message lint raises for it; the tests pin every one.
KINDS = ("missing-index", "missing-entry", "dead-link", "stale-row")


def classify(message: str) -> str | None:
    """The index finding kind of a lint message, or ``None`` for any other finding."""
    if message.startswith("Required _index.md is missing"):
        return "missing-index"
    if "entry is missing from directory index" in message:
        return "missing-entry"
    if message.startswith("Index links to missing file"):
        return "dead-link"
    if "(index-row-stale)" in message:
        return "stale-row"
    return None


def _create_missing_indexes(ctx: LintContext) -> None:
    """The structure step's index half: an existing directory that lacks its ``_index.md``.

    A missing directory is not an index finding (``lint --fix`` creates it
    under ``create``), and neither is a missing master ``_index.md``, which no
    repair writes.
    """
    directories = [ctx.root / rel for rel in REQUIRED_INDEX_DIRECTORIES]
    directories += optional_index_directories(ctx.root)
    for directory in directories:
        if directory.is_dir() and not (directory / "_index.md").exists():
            ensure_dir_index(ctx, directory)


def run_index_steps(ctx: LintContext) -> None:
    """The index steps of a lint run, in lint's order."""
    load_documents(ctx)
    _create_missing_indexes(ctx)
    check_index_consistency(ctx)
    check_index_rows(ctx)


def findings_of(ctx: LintContext) -> list[dict[str, Any]]:
    """The index findings of a finished run (load errors and other checks are not ours)."""
    found: list[dict[str, Any]] = []
    for issue in ctx.active_issues():
        kind = classify(issue.message)
        if kind is None:
            continue
        found.append(
            {
                "kind": kind,
                "check": issue.check,
                "path": ctx.rel(issue.path) if issue.path else None,
                "message": issue.message,
                "fixable": issue.fixable,
            }
        )
    found.sort(key=lambda item: (KINDS.index(item["kind"]), item["path"] or "", item["message"]))
    return found


def planned_json(ctx: LintContext, item: PlannedFix) -> dict[str, str]:
    return {
        "action": item.action,
        "check": item.check,
        "fix_class": item.fix_class,
        "path": ctx.rel(item.path),
    }


def _context(root: Path, *, fix: bool = False, preflight: bool = False) -> LintContext:
    return LintContext(root, fix=fix, preflight=preflight, fix_classes={"index", "create"})


def _counts(findings: list[dict[str, Any]]) -> dict[str, int]:
    return {kind: sum(1 for item in findings if item["kind"] == kind) for kind in KINDS}


def _print_findings(findings: list[dict[str, Any]], indent: str = "  ") -> None:
    for item in findings:
        suffix = "" if item["fixable"] else " (needs a hand edit)"
        print(f"{indent}{item['kind']:<13} {item['path']}: {item['message']}{suffix}")


def _resolve(args: argparse.Namespace, label: str) -> Path | None:
    root = resolve_wiki_root_args(args)
    if not root.is_dir():
        print(f"{label}: no wiki root at {root}", file=sys.stderr)
        return None
    if is_hub(root):
        print(
            f"{label}: {root} is a hub; pass --wiki <slug>, --local or a topic path "
            "(the hub's own index is `llm-wiki hub index`)",
            file=sys.stderr,
        )
        return None
    return root


def _emit(args: argparse.Namespace, payload: dict[str, Any]) -> None:
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))


def run_check(args: argparse.Namespace) -> int:
    root = _resolve(args, "index check")
    if root is None:
        return 2
    if is_private_placeholder(root):
        payload = {"schema": CHECK_SCHEMA, "root": str(root), "status": "skipped", "findings": [], "counts": _counts([])}
        if args.json:
            _emit(args, payload)
        else:
            print(f"index check: {root} is a private placeholder, not checked out here; nothing to check.")
        return 0
    ctx = _context(root)
    run_index_steps(ctx)
    findings = findings_of(ctx)
    payload = {
        "schema": CHECK_SCHEMA,
        "root": str(ctx.root),
        "status": "dirty" if findings else "clean",
        "counts": _counts(findings),
        "findings": findings,
    }
    if args.json:
        _emit(args, payload)
    elif findings:
        print(f"index check: {len(findings)} finding(s) in {ctx.root}")
        _print_findings(findings)
        print("Run `llm-wiki index rebuild` (add --dry-run to preview) to repair them.")
    else:
        print(f"index check: indexes are current in {ctx.root}")
    return 1 if findings else 0


def rebuild_indexes(root: Path) -> tuple[list[str], list[dict[str, Any]]]:
    """``index rebuild`` for callers that embed it: ``(changes made, findings left)``.

    Same steps and the same writes as ``run_rebuild`` without ``--dry-run``;
    a private placeholder is left alone.
    """
    if is_private_placeholder(root):
        return [], []
    before = _context(root)
    run_index_steps(before)
    if not findings_of(before):
        return [], []
    ctx = _context(root, fix=True)
    run_index_steps(ctx)
    return list(ctx.fixes), findings_of(ctx)


def run_rebuild(args: argparse.Namespace) -> int:
    root = _resolve(args, "index rebuild")
    if root is None:
        return 2
    if is_private_placeholder(root):
        payload = {
            "schema": REBUILD_SCHEMA, "root": str(root), "status": "skipped", "dry_run": args.dry_run,
            "findings": [], "changes": [], "planned": [], "remaining": [],
        }
        if args.json:
            _emit(args, payload)
        else:
            print(f"index rebuild: {root} is a private placeholder, not checked out here; nothing to rebuild.")
        return 0
    before = _context(root)
    run_index_steps(before)
    found = findings_of(before)
    payload: dict[str, Any] = {
        "schema": REBUILD_SCHEMA,
        "root": str(before.root),
        "dry_run": bool(args.dry_run),
        "findings": found,
        "changes": [],
        "planned": [],
        "remaining": [],
    }
    if not found:
        payload["status"] = "clean"
        if args.json:
            _emit(args, payload)
        else:
            print(f"index rebuild: indexes are current in {before.root}; nothing to rebuild.")
        return 0

    ctx = _context(root, fix=not args.dry_run, preflight=bool(args.dry_run))
    run_index_steps(ctx)
    if args.dry_run:
        payload["status"] = "dry-run"
        payload["planned"] = [planned_json(ctx, item) for item in ctx.plan]
        if args.json:
            _emit(args, payload)
        else:
            print(f"index rebuild --dry-run: {len(found)} finding(s) in {ctx.root}; nothing written.")
            _print_findings(found)
            for item in ctx.plan:
                print(f"PLAN {item.fix_class} {item.action}: {ctx.rel(item.path)}")
        return 0

    remaining = findings_of(ctx)
    payload["changes"] = list(ctx.fixes)
    payload["remaining"] = remaining
    payload["status"] = "incomplete" if remaining else "rebuilt"
    if args.json:
        _emit(args, payload)
    else:
        print(f"index rebuild: {len(ctx.fixes)} change(s) in {ctx.root}")
        for change in ctx.fixes:
            print(f"  {change}")
        if remaining:
            print(f"{len(remaining)} finding(s) left that no repair writes:")
            _print_findings(remaining)
    return 1 if remaining else 0


def _add_target(parser: argparse.ArgumentParser, verb: str) -> None:
    parser.add_argument("path", nargs="?", help=f"Wiki root path to {verb}.")
    add_scope_arguments(
        parser,
        local_help=f"{verb.capitalize()} .wiki/ in the current directory.",
        public_help=f"Resolve --wiki <slug>{PRIVATE_SUFFIX} to the public sibling <slug>.",
        include_archived_help=f"Allow {verb} on an explicitly targeted archived wiki.",
    )
    parser.add_argument("--json", action="store_true", help="Print a machine-readable JSON report.")


def register_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Register the ``index`` namespace on a top-level subparsers action."""
    index = subparsers.add_parser(
        "index",
        help="Check or rebuild the generated _index.md files of a topic wiki.",
        description=(
            "Deterministic front over the index contract lint enforces (no LLM). Works on a topic "
            "wiki, never a hub. Master-index statistics and dates are not touched."
        ),
    )
    sub = index.add_subparsers(dest="index_command", required=True)
    check = sub.add_parser(
        "check",
        help="List missing, dead and stale index entries; read-only.",
        description=(
            "Read-only. Lists directories lacking an _index.md, required entries an index does not "
            "link, dead index links and generated rows that differ from the article frontmatter. "
            "Exit 0 when clean, 1 when anything is found, 2 on a missing or hub root."
        ),
    )
    _add_target(check, "check")
    rebuild = sub.add_parser(
        "rebuild",
        help="Apply the index repairs lint --fix would make, for indexes only.",
        description=(
            "Regenerates a generated or stub index, repairs a curated one in place, refreshes stale "
            "generated rows and writes a missing _index.md for a directory that exists. Writes no "
            "log.md entry. Exit 0 when clean afterwards, 1 when a finding is left that no repair "
            "writes (a dead link inside prose), 2 on a missing or hub root."
        ),
    )
    _add_target(rebuild, "rebuild")
    rebuild.add_argument(
        "--dry-run",
        action="store_true",
        help="List the findings and the writes a rebuild would make, and write nothing.",
    )
    index.set_defaults(func=run)
    return index


def run(args: argparse.Namespace) -> int:
    """Dispatch a parsed ``index`` namespace."""
    return run_check(args) if args.index_command == "check" else run_rebuild(args)
