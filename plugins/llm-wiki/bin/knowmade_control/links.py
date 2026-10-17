"""The ``links`` namespace: whole-tree Markdown and wiki-link checks.

``llm-wiki links check`` reads the Markdown links and ``[[wiki-links]]`` of a
tree and reports the ones that point nowhere, and, on request, the ones that
are written as absolute paths. It is the whole-tree sibling of ``lint``'s
link-integrity check (C4), which only judges ``wiki/`` and ``inventory/`` of a
wiki root: a hub root, its README and its docs were never covered, and no
link finding could be turned into a gate for them (emercons/knowmade#113).

The link reader is the one ``lint`` uses: ``indexing.extract_link_destinations``
and ``indexing.is_local_markdown_link`` find and classify a destination,
``lint_links.resolve_link_target`` resolves it, and ``lint_links`` resolves a
wiki-link by path (``wikilink_file_target``) or by slug or alias. Only the
file selection, the ``--scope`` choice and the absolute-path rule are new.
``--scope wiki`` reads exactly the files C4 reads, so the two cannot disagree
about them.

Findings (all warnings, so ``--fail-on critical`` never fails on them):

- ``broken-link``: a local Markdown link whose target does not exist.
- ``unresolved-wikilink``: a wiki-link that resolves by none of the
  ``--wikilinks`` modes.
- ``absolute-link`` (``--warn-absolute`` only): a ``file:`` URL, a drive path
  (``C:\\x``, ``C:/x``), a UNC path (``\\\\host``) or a ``/``-rooted path. It
  is reported whether or not the target exists on this machine, because the
  link breaks on every other one.

Exit codes: 0 nothing at or above ``--fail-on``, 1 something is, 2 usage or
configuration error (no root, ``--scope tracked`` outside git, git failing),
never "clean" on an error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import fsio as _fsio
from .common import PRIVATE_SUFFIX, as_string_list
from .frontmatter import FrontmatterError, load as _load_frontmatter
from .hub import is_inside_git_work_tree
from .indexing import clean_link_destination, extract_link_destinations, is_local_markdown_link
from .lint_links import (
    INDEX_LINK_KEY,
    WIKILINK_RE,
    link_key,
    non_article_targets,
    resolve_link_target,
    strip_code_blocks,
    wikilink_file_target,
)
from .lint_report import is_private_dir_name, markdown_files
from .markdown import frontmatter_bounds
from .privacy import git_ls_files
from .roots import resolve_hub_args, resolve_wiki_root_args

SCOPES = ("wiki", "all", "tracked")
WIKILINK_MODES = ("slug", "path", "both")
FAIL_ON_LEVELS = ("warning", "critical")

BROKEN_LINK = "broken-link"
UNRESOLVED_WIKILINK = "unresolved-wikilink"
ABSOLUTE_LINK = "absolute-link"
FINDING_KINDS = (BROKEN_LINK, UNRESOLVED_WIKILINK, ABSOLUTE_LINK)

# The two directories C4 reads; ``--scope wiki`` reads the same ones.
C4_DIRECTORIES = ("wiki", "inventory")

_DRIVE_PATH_RE = re.compile(r"^/?[A-Za-z]:[\\/]")


class LinksError(Exception):
    """A usage or configuration error: exit code 2."""


@dataclass(frozen=True)
class Finding:
    kind: str
    path: str
    line: int
    target: str
    severity: str = "warning"

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "path": self.path,
            "line": self.line,
            "target": self.target,
        }


def resolve_root(args: argparse.Namespace) -> Path:
    """The root to check, through the same resolver the other namespaces use."""
    given = [bool(args.path), bool(args.local), bool(args.wiki)]
    if sum(given) > 1:
        raise LinksError("a path, --local and --wiki cannot be combined")
    try:
        if args.hub and not any(given):
            root = resolve_hub_args(args)
        else:
            root = resolve_wiki_root_args(args)
    except SystemExit as exc:
        raise LinksError(str(exc.code)) from None
    root = root.resolve(strict=False)
    if not root.is_dir():
        raise LinksError(f"links root is not a directory: {root}")
    return root


def _has_private_directory(root: Path, path: Path) -> bool:
    """True when a directory between ``root`` and ``path`` is a ``*-PRIVATE`` one."""
    return any(is_private_dir_name(part) for part in path.relative_to(root).parts[:-1])


def select_files(
    root: Path,
    scope: str,
    *,
    include_private: bool,
    ignore_private_readme: bool,
) -> list[Path]:
    """The Markdown files ``scope`` selects under ``root``, sorted."""
    if scope == "wiki":
        found = [path for name in C4_DIRECTORIES for path in markdown_files(root / name)]
    elif scope == "tracked":
        if not is_inside_git_work_tree(root):
            raise LinksError("--scope tracked needs a root inside a git work tree")
        listed = git_ls_files(root)
        if listed is None:
            raise LinksError("git could not list the tracked files of the root")
        found = [root / rel for rel in listed if rel.lower().endswith(".md")]
        found = [path for path in found if path.is_file()]
    else:
        found = markdown_files(root, skip_private=not include_private)

    chosen: list[Path] = []
    for path in found:
        relative_parts = path.relative_to(root).parts
        if ".git" in relative_parts:
            continue
        if scope != "wiki":
            if not include_private and _has_private_directory(root, path):
                continue
            if (
                ignore_private_readme
                and path.name == "README.md"
                and len(relative_parts) > 1
                and is_private_dir_name(relative_parts[-2])
            ):
                continue
        chosen.append(path)
    return sorted(set(chosen))


def read_markdown(path: Path) -> str:
    try:
        return _fsio.read_text(path)
    except UnicodeDecodeError:
        return path.read_bytes().decode("utf-8", errors="replace")


def blank_frontmatter(text: str) -> str:
    """``text`` with the frontmatter block emptied, keeping every line number."""
    bounds = frontmatter_bounds(text)
    if bounds is None:
        return text
    body_start = bounds[2]
    return "\n" * text.count("\n", 0, body_start) + text[body_start:]


def absolute_kind(destination: str) -> str | None:
    """Why ``destination`` is an absolute path, or ``None`` when it is not.

    A ``file:`` URL, a drive path, a UNC path and a ``/``-rooted path are
    absolute; any other URL scheme (``https:``, ``mailto:``), a protocol-relative
    ``//host`` link and a pure ``#fragment`` are not paths at all.
    """
    target = clean_link_destination(destination)
    if not target or target.startswith("#"):
        return None
    if target.lower().startswith("file:"):
        return "file URL"
    if _DRIVE_PATH_RE.match(target):
        return "drive path"
    if target.startswith("\\\\"):
        return "UNC path"
    if target.startswith("//") or "://" in target or target.lower().startswith("mailto:"):
        return None
    if target.startswith("/"):
        return "rooted path"
    return None


class SlugIndex:
    """Where a wiki-link slug may point: the checked files, plus what C4 allows.

    The checked files contribute their stem and any frontmatter ``aliases``;
    ``lint_links.non_article_targets`` adds the root ``_index.md``, the
    ``inventory``/``raw``/``output``/``datasets`` files and every ``wiki/``
    index, which C4 accepts as targets too.
    """

    def __init__(self, root: Path, files: list[Path]) -> None:
        self.root = root
        self.keys: dict[str, list[Path]] = {}
        for path in files:
            self._add(link_key(path.stem), path)
            for alias in self._aliases(path):
                self._add(link_key(alias), path)
        for key, paths in non_article_targets(root).items():
            for path in paths:
                self._add(key, path)

    def _add(self, key: str, path: Path) -> None:
        if key:
            self.keys.setdefault(key, []).append(path)

    @staticmethod
    def _aliases(path: Path) -> list[str]:
        try:
            return as_string_list(_load_frontmatter(path).fields.get("aliases"))
        except (OSError, ValueError, FrontmatterError):
            return []

    def resolves_by_slug(self, doc_dir: Path, written: str) -> bool:
        target = written.replace("\\", "/")
        stem = target.rstrip("/").rsplit("/", 1)[-1]
        if stem.lower().endswith(".md"):
            stem = stem[:-3]
        key = link_key(stem)
        if not key:
            return True
        if key == INDEX_LINK_KEY and (doc_dir / "_index.md").is_file():
            return True
        return key in self.keys


def wikilink_resolves(
    mode: str, index: SlugIndex, root: Path, doc_dir: Path, written: str
) -> bool:
    if mode in {"path", "both"}:
        target = written.replace("\\", "/")
        if wikilink_file_target(doc_dir, root, target) is not None:
            return True
        if mode == "path":
            return False
    return index.resolves_by_slug(doc_dir, written)


def check_file(
    path: Path,
    root: Path,
    index: SlugIndex,
    *,
    wikilinks: str,
    warn_absolute: bool,
) -> tuple[list[Finding], int]:
    """The findings of one file and how many links it holds."""
    text = read_markdown(path)
    rel = path.relative_to(root).as_posix()
    findings: list[Finding] = []
    links = 0
    for number, line in enumerate(text.split("\n"), start=1):
        if "](" not in line:
            continue
        for destination in extract_link_destinations(line):
            links += 1
            if warn_absolute and absolute_kind(destination):
                findings.append(Finding(ABSOLUTE_LINK, rel, number, destination))
            if is_local_markdown_link(destination):
                if not resolve_link_target(path.parent, destination).exists():
                    findings.append(Finding(BROKEN_LINK, rel, number, destination))

    prose = strip_code_blocks(blank_frontmatter(text))
    for number, line in enumerate(prose.split("\n"), start=1):
        if "[[" not in line:
            continue
        for match in WIKILINK_RE.finditer(line):
            links += 1
            written = match.group(1).strip()
            if not wikilink_resolves(wikilinks, index, root, path.parent, written):
                findings.append(Finding(UNRESOLVED_WIKILINK, rel, number, written))
    return findings, links


def check_tree(args: argparse.Namespace) -> tuple[Path, list[Finding], int, int]:
    root = resolve_root(args)
    files = select_files(
        root,
        args.scope,
        include_private=args.include_private,
        ignore_private_readme=args.ignore_private_readme,
    )
    index = SlugIndex(root, files)
    findings: list[Finding] = []
    links = 0
    for path in files:
        found, count = check_file(
            path,
            root,
            index,
            wikilinks=args.wikilinks,
            warn_absolute=args.warn_absolute,
        )
        findings.extend(found)
        links += count
    findings.sort(key=lambda item: (item.path, item.line, item.kind, item.target))
    return root, findings, len(files), links


def finding_counts(findings: list[Finding]) -> dict[str, Any]:
    by_kind = {kind: 0 for kind in FINDING_KINDS}
    for finding in findings:
        by_kind[finding.kind] = by_kind.get(finding.kind, 0) + 1
    return {
        "critical": sum(1 for item in findings if item.severity == "critical"),
        "warning": sum(1 for item in findings if item.severity == "warning"),
        "by_kind": by_kind,
    }


def exit_code(findings: list[Finding], fail_on: str) -> int:
    failing = {"critical"} if fail_on == "critical" else {"critical", "warning"}
    return 1 if any(item.severity in failing for item in findings) else 0


def run_check(args: argparse.Namespace) -> int:
    root, findings, files, links = check_tree(args)
    counts = finding_counts(findings)
    if args.json:
        print(
            json.dumps(
                {
                    "root": str(root),
                    "scope": args.scope,
                    "files": files,
                    "links": links,
                    "findings": [item.to_json() for item in findings],
                    "counts": counts,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        for item in findings:
            print(f"{item.severity} {item.kind} {item.path}:{item.line} -> {item.target}")
        print(
            f"links check: {counts['critical']} critical, {counts['warning']} warning "
            f"({files} files, {links} links, scope {args.scope})"
        )
    return exit_code(findings, args.fail_on)


def run_links(args: argparse.Namespace) -> int:
    try:
        return run_check(args)
    except LinksError as exc:
        print(f"llm-wiki links: {exc}", file=sys.stderr)
        return 2


def register_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Register the ``links`` namespace on a top-level subparsers action."""
    links = subparsers.add_parser(
        "links",
        help="Check Markdown links and wiki-links.",
        description="Whole-tree link checks.",
    )
    sub = links.add_subparsers(dest="links_command", required=True)
    check = sub.add_parser(
        "check",
        help="Check the links of a whole tree.",
        description="Exit 0 clean, 1 findings, 2 error.",
    )
    check.add_argument("path", nargs="?", help="Root to check.")
    check.add_argument("--local", action="store_true", help="Check ./.wiki.")
    check.add_argument("--wiki", help="Named wiki.")
    check.add_argument("--hub", help="Hub path; alone, the hub.")
    check.add_argument(
        "--scope",
        choices=SCOPES,
        default="wiki",
        help="wiki (as C4), all or tracked (default: wiki).",
    )
    check.add_argument(
        "--wikilinks",
        choices=WIKILINK_MODES,
        default="both",
        help="Resolve [[wiki-links]] by slug, path or both.",
    )
    check.add_argument(
        "--warn-absolute",
        action="store_true",
        help="Also warn on absolute targets.",
    )
    check.add_argument(
        "--include-private",
        action="store_true",
        help=f"Include {PRIVATE_SUFFIX} directories.",
    )
    check.add_argument(
        "--ignore-private-readme",
        action="store_true",
        help=f"Skip {PRIVATE_SUFFIX}/README.md.",
    )
    check.add_argument("--json", action="store_true", help="Print JSON.")
    check.add_argument(
        "--fail-on",
        choices=FAIL_ON_LEVELS,
        default="warning",
        help="Lowest severity that exits 1.",
    )
    links.set_defaults(func=run_links)
    return links


def run(args: argparse.Namespace) -> int:
    """Dispatch a parsed ``links`` namespace."""
    return run_links(args)
