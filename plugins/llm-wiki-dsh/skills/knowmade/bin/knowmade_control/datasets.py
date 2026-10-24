"""The ``dataset`` namespace: verify that dataset payloads are what a manifest says.

A dataset manifest (``datasets/<slug>/MANIFEST.md``, ``references/datasets.md``)
names where the data lives (``locations``) and may state what it should look
like (``size_bytes``, ``checksum``). ``dataset verify`` checks the local part
of that claim, deterministically and without reading anything but the payload
files themselves (emercons/knowmade#119):

- a ``locations`` entry that is a URL (anything with a ``scheme://``) is
  remote: it is never fetched and never checked;
- any other entry is a local path. A relative path resolves against the wiki
  root (the directory holding ``config.md``), never against ``raw/`` or the
  manifest's own folder; an absolute path is used as written; ``~`` is not
  expanded. A payload may be a file or a directory;
- ``size_bytes``, when an integer, is compared with the size of every local
  *file* location; ``checksum``, when present, must be ``sha256:<64 hex>`` and
  is compared with the SHA-256 of every local *file* location. A directory is
  checked for existence only.

Findings, by ``kind``:

- ``dataset-missing`` (warning): a local location does not exist. A payload
  that lives on another machine is a normal state, so the default
  ``--fail-on critical`` does not fail on it.
- ``dataset-size`` (critical): ``size_bytes`` is not an integer, or differs
  from the file's size.
- ``dataset-checksum`` (critical): ``checksum`` is not ``sha256:<64 hex>``, or
  differs from the file's digest.
- ``dataset-manifest`` (warning): the manifest cannot be read (``lint`` reports
  the same file as C1/C2, so only this command emits it).

Manifests with status ``unavailable`` or ``archived`` are skipped: their
payload is documented as gone or no longer maintained.

Exit codes, as ``privacy scan``: 0 nothing at or above ``--fail-on``, 1
something is, 2 usage or configuration error.

The same check runs inside ``lint`` as C27 under ``--profile datasets``
(``check_datasets``), through ``rule_lint.issue_versioned``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import frontmatter as _frontmatter
from . import rule_lint as _rule_lint
from .lint_report import LintContext
from .roots import resolve_wiki_root_args

CHECKSUM_RE = re.compile(r"sha256:([0-9A-Fa-f]{64})")
# ``https://``, ``s3://``, ``file://``: a remote or opaque location, never read.
URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
SKIPPED_STATUSES = frozenset({"unavailable", "archived"})
SEVERITY_RANK = {"warning": 1, "critical": 2}
# The rule set that introduced C27 (info there, warning next, critical after).
INTRODUCED_IN = 3
CHUNK = 1024 * 1024


class DatasetError(Exception):
    """A usage or configuration error: exit code 2."""


@dataclass(frozen=True)
class DatasetFinding:
    kind: str
    severity: str
    dataset: str
    manifest: str
    location: str
    message: str

    def to_json(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "dataset": self.dataset,
            "manifest": self.manifest,
            "location": self.location,
            "message": self.message,
        }


def local_locations(fields: dict[str, Any]) -> list[str]:
    """The ``locations`` entries that are local paths, in manifest order."""
    value = fields.get("locations")
    items = value if isinstance(value, list) else [value] if isinstance(value, str) else []
    found: list[str] = []
    for item in items:
        if isinstance(item, str) and item.strip() and not URL_RE.match(item.strip()):
            found.append(item.strip())
    return found


def resolve_location(root: Path, location: str) -> Path:
    """Resolve a local ``locations`` entry: absolute as written, else against ``root``."""
    if os.path.isabs(location) or DRIVE_RE.match(location):
        return Path(location)
    return root / location


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_manifest_fields(root: Path, manifest: str, fields: dict[str, Any]) -> tuple[list[DatasetFinding], int]:
    """Check one manifest's local payloads; return ``(findings, payloads checked)``.

    ``manifest`` is the manifest's path as the findings name it (relative to
    the wiki root).
    """
    dataset = str(fields.get("dataset_id") or Path(manifest).parent.name)
    if str(fields.get("status") or "") in SKIPPED_STATUSES:
        return [], 0
    findings: list[DatasetFinding] = []

    def add(kind: str, severity: str, location: str, message: str) -> None:
        findings.append(DatasetFinding(kind, severity, dataset, manifest, location, message))

    size = fields.get("size_bytes")
    declared_size: int | None = None
    if size not in (None, ""):
        # The frontmatter reader yields scalars as text: ``size_bytes: 6`` is "6".
        if not isinstance(size, bool) and re.fullmatch(r"[0-9]+", str(size).strip()):
            declared_size = int(str(size).strip())
        else:
            add("dataset-size", "critical", "", f"size_bytes {size!r} is not a non-negative integer.")
    checksum = fields.get("checksum")
    declared_digest: str | None = None
    if checksum not in (None, ""):
        match = CHECKSUM_RE.fullmatch(str(checksum).strip())
        if match:
            declared_digest = match.group(1).lower()
        else:
            add("dataset-checksum", "critical", "", f"checksum {str(checksum)!r} is not sha256:<64 hex digits>.")

    checked = 0
    for location in local_locations(fields):
        path = resolve_location(root, location)
        try:
            exists = path.exists()
        except OSError:
            exists = False
        if not exists:
            add("dataset-missing", "warning", location, f"Local location {location} does not exist.")
            continue
        checked += 1
        if not path.is_file():
            continue
        try:
            actual_size = path.stat().st_size
            if declared_size is not None and actual_size != declared_size:
                add(
                    "dataset-size",
                    "critical",
                    location,
                    f"{location} is {actual_size} bytes; size_bytes declares {declared_size}.",
                )
            if declared_digest is not None:
                actual = sha256_of(path)
                if actual != declared_digest:
                    add(
                        "dataset-checksum",
                        "critical",
                        location,
                        f"{location} has sha256 {actual}; checksum declares {declared_digest}.",
                    )
        except OSError as exc:
            add("dataset-missing", "warning", location, f"Local location {location} cannot be read: {exc.strerror or exc}.")
    return findings, checked


def check_datasets(ctx: LintContext) -> None:
    """Lint profile ``datasets`` (C27): verify the payloads of every loaded manifest."""
    for doc in sorted(ctx.documents.values(), key=lambda item: str(item.path)):
        rel = ctx.rel(doc.path)
        parts = rel.split("/")
        if len(parts) != 3 or parts[0] != "datasets" or parts[2] != "MANIFEST.md":
            continue
        findings, _ = verify_manifest_fields(ctx.root, rel, doc.frontmatter)
        for finding in findings:
            _rule_lint.issue_versioned(
                ctx,
                INTRODUCED_IN,
                f"{finding.kind}: {finding.message}",
                doc.path,
                check="C27",
            )


def verify_root(root: Path) -> dict[str, Any]:
    """Verify every manifest under ``root/datasets``; the report ``dataset verify`` prints."""
    findings: list[DatasetFinding] = []
    datasets = payloads = 0
    manifests = sorted((root / "datasets").glob("*/MANIFEST.md")) if (root / "datasets").is_dir() else []
    for path in manifests:
        rel = path.relative_to(root).as_posix()
        try:
            fields = _frontmatter.read(path)[0]
        except (OSError, ValueError) as exc:  # FrontmatterError and UnicodeDecodeError are ValueErrors
            findings.append(
                DatasetFinding("dataset-manifest", "warning", path.parent.name, rel, "", f"Manifest cannot be read: {exc}")
            )
            continue
        datasets += 1
        found, checked = verify_manifest_fields(root, rel, fields)
        findings.extend(found)
        payloads += checked
    counts = {
        "critical": sum(1 for item in findings if item.severity == "critical"),
        "warning": sum(1 for item in findings if item.severity == "warning"),
        "datasets": datasets,
        "payloads": payloads,
    }
    return {"root": str(root), "findings": findings, "counts": counts}


def resolve_root(args: argparse.Namespace) -> Path:
    if args.path and (args.local or args.wiki):
        raise DatasetError("a path cannot be combined with --local or --wiki")
    if args.local and args.wiki:
        raise DatasetError("--local and --wiki cannot be combined")
    try:
        root = resolve_wiki_root_args(args).resolve(strict=False)
    except SystemExit as exc:
        raise DatasetError(str(exc.code)) from None
    if not root.is_dir():
        raise DatasetError(f"dataset verify root is not a directory: {root}")
    return root


def run_verify(args: argparse.Namespace) -> int:
    try:
        root = resolve_root(args)
    except DatasetError as exc:
        print(f"llm-wiki dataset: {exc}", file=sys.stderr)
        return 2
    report = verify_root(root)
    findings: list[DatasetFinding] = report["findings"]
    counts = report["counts"]
    threshold = SEVERITY_RANK[args.fail_on]
    blocking = [item for item in findings if SEVERITY_RANK[item.severity] >= threshold]
    if args.json:
        print(
            json.dumps(
                {
                    "root": report["root"],
                    "counts": counts,
                    "findings": [item.to_json() for item in findings],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        for item in findings:
            print(f"{item.severity} {item.kind} {item.manifest}: {item.message}")
        print(
            f"dataset verify: {counts['critical']} critical, {counts['warning']} warning "
            f"({counts['datasets']} datasets, {counts['payloads']} local payloads checked)"
        )
    return 1 if blocking else 0


def register_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Register the ``dataset`` namespace on a top-level subparsers action."""
    dataset = subparsers.add_parser(
        "dataset",
        help="Verify dataset payloads against manifests.",
    )
    sub = dataset.add_subparsers(dest="dataset_command", required=True)
    verify = sub.add_parser(
        "verify",
        help="Check local payloads: existence, size_bytes, sha256 checksum.",
        description="Exit 0 clean, 1 findings at or above --fail-on, 2 error.",
    )
    verify.add_argument("path", nargs="?", help="Wiki root.")
    verify.add_argument("--local", action="store_true", help="Verify ./.wiki.")
    verify.add_argument("--wiki", help="Named wiki.")
    verify.add_argument("--hub", help="Hub path for --wiki.")
    verify.add_argument(
        "--fail-on",
        choices=["warning", "critical"],
        default="critical",
        help="Lowest severity that exits 1.",
    )
    verify.add_argument("--json", action="store_true", help="Print JSON.")
    dataset.set_defaults(func=run_verify)
    return dataset


def run(args: argparse.Namespace) -> int:
    """Dispatch a parsed ``dataset`` namespace."""
    return run_verify(args)
