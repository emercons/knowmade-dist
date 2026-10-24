"""``lint --all``: the single-root lint over every wiki root of a hub.

Each root runs through ``lint.run_lint``, the same path ``llm-wiki lint <root>``
takes, with a fresh ``LintContext`` per root; its JSON report is captured and
aggregated, so a root is judged exactly as it would be alone and ``--fail-on``
means the same thing. ``--all`` never repairs: ``--fix`` and the fix-class
flags (``--preflight``, ``--fix-only``, ``--no-*``, ``--refuse-on``) are
rejected, because a repair run over many roots at once cannot be reviewed
(emercons/knowmade#113, #111).

Discovery, in order, without duplicates (the first source of a path wins):

1. the entries of the hub registry ``wikis.json`` (archived entries are left
   out), which includes the ``hub`` entry, the hub root itself, each at the
   path ``HUB/wikis.local.json`` gives it on this machine;
2. every ``topics/<slug>/`` directory;
3. with ``--include-nested-wiki``, every ``topics/<slug>/.wiki/`` directory
   that holds an ``_index.md``.

A registry path or ``topics/<slug>/`` without an ``_index.md`` whose
``.wiki/`` holds one is that topic's wiki root (``roots.registry_entry_path``,
``roots.wiki_root_of``), linted under the topic's slug without
``--include-nested-wiki``; the flag only adds a ``.wiki/`` nested inside a
topic that is a wiki itself, as ``<slug>/.wiki``.

A candidate without an ``_index.md`` (the hub's stub private directories, or a
registry path that is not there) is reported as skipped, not linted;
``--skip-private`` additionally skips every ``*-PRIVATE`` root. With
``--report-dir`` one ``<slug>.json`` per linted root and a ``summary.json`` are
written to that directory and nowhere else.

Exit codes: 0 every linted root passes ``--fail-on``, 1 one does not, 2 usage
or configuration error or a root that could not be linted.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import fsio as _fsio
from .common import PRIVATE_SUFFIX, read_registry
from .roots import (
    NESTED_WIKI_DIR as NESTED_WIKI,
    initialized_wiki_root,
    is_archived_registry_entry,
    read_local_registry,
    registry_entry_path,
    resolve_hub_args,
    wiki_root_of,
)

SUMMARY_NAME = "summary"
SEVERITIES = ("critical", "warning", "suggestion", "info")


class MultiLintError(Exception):
    """A usage or configuration error: exit code 2."""


@dataclass
class Candidate:
    slug: str
    root: Path
    source: str


def discover_roots(hub: Path, *, include_nested_wiki: bool) -> list[Candidate]:
    """Every candidate root of ``hub`` in discovery order, without duplicates."""
    found: list[Candidate] = []
    seen: set[Path] = set()

    def add(slug: str, root: Path, source: str) -> None:
        key = root.resolve(strict=False)
        if key not in seen:
            seen.add(key)
            found.append(Candidate(slug, root, source))

    if (hub / "wikis.json").is_file():
        try:
            wikis = read_registry(hub).get("wikis", {})
            overlay = read_local_registry(hub)
        except SystemExit as exc:
            raise MultiLintError(str(exc.code)) from None
        for slug, entry in sorted(wikis.items()):
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                continue
            if is_archived_registry_entry(entry):
                continue
            add(slug, registry_entry_path(hub, slug, entry, overlay), "registry")

    topics = hub / "topics"
    if topics.is_dir():
        for topic in sorted(path for path in topics.iterdir() if path.is_dir()):
            if topic.name.startswith("."):
                continue
            add(topic.name, wiki_root_of(topic), "topics")
            nested = topic / NESTED_WIKI
            if include_nested_wiki and nested.is_dir():
                add(f"{topic.name}/{NESTED_WIKI}", nested, "nested")
    return found


def is_private_candidate(candidate: Candidate) -> bool:
    return candidate.slug.endswith(PRIVATE_SUFFIX) or any(
        part.endswith(PRIVATE_SUFFIX) for part in (candidate.root.name, candidate.root.parent.name)
    )


def lint_one(root: Path, args: argparse.Namespace) -> dict[str, Any]:
    """Run the single-root lint on ``root`` and return its parsed JSON report."""
    from .lint import run_lint

    fields = dict(vars(args))
    fields.update(
        path=str(root), all=False, include_nested_wiki=False, skip_private=False,
        report_dir=None, local=False, wiki=None, hub=None, private=False, public=False,
        include_archived=False, fix=False, json=True, preflight=False, fix_only=None,
        no_quarantine=False, no_log=False, refuse_on=None,
    )
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        run_lint(argparse.Namespace(**fields))
    return json.loads(captured.getvalue())


def lint_candidate(candidate: Candidate, args: argparse.Namespace) -> dict[str, Any]:
    entry: dict[str, Any] = {"slug": candidate.slug, "root": str(candidate.root)}
    if args.skip_private and is_private_candidate(candidate):
        return {**entry, "status": "skipped", "reason": "private", "counts": {}, "issues": []}
    if not initialized_wiki_root(candidate.root):
        reason = "no _index.md" if candidate.root.is_dir() else "not found"
        return {**entry, "status": "skipped", "reason": reason, "counts": {}, "issues": []}
    try:
        report = lint_one(candidate.root, args)
    except SystemExit as exc:
        return {**entry, "status": "error", "reason": str(exc.code), "counts": {}, "issues": []}
    except json.JSONDecodeError:
        return {**entry, "status": "error", "reason": "no lint report", "counts": {}, "issues": []}
    return {
        **entry,
        "status": report["status"],
        "counts": report["counts"],
        "issues": report["issues"],
    }


def aggregate_counts(roots: list[dict[str, Any]]) -> dict[str, int]:
    counts = {severity: 0 for severity in SEVERITIES}
    for root in roots:
        for severity in SEVERITIES:
            counts[severity] += int(root["counts"].get(severity, 0))
    counts["roots"] = len(roots)
    counts["linted"] = sum(1 for root in roots if root["status"] in {"pass", "fail"})
    counts["skipped"] = sum(1 for root in roots if root["status"] == "skipped")
    counts["failed"] = sum(1 for root in roots if root["status"] == "fail")
    counts["errors"] = sum(1 for root in roots if root["status"] == "error")
    return counts


def report_file_names(roots: list[dict[str, Any]]) -> dict[str, str]:
    """Map each linted root's path to a unique, filesystem-safe ``<name>.json``."""
    names: dict[str, str] = {}
    used = {SUMMARY_NAME}
    for root in roots:
        if root["status"] not in {"pass", "fail"}:
            continue
        base = re.sub(r"[^A-Za-z0-9_-]+", "-", root["slug"]).strip("-") or "root"
        name, number = base, 2
        while name in used:
            name, number = f"{base}-{number}", number + 1
        used.add(name)
        names[root["root"]] = f"{name}.json"
    return names


def write_reports(report_dir: Path, summary: dict[str, Any]) -> None:
    names = report_file_names(summary["roots"])
    report_dir.mkdir(parents=True, exist_ok=True)
    for root in summary["roots"]:
        name = names.get(root["root"])
        if name:
            _fsio.write_json_atomic(report_dir / name, root)
    _fsio.write_json_atomic(report_dir / f"{SUMMARY_NAME}.json", summary)


def print_text(summary: dict[str, Any]) -> None:
    for root in summary["roots"]:
        counts = root["counts"]
        if root["status"] in {"pass", "fail"}:
            detail = (
                f"{counts.get('critical', 0)} critical, {counts.get('warning', 0)} warnings, "
                f"{counts.get('suggestion', 0)} suggestions, {counts.get('info', 0)} info"
            )
        else:
            detail = root.get("reason", "")
        print(f"{root['status'].upper():7} {root['slug']}  {detail}  ({root['root']})")
    totals = summary["counts"]
    print(
        f"lint --all: {totals['linted']} linted, {totals['skipped']} skipped, "
        f"{totals['failed']} failed, {totals['errors']} errors; "
        f"{totals['critical']} critical, {totals['warning']} warnings, "
        f"{totals['suggestion']} suggestions, {totals['info']} info"
    )


def wants_multi(args: argparse.Namespace) -> bool:
    """True when any ``--all`` flag is set, so a stray one is an error, not ignored."""
    return bool(
        getattr(args, "all", False)
        or getattr(args, "include_nested_wiki", False)
        or getattr(args, "skip_private", False)
        or getattr(args, "report_dir", None)
    )


def run_all(args: argparse.Namespace) -> int:
    try:
        return _run_all(args)
    except MultiLintError as exc:
        print(f"llm-wiki lint --all: {exc}", file=sys.stderr)
        return 2


def _run_all(args: argparse.Namespace) -> int:
    if not args.all:
        raise MultiLintError("--include-nested-wiki, --skip-private and --report-dir need --all")
    if args.fix:
        raise MultiLintError("--all never repairs: it cannot be combined with --fix")
    fix_flags = ("preflight", "fix_only", "no_quarantine", "no_log", "refuse_on")
    if any(getattr(args, name, None) for name in fix_flags):
        raise MultiLintError(
            "--all never repairs: it cannot be combined with --preflight/--plan, "
            "--fix-only, --no-quarantine, --no-log or --refuse-on"
        )
    if args.path or args.local or args.wiki:
        raise MultiLintError("--all lints a whole hub: use --hub, not a path, --local or --wiki")
    try:
        hub = resolve_hub_args(args).resolve(strict=False)
    except SystemExit as exc:
        raise MultiLintError(str(exc.code)) from None
    if not hub.is_dir():
        raise MultiLintError(f"hub is not a directory: {hub}")

    candidates = discover_roots(hub, include_nested_wiki=args.include_nested_wiki)
    roots = [lint_candidate(candidate, args) for candidate in candidates]
    summary = {"hub": str(hub), "roots": roots, "counts": aggregate_counts(roots)}
    if args.report_dir:
        write_reports(Path(args.report_dir), summary)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print_text(summary)
    if summary["counts"]["errors"]:
        return 2
    return 1 if summary["counts"]["failed"] else 0
