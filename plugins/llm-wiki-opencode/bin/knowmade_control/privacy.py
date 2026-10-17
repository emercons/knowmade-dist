"""The ``privacy`` namespace: scan tracked files for secrets and private paths.

``llm-wiki privacy scan`` runs the same pattern pack ``checkpoint seal`` uses
(``checkpoint_scan.checkpoint_pattern_specs``, which reads the shared table in
``redaction.py``) over the text files of a wiki, hub or project, so the two
commands cannot drift: the same planted secret produces the same
``privacy-<sha16>`` finding id in both. ``--check tracked-private`` is the
structural check the content scan cannot make: a path under a ``*-PRIVATE/``
directory that git tracks.

The allow file is shared with ``checkpoint seal --allowlist``: see
``Allowlist``. A regex entry is searched in ``path::kind::match``; so that no
field can shift into another, runs of colons in the kind and the match text are
collapsed to one colon before joining, and a finding whose path contains ``::``
is never allowlistable. Exit codes: 0 nothing at or above ``--fail-on``, 1 something is,
2 usage or configuration error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import fsio as _fsio
from . import redaction as _redaction
from .checkpoint_scan import checkpoint_finding, checkpoint_pattern_specs
from .hub import is_inside_git_work_tree
from .roots import expand_leading_tilde, resolve_hub_args, resolve_wiki_root_args

TEXT_SUFFIXES = frozenset({".md", ".json", ".html", ".txt", ".yaml", ".yml", ".toml"})
MAX_FILE_BYTES = 10 * 1024 * 1024
PRIVATE_DIR_SUFFIX = "-PRIVATE"
SEVERITY_RANK = {"warning": 1, "critical": 2}
FINDING_ID_RE = re.compile(r"privacy-[0-9a-f]{16}")
PACK_FIELDS = ("id", "kind", "regex", "severity", "description")
EXCERPT_CONTEXT = 60
PRIVATE_TRACKED_KIND = "private-tracked"
PRIVATE_TRACKED_MESSAGE = "Git tracks a path under a -PRIVATE directory."
UNSCANNED_KIND = "unscanned"
UNSCANNED_MESSAGE = "A listed file was not scanned"

PatternSpec = tuple[str, str, str, "re.Pattern[str]"]
_COLONS = re.compile(r":{2,}")


class PrivacyError(Exception):
    """A usage or configuration problem; the CLI reports it and exits 2."""


@dataclass(frozen=True)
class Allowlist:
    """Findings an operator has acknowledged: exact ids and match regexes."""

    ids: frozenset[str] = frozenset()
    patterns: tuple["re.Pattern[str]", ...] = ()

    def allows(self, finding_id: str, path: str, kind: str, match: str) -> bool:
        if "::" in path:
            return False
        if finding_id in self.ids:
            return True
        # `::` is the field delimiter: it cannot occur inside a field.
        subject = f"{path}::{_COLONS.sub(':', kind)}::{_COLONS.sub(':', match)}"
        return any(pattern.search(subject) for pattern in self.patterns)

    def __bool__(self) -> bool:
        return bool(self.ids or self.patterns)


def load_allowlist(path: str | Path | None) -> Allowlist:
    """Parse an allow file: one entry per line, ``#`` comments, blank lines ignored.

    An entry is ``privacy-<id>`` (that finding id) or a regular expression
    searched in ``path::kind::match``. Raises ``PrivacyError`` on a file that
    cannot be read or an entry that does not compile.
    """
    if path is None:
        return Allowlist()
    file = expand_leading_tilde(str(path))
    try:
        text = file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise PrivacyError(f"--allowlist {path}: cannot read file ({type(exc).__name__})") from None
    ids: set[str] = set()
    patterns: list[re.Pattern[str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        entry = raw.strip()
        if not entry or entry.startswith("#"):
            continue
        if FINDING_ID_RE.fullmatch(entry):
            ids.add(entry)
            continue
        try:
            patterns.append(re.compile(entry))
        except re.error as exc:
            raise PrivacyError(
                f"--allowlist {path}: line {number} is not a finding id or a valid regex ({exc})"
            ) from None
    return Allowlist(frozenset(ids), tuple(patterns))


def load_pattern_packs(paths: Iterable[str | Path]) -> list[PatternSpec]:
    """Extra pattern rows from JSON pack files, validated strictly."""
    specs: list[PatternSpec] = []
    seen: set[str] = set()
    for raw_path in paths:
        file = expand_leading_tilde(str(raw_path))
        label = f"--patterns {raw_path}"
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except OSError as exc:
            raise PrivacyError(f"{label}: cannot read file ({type(exc).__name__})") from None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PrivacyError(f"{label}: not valid UTF-8 JSON ({exc})") from None
        if not isinstance(data, list):
            raise PrivacyError(f"{label}: expected a JSON list of pattern objects")
        for index, row in enumerate(data, start=1):
            where = f"{label}: entry {index}"
            if not isinstance(row, dict):
                raise PrivacyError(f"{where} must be an object")
            unknown = sorted(set(row) - set(PACK_FIELDS))
            if unknown:
                raise PrivacyError(f"{where} has unknown field(s): {', '.join(unknown)}")
            for field in PACK_FIELDS:
                value = row.get(field)
                if not isinstance(value, str) or not value.strip():
                    raise PrivacyError(f"{where} needs a non-empty string field {field!r}")
            if row["severity"] not in SEVERITY_RANK:
                raise PrivacyError(f"{where} severity must be 'warning' or 'critical'")
            if row["id"] in seen:
                raise PrivacyError(f"{where} repeats pattern id {row['id']!r}")
            seen.add(row["id"])
            try:
                compiled = re.compile(row["regex"])
            except re.error as exc:
                raise PrivacyError(f"{where} regex does not compile ({exc})") from None
            specs.append((row["kind"], row["severity"], row["description"], compiled))
    return specs


@dataclass(frozen=True)
class PrivacyFinding:
    finding_id: str
    kind: str
    severity: str
    path: str
    line: int
    excerpt: str
    allowed: bool

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.finding_id,
            "kind": self.kind,
            "severity": self.severity,
            "path": self.path,
            "line": self.line,
            "excerpt": self.excerpt,
            "allowed": self.allowed,
        }


def _excerpt(
    text: str, match: re.Match[str], kind: str, specs: Sequence[PatternSpec]
) -> str:
    """The match's line with every sensitive span replaced by ``[kind]``.

    The whole line is redacted first (the finding's own match, every active
    pattern, then the shared redaction table) and only then cut to a window
    around the finding, so a second secret on the line is never shown cut in
    half.
    """
    start = match.start()
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", start)
    if line_end == -1:
        line_end = len(text)
    line = text[line_start:line_end]
    spans = [[start - line_start, min(match.end(), line_end) - line_start, kind]]
    for spec_kind, _severity, _message, pattern in specs:
        for found in pattern.finditer(line):
            if found.end() > found.start():
                spans.append([found.start(), found.end(), spec_kind])
    spans.sort(key=lambda span: (span[0], span[0] - span[1]))
    merged: list[list[Any]] = []
    for span in spans:
        if merged and span[0] < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], span[1])
        else:
            merged.append(span)
    parts: list[str] = []
    position = 0
    for span_start, span_end, span_kind in merged:
        parts.append(line[position:span_start])
        parts.append(f"[{span_kind}]")
        position = span_end
    parts.append(line[position:])
    redacted = _redaction.redact_secrets("".join(parts))
    anchor = max(redacted.find(f"[{kind}]"), 0)
    return redacted[max(0, anchor - EXCERPT_CONTEXT) : anchor + EXCERPT_CONTEXT * 2].strip()


def scan_text(
    text: str, rel: str, specs: Sequence[PatternSpec], allowlist: Allowlist
) -> list[PrivacyFinding]:
    # Same newline normalisation as the checkpoint reader, so a CRLF file
    # yields the same evidence, and so the same finding id, in both commands.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    findings: list[PrivacyFinding] = []
    for kind, severity, message, pattern in specs:
        for match in pattern.finditer(text):
            if not match.group(0):
                continue
            line = text.count("\n", 0, match.start()) + 1
            finding_id = checkpoint_finding(
                kind, severity, rel, line, message, match.group(0)
            ).finding_id
            findings.append(
                PrivacyFinding(
                    finding_id,
                    kind,
                    severity,
                    rel,
                    line,
                    _excerpt(text, match, kind, specs),
                    allowlist.allows(finding_id, rel, kind, match.group(0)),
                )
            )
    return findings


def git_ls_files(root: Path) -> list[str] | None:
    git = shutil.which("git")
    if git is None:
        return None
    try:
        result = subprocess.run(
            [git, *_fsio.GIT_READ_OPTIONS, "ls-files", "-z"],
            cwd=str(root),
            env=_fsio.git_environment(),
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    # surrogateescape keeps a non-UTF-8 name mapped to the real file; a name
    # decoded with replacement characters would fail the file probe in
    # _scan_files and silently drop that tracked file from the scan.
    return sorted(
        item.decode("utf-8", errors="surrogateescape") for item in result.stdout.split(b"\0") if item
    )


def tracked_private_paths(root: Path, anchors: Sequence[str] = ()) -> list[str] | None:
    """Git-tracked paths under a ``*-PRIVATE/`` directory, except anchor basenames.

    Paths are relative to ``root`` (git runs from there, so a hub nested in a
    larger repository is judged on its own subtree). Returns ``None`` when git
    cannot list the files, which is not the same as ``[]`` ("nothing tracked"):
    a caller must fail visibly, never read it as clean. Callers decide first
    whether ``root`` is in a git work tree.
    """
    tracked = git_ls_files(root)
    if tracked is None:
        return None
    keep = set(anchors)
    found = []
    for rel in tracked:
        parts = rel.split("/")
        in_private = any(
            part.endswith(PRIVATE_DIR_SUFFIX) and len(part) > len(PRIVATE_DIR_SUFFIX)
            for part in parts[:-1]
        )
        if in_private and parts[-1] not in keep:
            found.append(rel)
    return found


def _is_text_candidate(rel: str) -> bool:
    return Path(rel).suffix.lower() in TEXT_SUFFIXES


def _worktree_files(root: Path) -> list[str]:
    files: list[str] = []
    for current, dirs, names in os.walk(root):
        dirs[:] = sorted(name for name in dirs if name != ".git")
        base = Path(current)
        for name in sorted(names):
            if name == ".git":
                continue
            rel = (base / name).relative_to(root).as_posix()
            if _is_text_candidate(rel):
                files.append(rel)
    return files


def _resolve_root(args: argparse.Namespace) -> Path:
    if args.path and (args.local or args.wiki):
        raise PrivacyError("a path cannot be combined with --local or --wiki")
    if args.local and args.wiki:
        raise PrivacyError("--local and --wiki cannot be combined")
    try:
        if args.hub and not (args.path or args.local or args.wiki):
            root = resolve_hub_args(args)
        else:
            root = resolve_wiki_root_args(args)
    except SystemExit as exc:
        raise PrivacyError(str(exc.code)) from None
    root = root.resolve(strict=False)
    if not root.is_dir():
        raise PrivacyError(f"privacy scan root is not a directory: {root}")
    return root


def _private_tracked_finding(rel: str, allowlist: Allowlist) -> PrivacyFinding:
    finding_id = checkpoint_finding(
        PRIVATE_TRACKED_KIND, "critical", rel, 0, PRIVATE_TRACKED_MESSAGE, rel
    ).finding_id
    return PrivacyFinding(
        finding_id,
        PRIVATE_TRACKED_KIND,
        "critical",
        rel,
        0,
        PRIVATE_TRACKED_MESSAGE,
        allowlist.allows(finding_id, rel, PRIVATE_TRACKED_KIND, rel),
    )


def _scan_files(
    root: Path,
    candidates: Sequence[str],
    specs: Sequence[PatternSpec],
    allowlist: Allowlist,
) -> tuple[list[PrivacyFinding], int, int]:
    """Scan every candidate; a candidate that cannot be scanned is a finding.

    A listed file that is missing, unreadable, undecodable by name or over
    ``MAX_FILE_BYTES`` is reported as an ``unscanned`` warning and counted in
    ``skipped``, never dropped silently: a scan that quietly skips a tracked
    file passes while that file leaks. Symlinks are not content and are
    skipped without a finding.
    """
    findings: list[PrivacyFinding] = []
    scanned = skipped = 0
    for rel in candidates:
        try:
            file = root / rel
            if file.is_symlink():
                continue
            if not file.is_file():
                raise OSError("not a regular file")
            if file.stat().st_size > MAX_FILE_BYTES:
                raise OSError(f"larger than {MAX_FILE_BYTES} bytes")
            text = file.read_bytes().decode("utf-8", errors="replace")
        except (OSError, ValueError) as exc:
            skipped += 1
            findings.append(_unscanned_finding(rel, str(exc), allowlist))
            continue
        scanned += 1
        findings.extend(scan_text(text, rel, specs, allowlist))
    return findings, scanned, skipped


def _unscanned_finding(rel: str, reason: str, allowlist: Allowlist) -> PrivacyFinding:
    shown = rel.encode("utf-8", errors="backslashreplace").decode("ascii", errors="replace")
    message = f"{UNSCANNED_MESSAGE} ({reason})."
    finding_id = checkpoint_finding(UNSCANNED_KIND, "warning", shown, 0, message, shown).finding_id
    return PrivacyFinding(
        finding_id,
        UNSCANNED_KIND,
        "warning",
        shown,
        0,
        message,
        allowlist.allows(finding_id, shown, UNSCANNED_KIND, shown),
    )


def _scan(args: argparse.Namespace) -> int:
    root = _resolve_root(args)
    allowlist = load_allowlist(args.allowlist)
    packs = load_pattern_packs(args.patterns or [])
    anchors = [item.strip() for item in (args.anchors or "").split(",") if item.strip()]
    in_git = is_inside_git_work_tree(root)

    if args.check == "tracked-private":
        if not in_git:
            print(f"privacy scan: {root} is not in a git work tree; tracked-private check skipped")
            return 0
        scope = "tracked"
        private_paths = tracked_private_paths(root, anchors)
        if private_paths is None:
            raise PrivacyError("git could not list the tracked files of the root")
        findings = [_private_tracked_finding(rel, allowlist) for rel in private_paths]
        scanned = skipped = 0
    else:
        scope = args.scope or ("tracked" if in_git else "worktree")
        if scope == "tracked":
            if not in_git:
                raise PrivacyError("--scope tracked needs a root inside a git work tree")
            listed = git_ls_files(root)
            if listed is None:
                raise PrivacyError("git could not list the tracked files of the root")
            candidates = [rel for rel in listed if _is_text_candidate(rel)]
        else:
            candidates = _worktree_files(root)
        findings, scanned, skipped = _scan_files(
            root, candidates, checkpoint_pattern_specs() + packs, allowlist
        )

    unique = {finding.finding_id: finding for finding in findings}
    findings = sorted(unique.values(), key=lambda f: (f.path, f.line, f.kind, f.finding_id))
    threshold = SEVERITY_RANK[args.fail_on]
    blocking = [
        f for f in findings if not f.allowed and SEVERITY_RANK[f.severity] >= threshold
    ]
    counts = {
        "critical": sum(1 for f in findings if not f.allowed and f.severity == "critical"),
        "warning": sum(1 for f in findings if not f.allowed and f.severity == "warning"),
        "allowed": sum(1 for f in findings if f.allowed),
        "files": scanned,
        "skipped": skipped,
    }
    shown = [f for f in findings if args.show_allowed or not f.allowed]
    if args.json:
        print(
            json.dumps(
                {
                    "root": str(root),
                    "scope": scope,
                    "findings": [f.to_json() for f in shown],
                    "counts": counts,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        for finding in shown:
            tag = " (allowed)" if finding.allowed else ""
            print(
                f"{finding.severity} {finding.kind} {finding.path}:{finding.line} "
                f"{finding.finding_id} {finding.excerpt}{tag}"
            )
        print(
            f"privacy scan: {counts['critical']} critical, {counts['warning']} warning, "
            f"{counts['allowed']} allowed ({scanned} files, scope {scope})"
        )
    return 1 if blocking else 0


def run_privacy(args: argparse.Namespace) -> int:
    try:
        return _scan(args)
    except PrivacyError as exc:
        print(f"llm-wiki privacy: {exc}", file=sys.stderr)
        return 2


def register_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Register the ``privacy`` namespace on a top-level subparsers action."""
    privacy = subparsers.add_parser(
        "privacy",
        help="Scan tracked files with the `checkpoint seal` pattern pack.",
        description="Privacy scanning with the pattern pack `checkpoint seal` uses.",
    )
    sub = privacy.add_subparsers(dest="privacy_command", required=True)
    scan = sub.add_parser(
        "scan",
        help="Scan the text files of a root.",
        description=(
            "Scan .md, .json, .html, .txt, .yaml, .yml and .toml files; excerpts "
            "never show the match. Exit 0 clean, 1 findings at or above --fail-on, "
            "2 usage error."
        ),
    )
    scan.add_argument("path", nargs="?", help="Root directory to scan.")
    scan.add_argument("--local", action="store_true", help="Scan ./.wiki.")
    scan.add_argument("--wiki", help="Scan a named wiki.")
    scan.add_argument(
        "--hub", help="Hub path; alone, scan the hub itself."
    )
    scan.add_argument(
        "--scope",
        choices=["tracked", "worktree"],
        help="Tracked files or the whole tree (default: tracked inside a work tree).",
    )
    scan.add_argument(
        "--patterns",
        action="append",
        default=[],
        metavar="FILE",
        help="Extra JSON pack of {id, kind, regex, severity, description}; repeatable.",
    )
    scan.add_argument(
        "--allowlist",
        metavar="FILE",
        help="Allow file shared with `checkpoint seal`: `privacy-<id>` or `path::kind::match` regex lines.",
    )
    scan.add_argument(
        "--fail-on",
        choices=["warning", "critical"],
        default="critical",
        help="Lowest severity that exits 1.",
    )
    scan.add_argument("--json", action="store_true", help="Print JSON.")
    scan.add_argument(
        "--show-allowed", action="store_true", help="List allowed findings too."
    )
    scan.add_argument(
        "--check",
        choices=["tracked-private"],
        help="Run only this check: tracked paths under *-PRIVATE/ (critical).",
    )
    scan.add_argument(
        "--anchors",
        default="",
        help="Comma-separated basenames the check tolerates.",
    )
    privacy.set_defaults(func=run_privacy)
    return privacy


def run(args: argparse.Namespace) -> int:
    """Dispatch a parsed ``privacy`` namespace."""
    return run_privacy(args)
