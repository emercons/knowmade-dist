#!/usr/bin/env python3
"""Brownfield adoption scanner backed by the shared root resolver.

The scanner implementation remains byte-for-byte compatible in
``adoption_legacy``; this module owns the ``llm-wiki-adopt`` argument parser
and root selection so both the legacy ``llm-wiki-adopt`` executable and
``llm-wiki adopt`` use the same deterministic path model.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from . import rules as _rules
from .adoption_legacy import (
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llm-wiki-adopt",
        description="Inventory an existing project tree and initialize a project-local Knowmade wiki. Dry-run is the default.",
    )
    parser.add_argument("project_root", nargs="?", default=".", help="Existing project/research directory.")
    parser.add_argument("--wiki-root", help="Wiki root to initialize; defaults to <project>/.wiki.")
    parser.add_argument("--title", help="Wiki title; defaults to the project directory name.")
    parser.add_argument("--mode", choices=["reference", "copy", "move"], default="reference")
    parser.add_argument("--apply", action="store_true", help="Create/update .wiki and write the import batch.")
    parser.add_argument("--yes", action="store_true", help="Required confirmation for --mode move --apply.")
    parser.add_argument("--batch-id", help="Explicit batch id; default is date + project slug + path hash.")
    parser.add_argument("--exclude", action="append", default=[], help="Glob relative to project root; repeatable.")
    parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    parser.add_argument("--small-data-limit", type=int, default=DEFAULT_SMALL_DATA_LIMIT, help="Bytes; larger CSV/JSON/XML/YAML becomes dataset-class.")
    parser.add_argument("--hash-limit", type=int, default=DEFAULT_HASH_LIMIT, help="Bytes; larger files are not hashed during planning.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable plan/summary.")
    return parser


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


def run(args: argparse.Namespace) -> int:
    """Scan (and with ``args.apply`` initialize) one brownfield project.

    ``args`` carries the ``llm-wiki-adopt`` options; the unified ``llm-wiki
    adopt scan|apply`` front controller builds the same namespace and calls
    this directly instead of re-serialising argv.
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


def main() -> int:
    configure_utf8_stdio()
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
