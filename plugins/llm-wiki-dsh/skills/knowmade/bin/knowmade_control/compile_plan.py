"""The ``compile`` namespace: ``compile plan``, the read-only source survey (emercons/knowmade#83).

``compile plan`` is steps 0-2 of ``commands/compile.md`` and
``references/compilation.md`` § Step 1 made deterministic: which raw sources
still need compiling, what the existing articles are (the input of the
map-to-existing step), what the placement pre-check would fix and whether a
topic guide exists. It decides nothing about content; the agent reads the
``to_compile`` sources and writes the articles.

Nothing is computed twice. The document set is the one lint loads
(``lint_docs.load_documents``), a source counts as cited when lint's own
``resolve_source_ref`` resolves one of an article's ``sources:`` to it (the
test ``check_coverage`` applies for C6), and the placement and alias findings
are what ``check_canonical_placement`` (C11), ``check_unknown_files`` (C12, files
loose at the root) and ``fix_frontmatter_aliases`` (C13) report in a run that
cannot repair. Nothing is written, planned or
logged; a hub root is not a topic and is refused.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from . import rules as _rules
from .common import PRIVATE_SUFFIX, add_scope_arguments, as_string_list, first_body_summary, is_under
from .lint_docs import (
    COVERAGE_REFERENCE as COVERAGE_NAME,
    check_canonical_placement,
    check_unknown_files,
    content_markdown_files,
    fix_frontmatter_aliases,
    load_documents,
    resolve_source_ref,
)
from .lint_hub import is_private_placeholder
from .lint_report import Document, LintContext
from .roots import is_hub, resolve_wiki_root_args
from .schema import schema_state_from_text

SCHEMA = "knowmade/compile-plan/v1"

# The lint-generated backlog (``wiki/references/<COVERAGE_NAME>``) lists
# uncompiled sources without compiling them: it is not an article (lint leaves
# it out of the article graph, by file name, as this does) and it must not count
# as a citation here (lint counts it, which is why a source in the backlog stops
# being a C6 finding).
# Tag of a raw collection manifest: lint exempts it from the C6 coverage check.
MANIFEST_TAG = "collection-manifest"
# The checks whose findings the compile placement pre-check (step 0) acts on:
# C11 (a misplaced file; ``lint --fix-only placement`` moves it), C12 (a loose
# file at the wiki root; ``lint --fix-only quarantine`` moves it to
# inbox/.unknown/) and C13 (legacy frontmatter; by hand from the C13 table).
PLACEMENT_CHECKS = {"C11", "C12", "C13"}
PLACEMENT_DIRS = {"raw", "wiki", "inventory"}
# "Last compiled" in a list item, bold or plain, a colon on either side of the
# emphasis, or a table row, followed by an ISO date (a time after it is ignored).
LAST_COMPILED = re.compile(
    r"^[ \t]*(?:[-*+][ \t]+|\|[ \t]*)?[*_]*Last compiled[*_]*[ \t]*[:|]?[ \t]*[*_]*[ \t]*\|?[ \t]*(\d{4}-\d{2}-\d{2})(?!\d)",
    re.I | re.M,
)
COMPILE_GUIDANCE = re.compile(r"^#{1,6}\s+Compile Guidance\b", re.I | re.M)

# Source statuses, in report order.
STATUSES = ("uncompiled", "new", "compiled", "exempt")


def last_compiled(root: Path) -> str | None:
    """The "Last compiled" date of the master ``_index.md``, or ``None`` when it has none."""
    try:
        text = (root / "_index.md").read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None
    match = LAST_COMPILED.search(text)
    return match.group(1) if match else None


def cited_by(ctx: LintContext) -> dict[Path, list[str]]:
    """Per raw file, the articles under ``wiki/`` whose ``sources:`` resolve to it."""
    cited: dict[Path, list[str]] = {}
    raw_dir = ctx.root / "raw"
    for doc in sorted(ctx.documents.values(), key=lambda item: str(item.path)):
        rel = ctx.rel(doc.path)
        if not rel.startswith("wiki/") or doc.path.name == COVERAGE_NAME:
            continue
        sources = doc.frontmatter.get("sources")
        if not isinstance(sources, list):
            continue
        for source in sources:
            resolved = resolve_source_ref(ctx, doc.path, str(source), wiki_source=True)
            if resolved is not None and is_under(resolved, raw_dir):
                owners = cited.setdefault(resolved.resolve(), [])
                if rel not in owners:
                    owners.append(rel)
    return cited


def source_status(doc: Document, owners: list[str], compiled_on: str | None) -> str:
    if MANIFEST_TAG in as_string_list(doc.frontmatter.get("tags")):
        return "exempt"
    if not owners:
        return "uncompiled"
    ingested = str(doc.frontmatter.get("ingested") or "")[:10]
    return "new" if compiled_on and ingested > compiled_on else "compiled"


def source_rows(ctx: LintContext, compiled_on: str | None) -> list[dict[str, Any]]:
    """Every raw source with its frontmatter facts, its citing articles and its status."""
    cited = cited_by(ctx)
    rows = []
    for path in content_markdown_files(ctx.root / "raw"):
        doc = ctx.documents.get(path.resolve())
        if doc is None:
            continue
        owners = cited.get(path.resolve(), [])
        rel = ctx.rel(path)
        parts = rel.split("/")
        rows.append(
            {
                "path": rel,
                "type": str(doc.frontmatter.get("type") or (parts[1] if len(parts) > 2 else "")) or None,
                "title": str(doc.frontmatter.get("title") or path.stem),
                "ingested": str(doc.frontmatter.get("ingested") or "") or None,
                "cited_by": owners,
                "status": source_status(doc, owners, compiled_on),
            }
        )
    return rows


def unreadable_sources(ctx: LintContext) -> list[str]:
    """Raw files lint could not load (not UTF-8, no frontmatter): lint reports them as C1."""
    return [ctx.rel(path) for path in content_markdown_files(ctx.root / "raw") if path.resolve() not in ctx.documents]


def article_rows(ctx: LintContext) -> list[dict[str, Any]]:
    """The existing articles: the input of the map-to-existing step."""
    rows = []
    for doc in sorted(ctx.documents.values(), key=lambda item: str(item.path)):
        rel = ctx.rel(doc.path)
        if not rel.startswith("wiki/") or doc.path.name == COVERAGE_NAME:
            continue
        fields = doc.frontmatter
        summary = str(fields.get("summary") or fields.get("abstract") or "").strip() or first_body_summary(doc.body)
        sources = fields.get("sources")
        rows.append(
            {
                "path": rel,
                "title": str(fields.get("title") or doc.path.stem),
                "category": str(fields.get("category") or fields.get("type") or "") or None,
                "tags": as_string_list(fields.get("tags")),
                "summary": summary.splitlines()[0] if summary else "",
                "sources": len(sources) if isinstance(sources, list) else 0,
            }
        )
    return rows


def placement_action(issue: Any) -> str:
    """What lint really does with a placement finding, in one line."""
    if issue.check == "C11":
        if issue.fixable:
            return "moved to its canonical path by `lint --fix --fix-only placement`"
        return "not moved (the destination exists); resolve by hand"
    if issue.check == "C12":
        return (
            "moved to inbox/.unknown/ by `lint --fix --fix-only quarantine`; "
            "triage it through ingest, it is not compiled"
        )
    return "by hand from the C13 table"


def placement_issues(ctx: LintContext) -> list[dict[str, str]]:
    """What compile step 0 acts on: C11 misplaced files, C12 loose files at the root, C13 legacy frontmatter."""
    rows = []
    for issue in ctx.active_issues():
        if issue.check not in PLACEMENT_CHECKS or issue.path is None:
            continue
        rel = ctx.rel(issue.path)
        parts = rel.split("/")
        if issue.check == "C11" and parts[0] not in PLACEMENT_DIRS:
            continue  # a dataset manifest naming another dataset_id is not a placement problem
        if issue.check == "C12" and (len(parts) != 1 or issue.path.is_dir()):
            continue  # only a file loose at the wiki root; lint's other unknown-file findings are not compile's
        rows.append(
            {
                "check": issue.check,
                "severity": issue.severity,
                "path": rel,
                "message": issue.message,
                "action": placement_action(issue),
            }
        )
    return rows


def placement_commands(root: Path, issues: list[dict[str, str]]) -> dict[str, Any]:
    """The narrowed lint command, its preview and their argv when ``issues`` hold a C11 move, else ``None``s.

    The text forms are POSIX-shell safe (``shlex.quote``); the argv forms need no shell at all.
    """
    if not any(row["check"] == "C11" and row["action"].startswith("moved") for row in issues):
        return {"fix": None, "preview": None, "fix_argv": None, "preview_argv": None}
    argv = ["llm-wiki", "lint", str(root), "--fix", "--fix-only", "placement"]
    return {
        "fix": shlex.join(argv),
        "preview": shlex.join([*argv, "--preflight"]),
        "fix_argv": argv,
        "preview_argv": [*argv, "--preflight"],
    }


def topic_guide(root: Path) -> dict[str, Any]:
    """The topic guide state; ``readable`` tells a guide that cannot be read from one that is absent."""
    schema = root / "schema.md"
    try:
        text = schema.read_text(encoding="utf-8-sig", errors="replace")
    except (FileNotFoundError, NotADirectoryError):
        return {"exists": False, "readable": False, "state": None, "compile_guidance": False}
    except OSError:
        return {"exists": True, "readable": False, "state": None, "compile_guidance": False}
    return {
        "exists": True,
        "readable": True,
        "state": schema_state_from_text(text),
        "compile_guidance": bool(COMPILE_GUIDANCE.search(text)),
    }


def select_source(ctx: LintContext, rows: list[dict[str, Any]], requested: str) -> dict[str, Any] | None:
    """The row for ``--source``: an absolute path, or a path against the wiki root first, then the current directory."""
    given = Path(requested.replace("\\", "/")).expanduser()
    for candidate in [given] if given.is_absolute() else [ctx.root / given, given]:
        try:
            wanted = ctx.rel(candidate.resolve())
        except (OSError, ValueError):
            continue
        row = next((row for row in rows if row["path"] == wanted), None)
        if row is not None:
            return row
    return None


def plan_payload(root: Path, *, full: bool = False, source: str | None = None) -> dict[str, Any]:
    """The whole survey of the topic wiki at ``root``; reads, never writes.

    Raises ``ValueError`` when ``source`` is not a raw source of the wiki.
    """
    ctx = LintContext(root)
    load_documents(ctx)
    fix_frontmatter_aliases(ctx)
    check_canonical_placement(ctx)
    check_unknown_files(ctx)
    compiled_on = last_compiled(ctx.root)
    sources = source_rows(ctx, compiled_on)
    if source is not None:
        chosen = select_source(ctx, sources, source)
        if chosen is None:
            raise ValueError(f"not a raw source of this wiki: {source}")
        mode, planned = "source", [chosen]
    elif full:
        mode, planned = "full", [row for row in sources if row["status"] != "exempt"]
    else:
        mode, planned = "incremental", [row for row in sources if row["status"] in {"uncompiled", "new"}]
    counts = Counter(row["status"] for row in sources)
    issues = placement_issues(ctx)
    return {
        "schema": SCHEMA,
        "ok": True,
        "root": str(ctx.root),
        "title": str((_rules.config_fields(root) or {}).get("title") or root.name),
        "private": ctx.root.parent.name.endswith(PRIVATE_SUFFIX) or ctx.root.name.endswith(PRIVATE_SUFFIX),
        "mode": mode,
        "last_compiled": compiled_on,
        "counts": {status: counts[status] for status in STATUSES},
        "sources": sources,
        "unreadable_sources": unreadable_sources(ctx),
        "to_compile": [{"path": row["path"], "title": row["title"], "status": row["status"]} for row in planned],
        "articles": article_rows(ctx),
        "placement": {"issues": issues, **placement_commands(ctx.root, issues)},
        "topic_guide": topic_guide(ctx.root),
    }


def print_plan(payload: dict[str, Any]) -> None:
    counts = payload["counts"]
    guide = payload["topic_guide"]
    issues = payload["placement"]["issues"]
    planned = payload["to_compile"]
    print(f"Compile plan: {payload['title']}{' [private]' if payload['private'] else ''}")
    print(f"Root: {payload['root']}")
    print(f"Mode:      {payload['mode']} (last compiled {payload['last_compiled'] or 'never'})")
    print(
        f"Sources:   {len(payload['sources'])} (uncompiled {counts['uncompiled']}, new {counts['new']}, "
        f"compiled {counts['compiled']}, exempt {counts['exempt']})"
    )
    print(f"Articles:  {len(payload['articles'])}")
    if payload["mode"] == "incremental" and payload["last_compiled"] is None:
        print(
            'Note: no "Last compiled" date was found in _index.md, so incremental mode can only plan '
            "uncompiled sources, not sources ingested after the last compile."
        )
    if not guide["exists"]:
        print("Topic guide: none")
    elif not guide["readable"]:
        print("Topic guide: schema.md exists but cannot be read")
    else:
        print(f"Topic guide: schema.md ({guide['state']}){', with Compile Guidance' if guide['compile_guidance'] else ''}")
    unreadable = payload["unreadable_sources"]
    if unreadable:
        print(
            f"Unreadable: {len(unreadable)} raw file(s) have no readable frontmatter (lint C1) "
            "and must be fixed before they can be compiled:"
        )
        for path in unreadable:
            print(f"  {path}")
    if issues:
        print(f"Placement: {len(issues)} issue(s)")
        for issue in issues:
            print(f"  {issue['check']} {issue['path']}: {issue['message']} ({issue['action']})")
        if payload["placement"]["fix"]:
            print(f"  apply the moves: {payload['placement']['fix']}  (preview: add --preflight)")
    if not planned:
        if unreadable:
            print("Nothing to compile until the unreadable raw files are fixed.")
        elif payload["mode"] == "incremental":
            print("Nothing to compile. All sources are already compiled; use --full to recompile everything.")
        else:
            print("Nothing to compile.")
        return
    print(f"To compile ({len(planned)}):")
    width = max(len(row["status"]) for row in planned)
    for row in planned:
        print(f"  {row['status']:<{width}}  {row['path']}  {row['title']}")


def run_plan(args: argparse.Namespace) -> int:
    root = resolve_wiki_root_args(args)
    if not root.is_dir():
        print(f"compile plan: no wiki root at {root}", file=sys.stderr)
        return 2
    if is_hub(root):
        print(
            f"compile plan: {root} is a hub, not a topic wiki; pass --wiki <slug> or a topic path "
            "(`llm-wiki archive list` and `llm-wiki hub index` cover the hub)",
            file=sys.stderr,
        )
        return 2
    if is_private_placeholder(root):
        print(f"compile plan: {root} is a private placeholder, not checked out here", file=sys.stderr)
        return 2
    try:
        payload = plan_payload(root, full=args.full, source=args.source)
    except ValueError as exc:
        print(f"compile plan: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print_plan(payload)
    return 0


def run(args: argparse.Namespace) -> int:
    """Dispatch a parsed ``compile`` namespace (``plan`` is its only verb)."""
    return run_plan(args)


def register_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Register the ``compile`` namespace on a parent ``llm-wiki`` parser."""
    compile_ = subparsers.add_parser(
        "compile",
        help="Deterministic survey for the compile step of a topic wiki.",
        description="Deterministic front over the survey half of /knowmade:compile (no LLM). Works on a topic wiki, never a hub.",
    )
    sub = compile_.add_subparsers(dest="compile_command", required=True)
    plan = sub.add_parser(
        "plan",
        help="List the raw sources to compile and the articles that exist; read-only.",
        description=(
            "Read-only. Reports the master index's \"Last compiled\" date; every raw source with its "
            "type, title, ingested date, the articles citing it (the lint C6 resolution) and a status "
            "(uncompiled: no article cites it; new: cited but ingested after the last compile; compiled; "
            "exempt: a collection manifest); the to_compile list for the chosen mode; the existing "
            "articles (category, tags, summary, source count; not lint's coverage backlog); the C11 "
            "misplaced files, C12 loose files at the root and C13 legacy-frontmatter findings with what "
            "lint does about each (this verb repairs nothing); raw files without readable frontmatter; "
            "and whether a topic guide (schema.md) exists and is readable. Exit 0, or 2 when the root is missing, is a hub or a private "
            "placeholder, or --source is not a raw source of the wiki."
        ),
    )
    plan.add_argument("path", nargs="?", help="Topic wiki root to survey.")
    add_scope_arguments(
        plan,
        local_help="Survey .wiki/ in the current directory.",
        public_help=f"Resolve --wiki <slug>{PRIVATE_SUFFIX} to the public sibling <slug>.",
        include_archived_help="Allow surveying an explicitly targeted archived wiki.",
    )
    mode = plan.add_mutually_exclusive_group()
    mode.add_argument(
        "--full",
        action="store_true",
        help="Plan every source (except collection manifests), not only the uncompiled and new ones.",
    )
    mode.add_argument(
        "--source",
        help="Plan only this raw source (a path relative to the wiki root, or an absolute path).",
    )
    plan.add_argument("--json", action="store_true", help="Print the full machine-readable plan (carries ok: true).")
    compile_.set_defaults(func=run)
    return compile_
