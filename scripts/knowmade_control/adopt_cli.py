"""Brownfield adoption: the ``llm-wiki adopt`` namespace and the ``llm-wiki-adopt`` scanner.

``adoption_plan`` holds the scanner, classifier and batch writer;
``adoption_batch`` the resumable claim/complete/verify state machine. This
module owns both argument-parser fronts over them and the root selection, so
``llm-wiki adopt scan|apply`` and the standalone ``llm-wiki-adopt`` executable
(:func:`scan_main`) share one deterministic path model.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
from pathlib import Path
from typing import Any

from . import adoption_batch
from . import hub as _hub
from . import rules as _rules
from .adoption_plan import (
    DEFAULT_HASH_LIMIT,
    DEFAULT_MAX_FILES,
    DEFAULT_SMALL_DATA_LIMIT,
    ensure_local_wiki,
    is_knowmade_checkout,
    make_batch_id,
    scan_project,
    summary,
    write_batch,
)
from .roots import resolve_project_wiki_roots
from .stdio import configure_utf8_stdio

ADOPT_HELP = "Scan, apply, resume, and verify deterministic brownfield adoption."


def add_gitignore_option(parser: argparse.ArgumentParser) -> None:
    """The ``--no-gitignore`` opt-out, shared by ``llm-wiki-adopt`` and ``llm-wiki adopt``."""
    parser.add_argument(
        "--no-gitignore",
        action="store_true",
        help="With --apply, do not append the wiki directory to the project's .gitignore.",
    )


def ensure_wiki_ignored(project_root: Path, wiki_root: Path, opt_out: bool = False) -> dict[str, Any]:
    """Keep an adopted ``.wiki/`` out of the project's git history (knowmade#95).

    The adopted tree holds staged copies of the adopted material and batch
    state, so a plain ``git add -A`` in the project would publish it. Inside a
    git work tree whose ignore rules do not already cover the wiki directory,
    append its rule to ``<project>/.gitignore`` (created when absent, the
    file's newline style kept) through the shared hub helper. Outside git, or
    when the wiki lives outside the project, nothing is written.

    Returns ``{"action": ..., "rule": ...}``; ``action`` is one of ``not-git``,
    ``outside-project``, ``already-ignored``, ``appended`` and ``opted-out``.
    """
    try:
        relative = wiki_root.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return {"action": "outside-project", "rule": None}
    if relative in ("", "."):
        return {"action": "outside-project", "rule": None}
    rule = ".wiki/" if relative == ".wiki" else f"/{relative}/"
    if not _hub.is_inside_git_work_tree(project_root):
        return {"action": "not-git", "rule": rule}
    ignored = _hub.git_ignores_path(project_root, f"{relative}/knowmade-ignore-probe")
    if ignored is None:
        ignored = rule in _hub.gitignore_rules(project_root)
    if ignored:
        return {"action": "already-ignored", "rule": rule}
    if opt_out:
        return {"action": "opted-out", "rule": rule}
    _hub.append_ignore_block(
        project_root,
        "Knowmade project wiki: staged copies and batch state of adopted material",
        [rule],
    )
    return {"action": "appended", "rule": rule}


def _preflight_existing_config(wiki_root: Path) -> None:
    """Refuse a hand-written ``config.md`` that cannot hold the rule-set declaration.

    ``--apply`` creates the wiki tree and then declares the current rule set in
    ``config.md``. A brownfield ``.wiki`` whose ``config.md`` lacks a YAML
    frontmatter block would otherwise be mutated and then fail on that last
    step. Check before writing anything. A wiki without a ``config.md`` is fine:
    the apply creates one.
    """
    config = wiki_root / "config.md"
    if not config.exists():
        return
    error = _rules.config_frontmatter_error(wiki_root)
    if error:
        raise SystemExit(
            f"cannot adopt into {wiki_root}: {error}. Add a YAML frontmatter block "
            "to config.md (or move it aside) and re-run; nothing was changed."
        )


def _add_scan_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("project_root", nargs="?", default=".", help="Existing project/research directory.")
    parser.add_argument("--wiki-root", help="Wiki root to initialize; defaults to <project>/.wiki.")
    parser.add_argument("--title", help="Wiki title; defaults to the project directory name.")
    parser.add_argument("--mode", choices=["reference", "copy", "move"], default="reference")
    parser.add_argument("--yes", action="store_true", help="Required confirmation for move apply.")
    parser.add_argument("--batch-id", help="Explicit batch id; default is date + project slug + path hash.")
    parser.add_argument("--exclude", action="append", default=[], help="Glob relative to project root; repeatable.")
    parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    parser.add_argument(
        "--small-data-limit",
        type=int,
        default=DEFAULT_SMALL_DATA_LIMIT,
        help="Bytes; larger CSV/JSON/XML/YAML becomes dataset-class.",
    )
    parser.add_argument(
        "--hash-limit",
        type=int,
        default=DEFAULT_HASH_LIMIT,
        help="Bytes; larger files are not hashed during planning.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable plan/summary.")
    add_gitignore_option(parser)


def build_scan_parser() -> argparse.ArgumentParser:
    """Parser of the standalone ``llm-wiki-adopt`` executable (scan, and with --apply, initialize)."""
    parser = argparse.ArgumentParser(
        prog="llm-wiki-adopt",
        description="Inventory an existing project tree and initialize a project-local Knowmade wiki. Dry-run is the default.",
    )
    _add_scan_options(parser)
    parser.add_argument("--apply", action="store_true", help="Create/update .wiki and write the import batch.")
    return parser


def scan_and_apply(args: argparse.Namespace) -> int:
    """Scan (and with ``args.apply`` initialize) one brownfield project.

    ``args`` carries the ``llm-wiki-adopt`` options; the unified ``llm-wiki
    adopt scan|apply`` subcommands build the same namespace and call this
    directly instead of re-serialising argv.
    """
    project_root, wiki_root = resolve_project_wiki_roots(args.project_root, args.wiki_root)
    if not project_root.is_dir():
        raise SystemExit(f"project root is not a directory: {project_root}")
    if is_knowmade_checkout(project_root):
        raise SystemExit(
            "refusing to adopt the Knowmade engine checkout itself; run this from the research/project directory"
        )
    if args.max_files < 1:
        raise SystemExit("--max-files must be >= 1")
    if args.mode == "move" and args.apply and not args.yes:
        raise SystemExit("--mode move --apply requires --yes")
    if args.apply:
        _preflight_existing_config(wiki_root)

    rows, truncated = scan_project(
        project_root,
        wiki_root,
        args.exclude,
        args.max_files,
        args.small_data_limit,
        args.hash_limit,
    )
    batch_id = args.batch_id or make_batch_id(project_root)
    title = args.title or project_root.name
    result: dict[str, Any] = {
        "schema": "knowmade/adoption-plan/v1",
        "project_root": str(project_root),
        "wiki_root": str(wiki_root),
        "batch_id": batch_id,
        "mode": args.mode,
        "apply": bool(args.apply),
        "scan_truncated": truncated,
        "rows": len(rows),
        "counts": summary(rows),
        "rule_set": _rules.CURRENT_RULE_SET,
    }
    if args.apply:
        result["created_paths"] = ensure_local_wiki(
            wiki_root, project_root, title
        )
        result["gitignore"] = ensure_wiki_ignored(
            project_root, wiki_root, bool(getattr(args, "no_gitignore", False))
        )
        try:
            result["rule_set_changed"] = _rules.declare_rule_set(wiki_root)
        except ValueError as exc:
            raise SystemExit(f"adopted tree is in place but the rule set was not declared: {exc}") from exc
        result["batch"] = write_batch(
            project_root,
            wiki_root,
            batch_id,
            args.mode,
            rows,
            truncated,
            args.yes,
        )
    if args.json:
        result["items"] = rows if not args.apply else None
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        action = "APPLY" if args.apply else "DRY RUN"
        counts = result["counts"]
        print(f"Knowmade brownfield adoption: {action}")
        print(f"Project: {project_root}")
        print(f"Wiki:    {wiki_root}")
        print(f"Batch:   {batch_id}")
        print(f"Mode:    {args.mode}")
        print(f"Rules:   rules/{_rules.CURRENT_RULE_SET}")
        print(
            f"Plan:    {counts['ingest']} ingest, {counts['collection']} collection, "
            f"{counts['dataset']} dataset, {counts['needs-human']} review, {counts['skip']} skip"
        )
        if truncated:
            print(f"WARNING: scan truncated at --max-files={args.max_files}")
        if args.apply:
            print(f"Batch written: {result['batch']['batch_root']}")
            ignore = result["gitignore"]
            if ignore["action"] == "appended":
                print(f"Ignore:  appended `{ignore['rule']}` to .gitignore (the wiki holds staged copies and batch state)")
            elif ignore["action"] == "opted-out":
                print(
                    f"WARNING: `{ignore['rule']}` is not ignored by git. Add it to .gitignore or commit it "
                    "deliberately; `git add -A` would publish the staged copies under inbox/.imports/."
                )
            print("Next: ask the full Knowmade workflow to process manifest.jsonl.")
        else:
            visible = [item for item in rows if item["classification"] != "skip"]
            for row in visible[:20]:
                print(
                    f"  {row['classification']:12} {row['source_path']} ({row['reason']})"
                )
            if len(visible) > 20:
                print("  ... use --json for the complete dry-run manifest")
            print("No files were changed. Re-run with --apply after reviewing the plan.")
    return 0


def scan_main() -> int:
    """Entry point of ``scripts/llm-wiki-adopt``."""
    configure_utf8_stdio()
    return scan_and_apply(build_scan_parser().parse_args())


def run_scan(args: argparse.Namespace) -> int:
    args.apply = False
    return scan_and_apply(args)


def run_apply(args: argparse.Namespace) -> int:
    args.apply = True
    return scan_and_apply(args)


def run_batch(args: argparse.Namespace) -> int:
    return adoption_batch.run(args, args.adopt_command)


def _add_batch_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("batch", help="Batch directory, or batch id with --wiki-root.")
    parser.add_argument(
        "--wiki-root",
        help="Wiki root when batch is supplied by id; also overrides the root recorded in state.json.",
    )


def _add_arguments(parser: argparse.ArgumentParser) -> None:
    subs = parser.add_subparsers(dest="adopt_command", required=True)

    scan = subs.add_parser("scan", help="Inventory a brownfield project without writing.")
    _add_scan_options(scan)
    scan.set_defaults(adopt_func=run_scan)

    apply_parser = subs.add_parser("apply", help="Initialize .wiki and write an adoption batch.")
    _add_scan_options(apply_parser)
    apply_parser.set_defaults(adopt_func=run_apply)

    status = subs.add_parser("status", help="Show adoption batch state.")
    _add_batch_common(status)
    status.add_argument("--json", action="store_true")
    status.add_argument("--items", action="store_true", help="Include manifest rows with --json.")
    status.set_defaults(adopt_func=run_batch)

    claim = subs.add_parser("claim", help="Claim ready rows for one worker.")
    _add_batch_common(claim)
    claim.add_argument("--classification", choices=sorted(adoption_batch.OUTCOMES_BY_CLASS))
    claim.add_argument("--limit", type=int, default=1)
    claim.add_argument("--worker", default=f"{socket.gethostname()}:{os.getpid()}")
    claim.add_argument("--retry-errors", action="store_true")
    claim.add_argument("--force-unlock", action="store_true")
    claim.add_argument("--allow-interrupted", action="store_true", help="Claim although a move batch was interrupted.")
    claim.add_argument("--json", action="store_true")
    claim.set_defaults(adopt_func=run_batch)

    complete = subs.add_parser("complete", help="Complete a claimed row with a durable outcome.")
    _add_batch_common(complete)
    complete.add_argument("--item-id", required=True)
    complete.add_argument("--outcome", required=True, choices=sorted(adoption_batch.TERMINAL_STATES))
    complete.add_argument("--destination", help="Existing path inside wiki_root for durable outcomes.")
    complete.add_argument("--note")
    complete.add_argument("--force", action="store_true")
    complete.add_argument("--force-unlock", action="store_true")
    complete.set_defaults(adopt_func=run_batch)

    release = subs.add_parser("release", help="Release a claim back to its resumable state.")
    _add_batch_common(release)
    release.add_argument("--item-id", required=True)
    release.add_argument("--note")
    release.add_argument("--force-unlock", action="store_true")
    release.set_defaults(adopt_func=run_batch)

    fail = subs.add_parser("fail", help="Mark a claimed row as retryable error.")
    _add_batch_common(fail)
    fail.add_argument("--item-id", required=True)
    fail.add_argument("--error", required=True)
    fail.add_argument("--force", action="store_true")
    fail.add_argument("--force-unlock", action="store_true")
    fail.set_defaults(adopt_func=run_batch)

    sweep = subs.add_parser("sweep", help="Terminalize deterministic skip/review rows.")
    _add_batch_common(sweep)
    sweep.add_argument("--apply", action="store_true")
    sweep.add_argument("--force-unlock", action="store_true")
    sweep.set_defaults(adopt_func=run_batch)

    repair = subs.add_parser("repair", help="Finish the staging of an interrupted move batch.")
    _add_batch_common(repair)
    repair.add_argument("--yes", action="store_true", help="Required to move the remaining originals of a move batch.")
    repair.add_argument(
        "--project-root", help="Project the batch was adopted from, when the pin in state.json does not resolve."
    )
    repair.add_argument("--force-unlock", action="store_true")
    repair.set_defaults(adopt_func=run_batch)

    verify = subs.add_parser("verify", help="Require every row to be terminal and durable.")
    _add_batch_common(verify)
    verify.add_argument("--json", action="store_true")
    verify.set_defaults(adopt_func=run_batch)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="llm-wiki adopt", description=ADOPT_HELP)
    _add_arguments(parser)
    parser.set_defaults(func=lambda args: run(args, parser))
    return parser


def register_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Register ``adopt`` on a parent ``llm-wiki`` parser; dispatch through :func:`run`."""
    parser = subparsers.add_parser("adopt", help=ADOPT_HELP, description=ADOPT_HELP)
    _add_arguments(parser)
    parser.set_defaults(func=lambda args: run(args, parser))
    return parser


def run(args: argparse.Namespace, parser: argparse.ArgumentParser | None = None) -> int:
    if getattr(args, "limit", 1) < 1:
        if parser is not None:
            parser.error("--limit must be >= 1")
        raise SystemExit("--limit must be >= 1")
    return int(args.adopt_func(args))


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    parser = build_parser()
    return run(parser.parse_args(argv), parser)
