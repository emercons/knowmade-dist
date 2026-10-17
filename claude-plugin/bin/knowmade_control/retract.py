"""The ``retract`` namespace: remove a value from wiki-controlled local files.

Secret-safe scanning of the selected local scope for a value and its common
encoded forms, the dry-run/apply rewrite, log-entry removal, and the
sanitized report that never echoes the value. Moved verbatim from
``legacy_cli`` (emercons/knowmade#67 R1.4, ``KM-CLI-001``).
"""

from __future__ import annotations

import argparse
import base64
import codecs
import getpass
import json
import os
import re
import sys
import tempfile
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import fsio as _fsio
from .common import (
    is_under,
    read_registry,
)
from .markdown import append_log
from .roots import (
    expand_leading_tilde,
    resolve_hub_args as resolve_hub,
    resolve_registry_path,
    resolve_wiki_root_args as resolve_wiki_root,
)


RETRACTED_MARKER = "[RETRACTED]"
RETRACT_SKIP_NAMES = {".git", ".hg", ".svn"}
RETRACT_LOG_ENTRY_HEADER = re.compile(rb"^## \[\d{4}-\d{2}-\d{2}\] ")
# A shorter raw value matches unrelated content (a one-byte value rewrites most
# files), so it is refused unless ``--allow-short`` says the operator means it.
RETRACT_MIN_VALUE_BYTES = 8
# An encoded form (hex, base64, ...) shorter than this is likely to occur in
# unrelated bytes, so it is never generated, even for an allowed short value.
RETRACT_MIN_VARIANT_CHARS = 12
# Files are read in chunks of this size; a match that straddles two chunks is
# found through an overlap of ``longest pattern - 1`` bytes.
RETRACT_CHUNK_BYTES = 1024 * 1024


@dataclass
class RetractScan:
    files_scanned: int
    text_matches: list[tuple[Path, int]]
    binary_matches: list[tuple[Path, int]]
    log_entries_matched: int
    name_matches: list[tuple[Path, str]]
    symlink_matches: list[Path]
    skipped_control_paths: list[Path]
    errors: list[str]


def strip_one_line_ending(value: bytes) -> bytes:
    """Drop exactly one trailing ``\\r\\n`` or ``\\n``, as ``echo`` and ``printf '...\\n'`` add.

    Kept, the line ending made the pattern match only line-terminated copies
    of the value, and verification re-scanned with the same pattern, so a
    retraction could report "verified" with the secret still on disk (#92).
    """
    if value.endswith(b"\r\n"):
        return value[:-2]
    if value.endswith(b"\n"):
        return value[:-1]
    return value


def read_retract_value(args: argparse.Namespace) -> bytes:
    if args.stdin:
        value = strip_one_line_ending(sys.stdin.buffer.read())
    else:
        value = getpass.getpass("Value to retract: ").encode("utf-8")
    if not value:
        raise SystemExit("retract value is empty")
    if len(value) < RETRACT_MIN_VALUE_BYTES and not getattr(args, "allow_short", False):
        raise SystemExit(
            f"retract value is shorter than the {RETRACT_MIN_VALUE_BYTES}-byte minimum: a short "
            "value matches unrelated content throughout the scope. Re-run with --allow-short "
            "(and preferably --variants exact) if you really mean it."
        )
    if value == RETRACTED_MARKER.encode("utf-8"):
        raise SystemExit("retract value cannot be the replacement marker")
    return value


def retract_patterns(value: bytes, mode: str) -> tuple[list[bytes], list[str]]:
    byte_patterns = {value}
    name_patterns: set[str] = set()
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError:
        text = ""
    if text:
        name_patterns.add(text)
    if mode == "common" and text and len(value) >= 4:
        encoded = {
            json.dumps(text, ensure_ascii=True)[1:-1],
            urllib.parse.quote(text, safe=""),
            urllib.parse.quote_plus(text, safe=""),
            base64.b64encode(value).decode("ascii"),
            base64.urlsafe_b64encode(value).decode("ascii"),
            value.hex(),
        }
        encoded.update(item.rstrip("=") for item in list(encoded) if item.endswith("="))
        encoded.discard(text)
        # Short encodings (unpadded base64 of a few bytes, for example) occur in
        # unrelated content, so only forms of RETRACT_MIN_VARIANT_CHARS or more
        # are matched; the exact value above is always kept.
        encoded = {item for item in encoded if len(item) >= RETRACT_MIN_VARIANT_CHARS}
        for item in encoded:
            byte_patterns.add(item.encode("utf-8"))
            name_patterns.add(item)
    return (
        sorted(byte_patterns, key=lambda item: (-len(item), item)),
        sorted(name_patterns, key=lambda item: (-len(item), item)),
    )


def registry_retract_roots(hub: Path) -> list[Path]:
    roots = [hub]
    registry = hub / "wikis.json"
    if not registry.exists():
        return roots
    data = read_registry(hub)
    for entry in data.get("wikis", {}).values():
        if isinstance(entry, dict) and entry.get("path"):
            roots.append(resolve_registry_path(str(entry["path"]), hub))
    local_wikis = data.get("local_wikis", [])
    if isinstance(local_wikis, list):
        for entry in local_wikis:
            if isinstance(entry, str):
                roots.append(resolve_registry_path(entry, hub))
            elif isinstance(entry, dict) and entry.get("path"):
                roots.append(resolve_registry_path(str(entry["path"]), hub))
    return roots


def minimize_roots(paths: list[Path]) -> list[Path]:
    selected: list[Path] = []
    for path in paths:
        try:
            candidate = path.expanduser().resolve()
        except OSError:
            candidate = path.expanduser().absolute()
        if any(candidate == root or is_under(candidate, root) for root in selected):
            continue
        selected = [root for root in selected if not is_under(root, candidate)]
        selected.append(candidate)
        selected.sort(key=lambda item: (len(item.parts), str(item)))
    return selected


def resolve_retract_roots(args: argparse.Namespace) -> list[Path]:
    if args.everywhere and (args.path or args.local or args.wiki):
        raise SystemExit("--everywhere cannot be combined with a path, --local, or --wiki")
    if args.everywhere:
        hub = resolve_hub(args)
        roots = registry_retract_roots(hub)
        local = Path.cwd() / ".wiki"
        if local.exists():
            roots.append(local)
    elif args.path:
        roots = [expand_leading_tilde(str(args.path))]
    else:
        # Retraction is allowed to target archived material. Archive state is
        # wiki context, not a mutation gate for this control-plane operation.
        args.include_archived = True
        roots = [resolve_wiki_root(args)]
    roots = minimize_roots(roots)
    if not roots:
        raise SystemExit("no initialized retract scope found")
    return roots


def replace_name(value: str, patterns: list[str]) -> str:
    for pattern in patterns:
        value = value.replace(pattern, RETRACTED_MARKER)
    return value


def sanitized_text(value: str, patterns: list[str]) -> str:
    return replace_name(value, patterns)


def sanitized_error(exc: BaseException, patterns: list[str]) -> str:
    return sanitized_text(str(exc), patterns)


def without_matching_log_entries(data: bytes, patterns: list[bytes]) -> tuple[bytes, int]:
    """Remove structured log entries, or legacy standalone lines, containing a match."""
    kept: list[bytes] = []
    current_entry: list[bytes] = []
    removed = 0

    def flush_entry() -> None:
        nonlocal removed
        if not current_entry:
            return
        entry = b"".join(current_entry)
        if any(pattern in entry for pattern in patterns):
            removed += 1
        else:
            kept.extend(current_entry)
        current_entry.clear()

    for line in data.splitlines(keepends=True):
        if RETRACT_LOG_ENTRY_HEADER.match(line):
            flush_entry()
            current_entry.append(line)
        elif current_entry:
            current_entry.append(line)
        elif any(pattern in line for pattern in patterns):
            # Old or hand-written logs may have content outside structured entries.
            removed += 1
        else:
            kept.append(line)
    flush_entry()
    return b"".join(kept), removed


def pattern_regex(patterns: list[bytes]) -> re.Pattern[bytes]:
    """One alternation, longest pattern first, so the leftmost match is the longest."""
    ordered = sorted(set(patterns), key=lambda item: (-len(item), item))
    return re.compile(b"|".join(re.escape(item) for item in ordered))


def iter_stream_segments(handle: Any, regex: re.Pattern[bytes], max_pattern_len: int):
    """Yield ``(is_match, bytes)`` pieces of a binary stream, never holding it whole.

    The stream is read in ``RETRACT_CHUNK_BYTES`` chunks. Everything except the
    last ``max_pattern_len - 1`` bytes of the working buffer is final, so a match
    that straddles a chunk boundary is still found; memory use is bounded by the
    chunk size plus the longest pattern.
    """
    overlap = max(max_pattern_len - 1, 0)
    buf = b""
    while True:
        chunk = handle.read(RETRACT_CHUNK_BYTES)
        eof = not chunk
        buf += chunk
        limit = len(buf) if eof else len(buf) - overlap
        pos = 0
        for match in regex.finditer(buf):
            if match.start() >= limit:
                break
            if match.start() > pos:
                yield False, buf[pos : match.start()]
            yield True, match.group()
            pos = match.end()
        if eof:
            if pos < len(buf):
                yield False, buf[pos:]
            return
        if limit > pos:
            yield False, buf[pos:limit]
            pos = limit
        buf = buf[pos:]


def count_file_matches(path: Path, regex: re.Pattern[bytes], max_pattern_len: int) -> int:
    count = 0
    with path.open("rb") as handle:
        for is_match, _ in iter_stream_segments(handle, regex, max_pattern_len):
            if is_match:
                count += 1
    return count


def file_is_text(path: Path) -> bool:
    """True when the whole file is valid UTF-8 without NUL bytes, read in chunks."""
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(RETRACT_CHUNK_BYTES)
                if b"\x00" in chunk:
                    return False
                decoder.decode(chunk, final=not chunk)
                if not chunk:
                    return True
    except UnicodeDecodeError:
        return False


def scan_retract_roots(
    roots: list[Path],
    byte_patterns: list[bytes],
    name_patterns: list[str],
    max_file_bytes: int = 0,
) -> RetractScan:
    regex = pattern_regex(byte_patterns)
    max_pattern_len = max(len(item) for item in byte_patterns)
    text_matches: list[tuple[Path, int]] = []
    binary_matches: list[tuple[Path, int]] = []
    log_entries_matched = 0
    name_matches: list[tuple[Path, str]] = []
    symlink_matches: list[Path] = []
    skipped_control_paths: list[Path] = []
    errors: list[str] = []
    files_scanned = 0

    def walk_error(exc: OSError) -> None:
        path = Path(str(getattr(exc, "filename", "unknown")))
        errors.append(
            f"could not scan {sanitized_text(str(path), name_patterns)}: "
            f"{sanitized_error(exc, name_patterns)}"
        )

    for root in roots:
        if replace_name(root.name, name_patterns) != root.name:
            errors.append(
                "selected retract root name contains a match and must be moved manually: "
                f"{sanitized_text(str(root), name_patterns)}"
            )
        if not root.exists():
            errors.append(f"retract scope does not exist: {sanitized_text(str(root), name_patterns)}")
            continue
        if not root.is_dir():
            errors.append(f"retract scope is not a directory: {sanitized_text(str(root), name_patterns)}")
            continue
        for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False, onerror=walk_error):
            current_path = Path(current)
            kept_dirs: list[str] = []
            for name in dirnames:
                path = current_path / name
                if name in RETRACT_SKIP_NAMES:
                    skipped_control_paths.append(path)
                    continue
                renamed = replace_name(name, name_patterns)
                if renamed != name:
                    name_matches.append((path, renamed))
                if path.is_symlink():
                    try:
                        target = os.readlink(path)
                        if replace_name(target, name_patterns) != target:
                            symlink_matches.append(path)
                    except OSError as exc:
                        errors.append(
                            f"could not inspect {sanitized_text(str(path), name_patterns)}: "
                            f"{sanitized_error(exc, name_patterns)}"
                        )
                    continue
                kept_dirs.append(name)
            dirnames[:] = kept_dirs

            for name in filenames:
                path = current_path / name
                if name in RETRACT_SKIP_NAMES:
                    skipped_control_paths.append(path)
                    continue
                renamed = replace_name(name, name_patterns)
                if renamed != name:
                    name_matches.append((path, renamed))
                if path.is_symlink():
                    try:
                        target = os.readlink(path)
                        if replace_name(target, name_patterns) != target:
                            symlink_matches.append(path)
                    except OSError as exc:
                        errors.append(
                            f"could not inspect {sanitized_text(str(path), name_patterns)}: "
                            f"{sanitized_error(exc, name_patterns)}"
                        )
                    continue
                try:
                    if max_file_bytes and path.stat().st_size > max_file_bytes:
                        errors.append(
                            f"skipped {sanitized_text(str(path), name_patterns)}: larger than "
                            f"--max-file-bytes ({max_file_bytes}); the scan is incomplete"
                        )
                        continue
                    occurrences = count_file_matches(path, regex, max_pattern_len)
                    is_text = bool(occurrences) and file_is_text(path)
                except OSError as exc:
                    errors.append(
                        f"could not read {sanitized_text(str(path), name_patterns)}: "
                        f"{sanitized_error(exc, name_patterns)}"
                    )
                    continue
                files_scanned += 1
                if not occurrences:
                    continue
                target = text_matches if is_text else binary_matches
                target.append((path, occurrences))
                if is_text and path.name == "log.md":
                    # Log entries are structural, so a log is read whole; logs are small.
                    try:
                        _, matched_entries = without_matching_log_entries(
                            path.read_bytes(), byte_patterns
                        )
                    except OSError as exc:
                        errors.append(
                            f"could not read {sanitized_text(str(path), name_patterns)}: "
                            f"{sanitized_error(exc, name_patterns)}"
                        )
                        continue
                    log_entries_matched += matched_entries

    return RetractScan(
        files_scanned=files_scanned,
        text_matches=text_matches,
        binary_matches=binary_matches,
        log_entries_matched=log_entries_matched,
        name_matches=name_matches,
        symlink_matches=symlink_matches,
        skipped_control_paths=skipped_control_paths,
        errors=errors,
    )


def atomic_replace_patterns(path: Path, patterns: list[bytes]) -> int:
    """Rewrite ``path`` with every match replaced, streaming through a temp file."""
    regex = pattern_regex(patterns)
    max_pattern_len = max(len(item) for item in patterns)
    replacement = RETRACTED_MARKER.encode("utf-8")
    count = 0
    mode = path.stat().st_mode & 0o7777
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out, path.open("rb") as source:
            for is_match, piece in iter_stream_segments(source, regex, max_pattern_len):
                if is_match:
                    count += 1
                    out.write(replacement)
                else:
                    out.write(piece)
            out.flush()
            os.fsync(out.fileno())
        if count:
            os.chmod(tmp, mode)
            _fsio._replace(tmp, path)
            _fsio._fsync_directory(path.parent)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return count


def atomic_remove_matching_log_entries(path: Path, patterns: list[bytes]) -> int:
    data = path.read_bytes()
    rewritten, removed = without_matching_log_entries(data, patterns)
    if not removed:
        return 0
    mode = path.stat().st_mode & 0o7777
    _fsio.atomic_write_bytes(path, rewritten, mode=mode)
    return removed


def apply_retract(
    scan: RetractScan,
    byte_patterns: list[bytes],
    delete_binary_matches: bool,
    remove_from_logs: bool,
    name_patterns: list[str],
) -> tuple[int, int, int, int, list[str]]:
    rewritten = 0
    deleted = 0
    renamed = 0
    log_entries_removed = 0
    errors: list[str] = []
    for path, _ in scan.text_matches:
        try:
            if remove_from_logs and path.name == "log.md":
                removed = atomic_remove_matching_log_entries(path, byte_patterns)
                log_entries_removed += removed
                changed = bool(removed)
            else:
                changed = bool(atomic_replace_patterns(path, byte_patterns))
            if changed:
                rewritten += 1
        except OSError as exc:
            errors.append(
                f"could not rewrite {sanitized_text(str(path), name_patterns)}: "
                f"{sanitized_error(exc, name_patterns)}"
            )
    if delete_binary_matches:
        for path, _ in scan.binary_matches:
            try:
                path.unlink()
                deleted += 1
            except OSError as exc:
                errors.append(
                    f"could not delete {sanitized_text(str(path), name_patterns)}: "
                    f"{sanitized_error(exc, name_patterns)}"
                )

    # Rename deepest entries first so parent renames do not invalidate child paths.
    for path, new_name in sorted(scan.name_matches, key=lambda item: len(item[0].parts), reverse=True):
        if not path.exists() and not path.is_symlink():
            continue
        destination = path.with_name(new_name)
        if destination.exists() or destination.is_symlink():
            errors.append(
                "could not rename "
                f"{sanitized_text(str(path), name_patterns)}: destination already exists"
            )
            continue
        try:
            path.rename(destination)
            renamed += 1
        except OSError as exc:
            errors.append(
                f"could not rename {sanitized_text(str(path), name_patterns)}: "
                f"{sanitized_error(exc, name_patterns)}"
            )
    return rewritten, deleted, renamed, log_entries_removed, errors


def retract_report(
    args: argparse.Namespace,
    roots: list[Path],
    scan: RetractScan,
    name_patterns: list[str],
    rewritten: int = 0,
    deleted: int = 0,
    renamed: int = 0,
    log_entries_removed: int = 0,
    verification: RetractScan | None = None,
    value_length: int | None = None,
) -> dict[str, Any]:
    unresolved_binary = len(scan.binary_matches) if not args.delete_binary_matches else 0
    verification_matches = 0
    verification_errors: list[str] = []
    if verification is not None:
        verification_matches = (
            len(verification.text_matches)
            + len(verification.binary_matches)
            + len(verification.name_matches)
            + len(verification.symlink_matches)
        )
        verification_errors = verification.errors
    errors = scan.errors + verification_errors
    if errors:
        status = "incomplete"
    elif not args.apply:
        status = "dry-run"
    elif unresolved_binary or scan.symlink_matches or verification_matches:
        status = "incomplete"
    else:
        status = "verified"

    matched_paths = {
        sanitized_text(str(path), name_patterns)
        for path, _ in scan.text_matches + scan.binary_matches
    }
    matched_paths.update(
        sanitized_text(str(path), name_patterns) for path, _ in scan.name_matches
    )
    matched_paths.update(sanitized_text(str(path), name_patterns) for path in scan.symlink_matches)
    return {
        "status": status,
        "mode": "apply" if args.apply else "dry-run",
        "value_length_bytes": value_length,
        "scope": [sanitized_text(str(path), name_patterns) for path in roots],
        "files_scanned": scan.files_scanned,
        "text_files_matched": len(scan.text_matches),
        "binary_files_matched": len(scan.binary_matches),
        "log_entries_matched": scan.log_entries_matched,
        "path_names_matched": len(scan.name_matches),
        "symlink_targets_matched": len(scan.symlink_matches),
        "files_rewritten": rewritten,
        "binary_files_deleted": deleted,
        "log_entries_removed": log_entries_removed,
        "paths_renamed": renamed,
        "remove_from_logs": args.remove_from_logs,
        "remaining_matches": verification_matches if verification is not None else None,
        "skipped_version_control_paths": len(scan.skipped_control_paths),
        "matched_paths": sorted(matched_paths),
        "errors": errors,
    }


def print_retract_report(report: dict[str, Any]) -> None:
    print(f"Retraction status: {report['status']}")
    print(f"Mode: {report['mode']}")
    if report.get("value_length_bytes") is not None:
        print(f"Value length: {report['value_length_bytes']} bytes")
    print(f"Files scanned: {report['files_scanned']}")
    print(
        "Matches: "
        f"{report['text_files_matched']} text files, "
        f"{report['binary_files_matched']} binary files, "
        f"{report['log_entries_matched']} log entries, "
        f"{report['path_names_matched']} path names, "
        f"{report['symlink_targets_matched']} symlink targets"
    )
    if report["mode"] == "apply":
        print(
            "Applied: "
            f"{report['files_rewritten']} files rewritten, "
            f"{report['binary_files_deleted']} binary files deleted, "
            f"{report['log_entries_removed']} log entries removed, "
            f"{report['paths_renamed']} paths renamed"
        )
        print(f"Remaining matches: {report['remaining_matches']}")
    if report["matched_paths"]:
        print("Matched paths:")
        for path in report["matched_paths"][:100]:
            print(f"- {path}")
        if len(report["matched_paths"]) > 100:
            print(f"- ... {len(report['matched_paths']) - 100} more")
    if report["skipped_version_control_paths"]:
        print(
            "Boundary: hidden version-control metadata was not scanned or changed "
            f"({report['skipped_version_control_paths']} paths)."
        )
    for error in report["errors"]:
        print(f"Error: {error}", file=sys.stderr)
    if report["mode"] == "dry-run":
        print("Dry run only. Re-run with --apply to write changes.")


def run_retract(args: argparse.Namespace) -> int:
    value = read_retract_value(args)
    byte_patterns, name_patterns = retract_patterns(value, args.variants)
    try:
        roots = resolve_retract_roots(args)
    except SystemExit as exc:
        raise SystemExit(sanitized_text(str(exc), name_patterns)) from exc
    max_file_bytes = getattr(args, "max_file_bytes", 0) or 0
    scan = scan_retract_roots(roots, byte_patterns, name_patterns, max_file_bytes)
    rewritten = deleted = renamed = log_entries_removed = 0
    verification: RetractScan | None = None
    if args.apply:
        rewritten, deleted, renamed, log_entries_removed, apply_errors = apply_retract(
            scan,
            byte_patterns,
            args.delete_binary_matches,
            args.remove_from_logs,
            name_patterns,
        )
        scan.errors.extend(apply_errors)
        if rewritten or deleted or renamed:
            for root in roots:
                try:
                    append_log(
                        root,
                        "retract",
                        "user-directed data retraction; local content updated",
                    )
                    log_path = root / "log.md"
                    if log_path.exists():
                        if args.remove_from_logs:
                            log_entries_removed += atomic_remove_matching_log_entries(
                                log_path, byte_patterns
                            )
                        else:
                            atomic_replace_patterns(log_path, byte_patterns)
                except OSError as exc:
                    scan.errors.append(
                        "could not append operation log at "
                        f"{sanitized_text(str(root), name_patterns)}: "
                        f"{sanitized_error(exc, name_patterns)}"
                    )
        verification = scan_retract_roots(roots, byte_patterns, name_patterns, max_file_bytes)
    report = retract_report(
        args,
        roots,
        scan,
        name_patterns,
        rewritten=rewritten,
        deleted=deleted,
        renamed=renamed,
        log_entries_removed=log_entries_removed,
        verification=verification,
        value_length=len(value),
    )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_retract_report(report)
    return 2 if report["status"] == "incomplete" else 0


def register_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Register the ``retract`` namespace on a top-level subparsers action."""
    retract = subparsers.add_parser(
        "retract",
        help="Remove a value from wiki-controlled local files.",
        description=(
            "Find a value and common encoded forms without placing the value in command "
            "arguments. Dry-run is the default; pass --apply to rewrite matched text, "
            "rename matched paths, and verify the selected local scope."
        ),
    )
    retract.add_argument("path", nargs="?", help="Wiki root path to scan.")
    retract.add_argument(
        "--stdin",
        action="store_true",
        help=(
            "Read the value from standard input instead of a hidden prompt; one "
            "trailing newline (LF or CRLF) is dropped, as echo and printf add one."
        ),
    )
    retract.add_argument("--apply", action="store_true", help="Apply changes and verify again.")
    retract.add_argument("--local", action="store_true", help="Target .wiki/ in the current directory.")
    retract.add_argument("--wiki", help="Target a named wiki, including an archived wiki.")
    retract.add_argument("--hub", help="Override hub path resolution.")
    retract.add_argument(
        "--everywhere",
        action="store_true",
        help="Scan the hub, registered wiki roots, archives, sessions, and current .wiki/.",
    )
    retract.add_argument(
        "--variants",
        choices=["exact", "common"],
        default="common",
        help="Match exact bytes only or also common JSON, URL, base64, and hex forms.",
    )
    retract.add_argument(
        "--delete-binary-matches",
        action="store_true",
        help="Delete whole binary files containing a match; otherwise report them as unresolved.",
    )
    retract.add_argument(
        "--remove-from-logs",
        action="store_true",
        help=(
            "Remove complete matching entries from log.md files instead of replacing "
            "the selected value inside those entries."
        ),
    )
    retract.add_argument(
        "--allow-short",
        action="store_true",
        help=(
            f"Accept a value shorter than {RETRACT_MIN_VALUE_BYTES} bytes. Short values match "
            "unrelated content throughout the scope; combine with --variants exact. Encoded "
            f"forms shorter than {RETRACT_MIN_VARIANT_CHARS} characters are never matched."
        ),
    )
    retract.add_argument(
        "--max-file-bytes",
        type=int,
        default=0,
        metavar="N",
        help=(
            "Skip files larger than N bytes and report each as an error (the result is then "
            "incomplete). 0, the default, scans files of any size; files are read in chunks."
        ),
    )
    retract.add_argument("--json", action="store_true", help="Print a machine-readable report.")
    retract.set_defaults(func=run_retract)
    return retract


def run(args: argparse.Namespace) -> int:
    """Dispatch a parsed ``retract`` namespace."""
    return run_retract(args)
