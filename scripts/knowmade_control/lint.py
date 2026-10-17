"""The ``lint`` namespace: which checks and repairs run on a root, and in what order.

The checks live in five modules: ``lint_docs`` (per-file structure,
frontmatter, placement, index, provenance and coverage checks and their
repairs), ``lint_links`` (link, wiki-link and footnote resolution, C4),
``lint_hub`` (hub-root checks and the repository-level checks every root
runs), ``freshness`` (the C14/C15 freshness check, also the ``freshness``
namespace) and ``lint_report`` (the ``LintContext`` model, the fix gate, the
report and the exit-code policy). This module holds the run order, ``lint_steps``, and the
parser. Split from one module by emercons/knowmade#105 R3; the ``lint``
namespace itself moved out of ``legacy_cli`` in knowmade#67 R1.4.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, NamedTuple

from . import freshness as _freshness
from .common import PRIVATE_SUFFIX, add_scope_arguments
from .lint_docs import (
    check_canonical_placement,
    check_coverage,
    check_frontmatter_schema,
    check_index_consistency,
    check_projects,
    check_source_lifecycle_pointers,
    check_source_provenance,
    check_structure,
    check_tags,
    check_unknown_files,
    fix_legacy_wiki_frontmatter,
    fix_source_references,
    load_documents,
)
from .lint_hub import (
    HUB_STEPS,
    check_engine_contract,
    check_hub_structure,
    check_tracked_machine_state,
    is_private_placeholder,
)
from .lint_links import check_footnotes, check_links, check_wikilinks
from .lint_report import (
    FAIL_ON_LEVELS,
    FIX_CLASSES,
    REFUSED_EXIT,
    LintContext,
    Step,
    append_lint_log,
    report_lint_result,
)
from .roots import is_hub, resolve_wiki_root_args as resolve_wiki_root


# What a wiki root runs after the checks every root runs, in run order. The
# order is load-bearing under --fix: a repair that rewrites or moves files
# reloads the document set, and every later check sees the repaired tree.
WIKI_STEPS: tuple[Step, ...] = (
    Step("load documents", load_documents),
    Step(
        "legacy wiki frontmatter",
        fix_legacy_wiki_frontmatter,
        fix="infer category, summary, tags, created/updated and volatility; reload",
        fix_class="frontmatter",
    ),
    Step("frontmatter schema", check_frontmatter_schema),
    Step(
        "canonical placement",
        check_canonical_placement,
        fix="move misplaced files; reload",
        fix_class="placement",
    ),
    Step("reload documents", load_documents),
    Step(
        "source references",
        fix_source_references,
        fix="rewrite a source ref that matches exactly one raw/ file; reload",
        fix_class="frontmatter",
    ),
    Step(
        "index consistency",
        check_index_consistency,
        fix="regenerate a generated index, repair a curated one in place",
        fix_class="index",
    ),
    Step("links", check_links),
    Step("wiki-links", check_wikilinks),
    Step("footnotes", check_footnotes),
    Step("source provenance", check_source_provenance),
    Step("tags", check_tags),
    Step(
        "coverage",
        check_coverage,
        fix="attach a pointer to its one citing article, else write the coverage reference",
        fix_class="create",
    ),
    Step("freshness", _freshness.check_freshness),
    Step("projects", check_projects),
    Step("source lifecycle pointers", check_source_lifecycle_pointers),
)


def lint_steps(hub_root: bool, strict_engine: bool = False) -> tuple[Step, ...]:
    """Every step of one lint run on a hub or wiki root, in run order.

    This is the one place the run order, and so the ``--fix`` repair order,
    is stated (emercons/knowmade#99 was an ordering bug). Four steps
    run on every root, then ``lint_hub.HUB_STEPS`` or ``WIKI_STEPS``, then the
    ``log.md`` entry of a ``--fix`` run.
    """
    every_root = (
        Step(
            "engine and rule-set contract",
            lambda ctx: check_engine_contract(ctx, strict=strict_engine),
            fix="declare the current rule set in an existing config.md",
            fix_class="create",
        ),
        Step(
            "structure",
            check_hub_structure if hub_root else check_structure,
            fix="" if hub_root else "create missing directories, indexes, inbox/ and schema.md",
            fix_class="" if hub_root else "create",
        ),
        Step(
            "unknown files",
            check_unknown_files,
            fix="move unexpected files to inbox/.unknown/",
            fix_class="quarantine",
        ),
        Step(
            "tracked machine state",
            lambda ctx: check_tracked_machine_state(ctx, hub_root),
            fix="hub: append the missing machine-state ignore rules" if hub_root else "",
            fix_class="gitignore" if hub_root else "",
        ),
    )
    log = Step("lint log", append_lint_log, fix="append this run to log.md", fix_class="log")
    return every_root + (HUB_STEPS if hub_root else WIKI_STEPS) + (log,)


class FixOptions(NamedTuple):
    """What the repair flags of one ``lint`` call allow (knowmade#111)."""

    fix: bool
    preflight: bool
    fix_classes: frozenset[str]
    refuse_on: frozenset[str]

    def context(self, root: Any, *, preflight: bool | None = None) -> LintContext:
        return LintContext(
            root,
            fix=self.fix,
            preflight=self.preflight if preflight is None else preflight,
            fix_classes=self.fix_classes,
            refuse_on=self.refuse_on,
        )


def _class_list(values: list[list[str]] | None) -> list[str]:
    return [name for group in values or [] for name in group]


def fix_options(args: argparse.Namespace) -> FixOptions:
    """Resolve the repair flags: ``--fix-only`` intersects, ``--no-*`` subtract."""
    only = _class_list(getattr(args, "fix_only", None))
    classes = set(only) if only else set(FIX_CLASSES)
    if getattr(args, "no_quarantine", False):
        classes.discard("quarantine")
    if getattr(args, "no_log", False):
        classes.discard("log")
    return FixOptions(
        fix=bool(getattr(args, "fix", False) or only),
        preflight=bool(getattr(args, "preflight", False)),
        fix_classes=frozenset(classes),
        refuse_on=frozenset(_class_list(getattr(args, "refuse_on", None))),
    )


def run_lint(args: argparse.Namespace) -> int:
    # Imported here: lint_multi runs this very function once per root.
    from .lint_multi import run_all, wants_multi

    if wants_multi(args):
        return run_all(args)
    root = resolve_wiki_root(args)
    options = fix_options(args)
    if options.refuse_on and not (options.fix or options.preflight):
        print("lint: --refuse-on needs --fix, --fix-only or --preflight.", file=sys.stderr)
        return REFUSED_EXIT
    ctx = options.context(root)
    if is_private_placeholder(root):
        ctx.issue(
            "info",
            "Private placeholder, not checked out here: the directory holds only "
            "README.md/.gitkeep, so it is not linted as a wiki.",
            root,
            check="C19",
        )
        return report_lint_result(ctx, args)
    steps = lint_steps(is_hub(root), strict_engine=bool(getattr(args, "strict_engine", False)))
    if options.refuse_on and options.fix and not options.preflight:
        # Plan first, so a refused class stops the run before anything is
        # written. A repair that only an earlier repair makes necessary is
        # still refused in the writing pass below (and exits 2 there).
        probe = options.context(root, preflight=True)
        for step in steps:
            step.run(probe)
        if probe.refused:
            return report_lint_result(probe, args)
    for step in steps:
        step.run(ctx)
    return report_lint_result(ctx, args)


def fix_class_list(value: str) -> list[str]:
    """``argparse`` type of a ``CLASS[,CLASS]`` repair-class list."""
    names = [name.strip() for name in value.split(",") if name.strip()]
    unknown = [name for name in names if name not in FIX_CLASSES]
    if unknown or not names:
        raise argparse.ArgumentTypeError(
            f"unknown fix class {', '.join(unknown) or repr(value)}; "
            f"choose from {', '.join(FIX_CLASSES)}"
        )
    return names


def add_fix_arguments(lint: argparse.ArgumentParser) -> None:
    """The repair-class flags of ``lint`` (references/linting.md, "Fix classes")."""
    lint.add_argument(
        "--preflight",
        "--plan",
        dest="preflight",
        action="store_true",
        help="Plan the --fix writes and write nothing.",
    )
    lint.add_argument(
        "--fix-only",
        action="append",
        type=fix_class_list,
        metavar="CLASS[,CLASS]",
        help=f"Apply only these fix classes ({', '.join(FIX_CLASSES)}); implies --fix.",
    )
    lint.add_argument(
        "--no-quarantine",
        action="store_true",
        help="Leave unexpected files in place.",
    )
    lint.add_argument("--no-log", action="store_true", help="Do not append to log.md.")
    lint.add_argument(
        "--refuse-on",
        action="append",
        type=fix_class_list,
        metavar="CLASS[,CLASS]",
        help="Exit 2, writing nothing, if these classes would act.",
    )


def register_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Register the ``lint`` namespace on a top-level subparsers action."""
    lint = subparsers.add_parser(
        "lint",
        help="Run local structural checks on a wiki root.",
        description=(
            "Run deterministic checks that do not require an LLM. Pass a wiki root "
            "path, or use --local/--wiki to resolve one through the normal hub rules."
        ),
    )
    lint.add_argument("path", nargs="?", help="Wiki root path to lint.")
    lint.add_argument("--fix", action="store_true", help="Apply unambiguous structural fixes.")
    add_fix_arguments(lint)
    add_scope_arguments(
        lint,
        local_help="Lint .wiki/ in the current directory.",
        public_help=f"Resolve --wiki <slug>{PRIVATE_SUFFIX} to the public sibling <slug>.",
        include_archived_help="Allow linting an explicitly targeted archived wiki.",
    )
    lint.add_argument("--json", action="store_true", help="Print a machine-readable JSON report.")
    lint.add_argument(
        "--strict-engine",
        action="store_true",
        help=(
            "Treat a mismatch between the declared and running engine as a protocol "
            "violation rather than a warning."
        ),
    )
    lint.add_argument(
        "--fail-on",
        choices=FAIL_ON_LEVELS,
        default=None,
        help=(
            "Lowest severity that makes the command exit non-zero "
            "(default: suggestion, i.e. any finding; none for --preflight)."
        ),
    )
    lint.add_argument(
        "--all",
        action="store_true",
        help="Lint every root of the hub.",
    )
    lint.add_argument(
        "--include-nested-wiki",
        action="store_true",
        help="With --all, add topics/*/.wiki.",
    )
    lint.add_argument(
        "--skip-private",
        action="store_true",
        help=f"With --all, skip {PRIVATE_SUFFIX} roots.",
    )
    lint.add_argument(
        "--report-dir",
        metavar="DIR",
        help="With --all, write reports to DIR.",
    )
    lint.set_defaults(func=run_lint)
    return lint


def run(args: argparse.Namespace) -> int:
    """Dispatch a parsed ``lint`` namespace."""
    return run_lint(args)
