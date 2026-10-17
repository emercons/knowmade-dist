"""Unified ``llm-wiki adopt`` command surface.

The scanner and resumable batch state machine remain separate deterministic
modules.  This front controller gives them one public namespace and calls
their ``run`` entry points directly with the parsed namespace; the legacy
executables build the same namespaces through their own parsers.
"""

from __future__ import annotations

import argparse
import os
import socket
from typing import Any

from . import adoption, adoption_batch
from .stdio import configure_utf8_stdio

ADOPT_HELP = "Scan, apply, resume, and verify deterministic brownfield adoption."


def _add_scan_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("project_root", nargs="?", default=".", help="Existing project/research directory.")
    parser.add_argument("--wiki-root", help="Wiki root to initialize; defaults to <project>/.wiki.")
    parser.add_argument("--title", help="Wiki title; defaults to the project directory name.")
    parser.add_argument("--mode", choices=["reference", "copy", "move"], default="reference")
    parser.add_argument("--yes", action="store_true", help="Required confirmation for move apply.")
    parser.add_argument("--batch-id", help="Explicit batch id; default is date + project slug + path hash.")
    parser.add_argument("--exclude", action="append", default=[], help="Glob relative to project root; repeatable.")
    parser.add_argument("--max-files", type=int, default=adoption.DEFAULT_MAX_FILES)
    parser.add_argument(
        "--small-data-limit",
        type=int,
        default=adoption.DEFAULT_SMALL_DATA_LIMIT,
        help="Bytes; larger CSV/JSON/XML/YAML becomes dataset-class.",
    )
    parser.add_argument(
        "--hash-limit",
        type=int,
        default=adoption.DEFAULT_HASH_LIMIT,
        help="Bytes; larger files are not hashed during planning.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable plan/summary.")
    adoption.add_gitignore_option(parser)


def run_scan(args: argparse.Namespace) -> int:
    args.apply = False
    return adoption.run(args)


def run_apply(args: argparse.Namespace) -> int:
    args.apply = True
    return adoption.run(args)


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
