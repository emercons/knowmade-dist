"""The lint data model and the lint report.

``LintContext`` carries one lint run: its root, whether ``--fix`` is on, the
findings (``Issue``), the repairs made and the parsed ``Document`` set that
the checks share; ``Step`` names one step of a run (``lint.lint_steps`` lists
them in order). Every repair asks one gate, ``LintContext.fix_allowed``,
before it writes; the gate applies the repair-class flags, plans under
``--preflight`` and refuses under ``--refuse-on`` (emercons/knowmade#111).
Below the model: reading a document's text and finding the
Markdown files under a root, the ``log.md`` entry a ``--fix`` run appends, and
the report itself, with severity ordering, the text and JSON renderings, and
the exit-code policy behind ``--fail-on``. Split out of ``lint`` with the
model from ``common`` (emercons/knowmade#105 R3).
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

from . import engine as _engine
from .common import PRIVATE_SUFFIX
from .markdown import append_text_preserving, today_date
from .roots import is_permission_denied, permission_denied_message

# The repair classes of ``lint --fix``, one per kind of mutation; the table in
# references/linting.md says which repairs each covers (knowmade#111).
FIX_CLASSES = (
    "quarantine",
    "placement",
    "frontmatter",
    "index",
    "create",
    "registry",
    "gitignore",
    "log",
)

# The exit code of a run that ``--refuse-on`` stopped before it wrote.
REFUSED_EXIT = 2


@dataclass
class Issue:
    severity: str
    message: str
    path: Path | None = None
    fixable: bool = False
    fixed: bool = False
    # The stable id of the check that found it: a row of the rules table in
    # references/linting.md (``C1``..``C25``, ``C4b``), emercons/knowmade#82.
    check: str = ""
    # The repair class that fixes it (``FIX_CLASSES``); empty when not fixable.
    fix_class: str = ""

    def sort_key(self) -> tuple[int, str, str]:
        order = {"critical": 0, "warning": 1, "suggestion": 2, "info": 3}
        return (order.get(self.severity, 9), self.rel, self.message)

    @property
    def rel(self) -> str:
        return str(self.path) if self.path else ""


@dataclass
class Document:
    """A parsed Markdown file.

    ``raw_text`` is the file's full text as read when it was parsed, so the
    checks that need the whole file (links, footnotes, source rewrites) reuse
    it instead of reading the same file again. It is ``None`` once a repair
    has rewritten the file after parsing; ``document_text`` then reads
    the file afresh and caches the result.
    """

    path: Path
    frontmatter: dict[str, Any]
    body: str
    raw_text: str | None = None
    # The file's on-disk style, so a rewrite can keep it (emercons/knowmade#28):
    # ``newline`` is "\r\n" or "\n", ``bom`` whether it began with a UTF-8 BOM.
    newline: str = "\n"
    bom: bool = False


@dataclass
class PlannedFix:
    """One mutation a repair would make: listed by ``--preflight``, named by ``--refuse-on``."""

    fix_class: str
    action: str
    path: Path
    check: str = ""


class LintContext:
    """One lint run.

    ``fix`` is a writing ``--fix`` run; ``preflight`` plans the same repairs
    and writes nothing. ``fix_classes`` are the repair classes allowed to act,
    ``refuse_on`` the classes whose acting refuses the run.
    """

    def __init__(
        self,
        root: Path,
        fix: bool = False,
        *,
        preflight: bool = False,
        fix_classes: frozenset[str] | set[str] | None = None,
        refuse_on: frozenset[str] | set[str] = frozenset(),
    ) -> None:
        self.root = root.resolve()
        self.fix = fix and not preflight
        self.preflight = preflight
        self.fix_classes = frozenset(FIX_CLASSES if fix_classes is None else fix_classes)
        self.refuse_on = frozenset(refuse_on)
        self.plan: list[PlannedFix] = []
        self.refused: list[PlannedFix] = []
        self.issues: list[Issue] = []
        self.fixes: list[str] = []
        self.documents: dict[Path, Document] = {}
        self.referenced_raw: set[Path] = set()
        self.engine: dict[str, Any] | None = None

    def rel(self, path: Path | None) -> str:
        if path is None:
            return ""
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    def issue(
        self,
        severity: str,
        message: str,
        path: Path | None = None,
        fixable: bool = False,
        fixed: bool = False,
        *,
        check: str,
        fix_class: str = "",
    ) -> None:
        self.issues.append(
            Issue(severity, message, path, fixable, fixed, check, fix_class if fixable else "")
        )

    @property
    def fixing(self) -> bool:
        """True when repairs are considered at all: ``--fix`` or ``--preflight``."""
        return self.fix or self.preflight

    def applies(self, fix_class: str) -> bool:
        """True when ``fix_class`` writes in this run; asks without recording a plan.

        For a site that reports what blocks its repair (a move whose
        destination exists) instead of planning one.
        """
        return self.fix and fix_class in self.fix_classes and fix_class not in self.refuse_on

    def fix_allowed(self, fix_class: str, path: Path, action: str, *, check: str = "") -> bool:
        """The one gate every repair passes before it writes.

        True only on a ``--fix`` run whose allowed classes include
        ``fix_class`` and that does not refuse it. Under ``--preflight`` the
        mutation is recorded in ``plan``; one of a ``--refuse-on`` class is
        also recorded in ``refused``. When the answer is False the caller
        behaves as a run without ``--fix`` at that site: it reports the
        finding as fixable and writes nothing.
        """
        if fix_class not in FIX_CLASSES:
            raise ValueError(f"unknown fix class: {fix_class}")
        if not self.fixing or fix_class not in self.fix_classes:
            return False
        if self.preflight or fix_class in self.refuse_on:
            planned = PlannedFix(fix_class, action, path, check)
            self.plan.append(planned)
            if fix_class in self.refuse_on:
                self.refused.append(planned)
            return False
        return True

    def fixed(self, message: str) -> None:
        self.fixes.append(message)

    def active_issues(self) -> list[Issue]:
        return [issue for issue in self.issues if not issue.fixed]

    def counts(self) -> dict[str, int]:
        counts = {"critical": 0, "warning": 0, "suggestion": 0, "info": 0}
        for issue in self.active_issues():
            counts[issue.severity] = counts.get(issue.severity, 0) + 1
        return counts


class Step(NamedTuple):
    """One named step of a lint run, in the order ``lint.lint_steps`` lists it.

    ``fix`` says what the step changes under ``--fix``; it is empty for a step
    that only reports, and so is ``fix_class``, the step's main repair class
    (``FIX_CLASSES``). A step that also writes in another class gates that
    site with its own class. The name, ``fix`` and ``fix_class`` document the
    run; only ``run`` executes.
    """

    name: str
    run: Callable[[LintContext], None]
    fix: str = ""
    fix_class: str = ""


def document_text(doc: Document) -> str:
    """Return the file's full text, reading it only if it is not cached.

    ``read_document`` caches the text it parsed. A repair that rewrites the
    file clears ``raw_text``, so this never serves text older than the file.
    """
    if doc.raw_text is None:
        doc.raw_text = doc.path.read_text(encoding="utf-8")
    return doc.raw_text


def is_private_dir_name(name: str) -> bool:
    """True for ``<slug>-PRIVATE``: the suffix plus at least one more character."""
    return name.endswith(PRIVATE_SUFFIX) and len(name) > len(PRIVATE_SUFFIX)


def markdown_files(root: Path, *, skip_private: bool = False) -> list[Path]:
    """Every ``*.md`` file under ``root``, sorted.

    With ``skip_private`` the walk prunes every ``*-PRIVATE`` directory below
    ``root`` (``root`` itself is never pruned), so ``links check`` and
    ``lint --all`` can leave private siblings alone. The default is the full
    recursive listing.
    """
    try:
        if not root.exists():
            return []
        if skip_private:
            found: list[Path] = []
            for current, dirs, names in os.walk(root):
                dirs[:] = [name for name in dirs if not is_private_dir_name(name)]
                found.extend(
                    path for path in (Path(current) / name for name in names if name.endswith(".md"))
                    if path.is_file()
                )
            return sorted(found)
        return sorted(path for path in root.rglob("*.md") if path.is_file())
    except OSError as exc:
        if is_permission_denied(exc):
            raise SystemExit(permission_denied_message(root, "list markdown files under", exc)) from exc
        raise


def append_lint_log(ctx: LintContext) -> None:
    log = ctx.root / "log.md"
    if not ctx.fixing or not log.exists():
        return
    if not ctx.fix_allowed("log", log, "append this run's lint entry"):
        return
    counts = ctx.counts()
    today = today_date().isoformat()
    entry = (
        f"\n## [{today}] lint | local command: "
        f"{counts['critical']} critical, {counts['warning']} warnings, "
        f"{counts['suggestion']} suggestions, {len(ctx.fixes)} auto-fixed\n"
    )
    append_text_preserving(log, entry)


def report_lint_result(ctx: LintContext, args: argparse.Namespace) -> int:
    """Print the lint report and return its exit code.

    One verdict feeds the JSON ``status``, the text ``Result:`` line and the
    exit code, so ``--fail-on`` can never make them disagree.
    """
    fail_on = effective_fail_on(args, ctx.preflight)
    exit_code = lint_exit_code(ctx.counts(), fail_on)
    failed = exit_code != 0
    if args.json:
        print_json_report(ctx, failed=failed, fail_on=fail_on)
    else:
        print_text_report(ctx, failed=failed)
    return REFUSED_EXIT if ctx.refused else exit_code


def effective_fail_on(args: argparse.Namespace, preflight: bool = False) -> str:
    """The ``--fail-on`` level in force: ``none`` for a preflight that names none."""
    fail_on = getattr(args, "fail_on", None)
    if fail_on:
        return fail_on
    return "none" if preflight else "suggestion"


FAIL_ON_LEVELS = ("critical", "warning", "suggestion")

# How the text report names each severity in a finding line.
SEVERITY_LABELS = {
    "critical": "Critical",
    "warning": "Warning",
    "suggestion": "Suggestion",
    "info": "Info",
}


def lint_exit_code(counts: dict[str, int], fail_on: str = "suggestion") -> int:
    """Exit non-zero when a finding at or above ``fail_on`` severity exists.

    The default reproduces the historical behaviour (any finding fails). A
    stricter gate such as ``--fail-on critical`` lets CI block protocol
    violations without being blocked by advisory findings, which otherwise
    makes the exit code unusable as a gate on any wiki carrying known
    citation debt. ``none`` (a ``--preflight`` without ``--fail-on``) never
    fails.
    """
    if fail_on == "none":
        return 0
    threshold = FAIL_ON_LEVELS.index(fail_on) if fail_on in FAIL_ON_LEVELS else len(FAIL_ON_LEVELS) - 1
    return 1 if any(counts.get(level) for level in FAIL_ON_LEVELS[: threshold + 1]) else 0


def print_json_report(
    ctx: LintContext,
    failed: bool | None = None,
    fail_on: str = "suggestion",
) -> None:
    counts = ctx.counts()
    if failed is None:
        failed = lint_exit_code(counts, fail_on) != 0
    report = {
        "engine": ctx.engine or _engine.contract_state(ctx.root),
        "root": str(ctx.root),
        "status": "fail" if failed else "pass",
        "fail_on": fail_on,
        "counts": counts,
        "fixes": ctx.fixes,
        "issues": [
            {
                "check": issue.check,
                "fix_class": issue.fix_class,
                "severity": issue.severity,
                "message": issue.message,
                "path": ctx.rel(issue.path) if issue.path else None,
                "fixable": issue.fixable,
            }
            for issue in sorted(ctx.active_issues(), key=lambda item: item.sort_key())
        ],
    }
    if ctx.preflight or ctx.refused:
        report["plan"] = [planned_fix_json(ctx, item) for item in ctx.plan]
    if ctx.refused:
        report["refused"] = [planned_fix_json(ctx, item) for item in ctx.refused]
    print(json.dumps(report, indent=2, sort_keys=True))


def planned_fix_json(ctx: LintContext, item: PlannedFix) -> dict[str, str]:
    return {
        "action": item.action,
        "check": item.check,
        "fix_class": item.fix_class,
        "path": ctx.rel(item.path),
    }


def print_text_report(ctx: LintContext, failed: bool | None = None) -> None:
    counts = ctx.counts()
    if failed is None:
        failed = lint_exit_code(counts) != 0
    has_findings = bool(counts["critical"] or counts["warning"] or counts["suggestion"])
    print(f"Knowmade lint: {ctx.root}")
    engine = ctx.engine or _engine.contract_state(ctx.root)
    if engine["commit"] or engine["version"] or engine["state"] != "undeclared":
        version = engine["version"] or "unknown"
        commit = (engine["commit"] or "unknown")[:12]
        declared = []
        if engine["declared_version"]:
            declared.append(f"version {engine['declared_version']}")
        if engine["declared"]:
            declared.append(f"commit {engine['declared'][:12]}")
        print(
            f"Engine: {version} ({commit}); declared: "
            f"{', '.join(declared) or 'none'}, {engine['state']}"
        )
    if ctx.fixes:
        print("\nAuto-fixed:")
        for fix in ctx.fixes:
            print(f"- {fix}")
    if ctx.preflight or ctx.refused:
        print("\nPlan (nothing written):" if ctx.plan else "\nPlan: nothing to repair.")
        for item in ctx.plan:
            print(f"PLAN {item.fix_class} {item.action}: {ctx.rel(item.path)}")
    for item in ctx.refused:
        print(f"REFUSED {item.fix_class} {item.action}: {ctx.rel(item.path)}")
    if has_findings or counts["info"]:
        print("\nFindings:")
        for issue in sorted(ctx.active_issues(), key=lambda item: item.sort_key()):
            prefix = SEVERITY_LABELS.get(issue.severity, issue.severity.title())
            location = f" ({ctx.rel(issue.path)})" if issue.path else ""
            fixable = " Run again with --fix to apply the safe fix." if issue.fixable and not ctx.fix else ""
            print(f"- {prefix}: {issue.message}{location}{fixable}")
    else:
        print("\nFindings: none")
    print(
        "\nSummary: "
        f"{counts['critical']} critical, {counts['warning']} warnings, "
        f"{counts['suggestion']} suggestions, {counts['info']} info, "
        f"{len(ctx.fixes)} auto-fixed."
    )
    if ctx.refused:
        refused = ",".join(sorted({item.fix_class for item in ctx.refused}))
        print(f"Result: REFUSED (--refuse-on {refused})")
        return
    print("Result: " + ("FAIL" if failed else "PASS"))
