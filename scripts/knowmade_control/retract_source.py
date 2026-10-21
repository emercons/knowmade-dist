"""``retract --source``: retract an ingested raw source by its path.

The second mode of ``retract-protocol.md`` (§ Source path). Where the literal
mode scans for a value, this one starts from ``raw/<type>/<file>.md``: it maps
every reference to the source in the selected wiki root (the protocol's
"Identify and map") and, with ``--apply``, does the part of "Apply" that is
mechanical:

- delete the raw source (the protocol's explicit exception to raw immutability);
- remove the matching entries of every ``sources:`` frontmatter list (a scalar
  value becomes ``sources: []``), resolved the way lint resolves a source
  reference (exact path, ``../`` form, slug fallback), and nothing else in those
  files;
- repair the generated indexes through ``index rebuild`` (dead rows go) and
  decrement the master ``Sources: N`` statistic when it was current;
- with ``--remove-from-logs``, remove each complete ``log.md`` entry naming the
  source (topic log, and the owning hub's log);
- append the generic ``retract`` entry to the topic and hub logs.

Everything that needs a judgment stays with the agent and is listed, never
edited: body links, wiki-links, footnotes and prose mentions, "claims supported
only by that source", other frontmatter keys. Applying with such references
left reports ``incomplete`` (exit 2); once the agent has dealt with them,
``retract --source <same path>`` runs again against the now-absent source and
reports ``verified`` when nothing is left (the protocol's step 4).

Matching is by the source's path, never by its filename alone: a reference counts
only when it resolves to exactly this file (a ``sources:`` entry, a markdown link
or an index row relative to the file holding it, the full ``raw/<type>/<file>``
path in text and logs), so a same-named file under another ``raw/<type>/``, or an
external URL ending in the same name, is left alone.

``log.md`` entries naming the source are not a remaining match unless
``--remove-from-logs`` was asked for: the protocol makes that removal optional,
so the report counts them as ``log_entries_kept`` instead.

The report names paths, line numbers and kinds only; it never echoes content.
Dry-run is the default; ``--apply`` is the only mutation switch.
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from . import frontmatter as _frontmatter
from . import fsio as _fsio
from . import index_cli as _index_cli
from .common import strip_matching_quotes
from .lint_docs import content_markdown_files, resolve_source_ref
from .lint_links import FOOTNOTE_DEF_RE, slugify
from .lint_report import LintContext
from .markdown import append_log, frontmatter_bounds, parse_frontmatter_block, yaml_quote
from .roots import expand_leading_tilde, is_hub, resolve_wiki_root_args, windows_absolute_path
from .source_lifecycle import hub_of_topic

SCHEMA = "knowmade/retract-source/v1"
SKIP_DIRS = {".git", ".hg", ".svn"}
LOG_ENTRY_HEADER = re.compile(r"^## \[\d{4}-\d{2}-\d{2}\] ")
NON_SOURCE_NAMES = {"_index.md", "config.md", "log.md"}
GENERIC_LOG = "user-directed source retraction; local content updated"
STATS_RE = re.compile(r"(?m)^(- Sources: )(\d+)\b")

# kind -> action. Only ``agent`` kinds need a person (or the agent) to decide.
ACTIONS = {
    "frontmatter-source": "remove-frontmatter-source",
    "index-row": "index-rebuild",
    "log-entry": "remove-log-entry",
}
# Flags of the literal mode that make no sense here (retract.py passes the namespace).
LITERAL_ONLY = (
    ("stdin", "--stdin"), ("everywhere", "--everywhere"), ("allow_short", "--allow-short"),
    ("delete_binary_matches", "--delete-binary-matches"),
)


class RetractSourceError(Exception):
    """A usage error: exit code 2, nothing changed."""


@dataclass
class Hit:
    path: str
    line: int
    kind: str

    @property
    def action(self) -> str:
        return ACTIONS.get(self.kind, "agent")

    def to_json(self) -> dict[str, Any]:
        return {"path": self.path, "line": self.line, "kind": self.kind, "action": self.action}


@dataclass
class Scan:
    hits: list[Hit] = field(default_factory=list)
    # rel path -> sources: entries (as written) that resolve to the source
    entries: dict[str, list[str]] = field(default_factory=dict)
    log_entries: dict[Path, int] = field(default_factory=dict)
    files_scanned: int = 0
    binary_skipped: int = 0
    errors: list[str] = field(default_factory=list)


def _is_link(path: Path) -> bool:
    """A symbolic link or a Windows junction (a junction is a directory that realpath redirects)."""
    if path.is_symlink():
        return True
    isjunction = getattr(os.path, "isjunction", None)
    if isjunction is not None and isjunction(path):
        return True
    parent = os.path.realpath(path.parent)
    return os.path.normcase(os.path.realpath(path)) != os.path.normcase(os.path.join(parent, path.name))


def ensure_contained(root: Path, path: Path) -> None:
    """Refuse a source that is, or sits behind, a link, or that resolves outside ``root/raw``.

    The check is on the file system, not on the text of the path: ``raw`` or any
    directory below it may be a symbolic link or junction, and deleting through
    one would remove a file outside the wiki. Components that do not exist yet
    (a verification run after the delete) are not links and are skipped.
    """
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise RetractSourceError(f"{path} is outside the selected wiki root") from exc
    current = root
    for part in relative.parts:
        current = current / part
        if os.path.lexists(current) and _is_link(current):
            shown = current.relative_to(root).as_posix()
            raise RetractSourceError(f"{shown} is a symbolic link or junction; retract the real file by its own path, inside the wiki")
    raw = Path(os.path.realpath(root / "raw"))
    real = Path(os.path.realpath(path))
    if raw != Path(os.path.realpath(root)) / "raw" or (real != raw and raw not in real.parents):
        raise RetractSourceError(f"{relative.as_posix()} does not resolve to a file inside this wiki's raw/ directory")


def resolve_source(root: Path, value: str) -> tuple[str, Path]:
    """``(root-relative path, absolute path)`` of the raw source ``value`` names.

    ``raw/<type>/<file>`` need not exist (a verification run after the source
    was deleted); a bare filename must match exactly one file under ``raw/``.
    """
    text = value.strip().replace("\\", "/")
    if not text:
        raise RetractSourceError("--source is empty")
    candidate = Path(text)
    if candidate.is_absolute():
        try:
            text = candidate.resolve().relative_to(root).as_posix()
        except ValueError as exc:
            raise RetractSourceError(f"--source {value!r} is outside the selected wiki root") from exc
    text = posixpath.normpath(text)
    if text.startswith("../") or text == "..":
        raise RetractSourceError(f"--source {value!r} is outside the selected wiki root")
    parts = text.split("/")
    name = parts[-1]
    if name in NON_SOURCE_NAMES or name.lower().startswith("readme") or name.startswith("."):
        raise RetractSourceError(
            f"{name} is an index or documentation file, not a raw source; pass the raw/<type>/<file> path of an ingested source"
        )
    if parts[0] == "raw":
        if len(parts) < 3:
            raise RetractSourceError(f"--source must name a file under raw/<type>/, not {value!r}")
        path = root / text
    else:
        found = []
        if "/" not in text and (root / "raw").is_dir():
            found = sorted(item for item in (root / "raw").rglob("*") if item.name == text and item.is_file())
        if not found:
            raise RetractSourceError(f"no source named {value!r} under raw/; pass its raw/<type>/<file> path")
        if len(found) > 1:
            names = ", ".join(item.relative_to(root).as_posix() for item in found)
            raise RetractSourceError(f"{value!r} matches several sources ({names}); pass the full raw/ path")
        path = found[0]
        text = path.relative_to(root).as_posix()
    ensure_contained(root, path)
    if path.is_dir():
        raise RetractSourceError(f"{text} is a directory")
    return text, path


def path_pattern(rel: str) -> re.Pattern[str]:
    """Match the source's full wiki-relative path (plain or URL-encoded), not its filename alone."""
    forms = {re.escape(rel), re.escape(quote(rel))}
    return re.compile(r"(?<![\w-])(?:" + "|".join(sorted(forms, key=len, reverse=True)) + r")(?![\w-])(?!\.\w)")


MD_LINK = re.compile(r"\]\(\s*(<[^>]*>?|[^)]*)")


def _markdown_names(root: Path):
    for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS]
        rel_dir = Path(current).relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        for name in filenames:
            if name.lower().endswith(".md"):
                yield posixpath.join(rel_dir, name)


class Matcher:
    """What counts as a reference to the one source ``rel``.

    A reference counts when it names this path: the full ``raw/<type>/<file>``
    path, or a relative markdown link that resolves to it from the file holding
    it. ``[[stem]]`` counts only while no other markdown file has that stem. A
    filename alone never counts, because the same name may exist elsewhere.
    """

    def __init__(self, root: Path, rel: str) -> None:
        self.root = root
        self.rel = rel
        self.path_rx = path_pattern(rel)
        stem = posixpath.splitext(posixpath.basename(rel))[0]
        self.wikilink: re.Pattern[str] | None = None
        if not any(
            posixpath.splitext(posixpath.basename(item))[0] == stem and item != rel for item in _markdown_names(root)
        ):
            self.wikilink = re.compile(r"\[\[" + re.escape(stem) + r"[\]|#]")

    def search(self, text: str) -> bool:
        """Full-path match: what log entries are held to."""
        return self.path_rx.search(text) is not None

    def line_refers(self, owner_rel: str, line: str) -> bool:
        if self.path_rx.search(line) or (self.wikilink is not None and self.wikilink.search(line)):
            return True
        base = posixpath.dirname(owner_rel)
        for found in MD_LINK.finditer(line):
            target = found.group(1).strip()
            if target.startswith("<"):
                target = target[1:].split(">", 1)[0]
            candidates = {target, target.split()[0] if target.split() else ""}
            for candidate in candidates:
                candidate = unquote(re.split(r"[#?]", candidate, maxsplit=1)[0])
                if not candidate or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", candidate) or candidate.startswith("/"):
                    continue
                if posixpath.normpath(posixpath.join(base, candidate)) == self.rel:
                    return True
        return False

    def entry_refers(
        self, ctx: LintContext, owner: Path, entry: str, wiki_source: bool, target_exists: bool, slugs: set[str]
    ) -> bool:
        """A ``sources:`` entry that resolves to exactly this file (an external URL never does)."""
        ref = strip_matching_quotes(entry.strip())
        if ref.startswith(("http://", "https://")):
            return False
        goal = os.path.normcase(os.path.normpath(self.root / self.rel))
        windows_ref = windows_absolute_path(ref)
        if windows_ref is not None or Path(ref).is_absolute():
            candidates = [windows_ref or Path(ref)]
        elif ref.startswith(("../", "./")):
            candidates = [owner.parent / ref]
        else:
            candidates = [self.root / ref] + ([self.root.parent / ref] if self.root.name == ".wiki" else [])
        if any(os.path.normcase(os.path.normpath(item)) == goal for item in candidates):
            return True
        try:
            resolved = resolve_source_ref(ctx, owner, ref, wiki_source=wiki_source)
        except OSError:
            return False
        if resolved is not None:
            return target_exists and os.path.normcase(os.path.normpath(resolved)) == goal
        # Nothing resolves: with the file gone, lint's slug fallback is all that can still point at it.
        return wiki_source and not target_exists and slugify(Path(ref).stem) in slugs


def read_text(path: Path) -> str | None:
    """The file as UTF-8 text (BOM dropped), or ``None`` for a binary file."""
    data = path.read_bytes()
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def _kind_of_body_line(line: str) -> str:
    if FOOTNOTE_DEF_RE.match(line):
        return "footnote"
    if "[[" in line:
        return "wikilink"
    if "](" in line:
        return "link"
    return "mention"


def _entries_of(fields: dict[str, Any]) -> list[str]:
    value = fields.get("sources")
    if isinstance(value, list):
        return [str(item) for item in value]
    return [value] if isinstance(value, str) and value else []


def count_log_entries(text: str, rx: Matcher) -> int:
    """Complete ``log.md`` entries (or stray legacy lines) naming the source."""
    entries: list[list[str]] = []
    for line in text.split("\n"):
        if LOG_ENTRY_HEADER.match(line) or not entries:
            entries.append([line])
        else:
            entries[-1].append(line)
    return sum(1 for entry in entries if rx.search("\n".join(entry)))


def without_log_entries(text: str, rx: Matcher) -> tuple[str, int]:
    """``text`` minus the entries naming the source (unrelated entries byte for byte), and how many went."""
    kept: list[str] = []
    removed = 0
    current: list[str] = []

    def flush() -> None:
        nonlocal removed
        if current and rx.search("".join(current)):
            removed += 1
        else:
            kept.extend(current)
        current.clear()

    for line in text.splitlines(keepends=True):
        if LOG_ENTRY_HEADER.match(line):
            flush()
            current.append(line)
        elif current:
            current.append(line)
        elif rx.search(line):
            removed += 1
        else:
            kept.append(line)
    flush()
    return "".join(kept), removed


def scan_root(root: Path, rel: str, source: Path, rx: Matcher, ctx: LintContext) -> Scan:
    scan = Scan()
    target_exists = source.exists()
    # With the file gone only lint's slug fallback can still match a bare slug.
    stem = slugify(source.stem)
    slugs = {stem, re.sub(r"^\d{4}-\d{2}-\d{2}-", "", stem)}
    for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        dirnames[:] = sorted(name for name in dirnames if name not in SKIP_DIRS and not _is_link(Path(current, name)))
        for name in sorted(filenames):
            path = Path(current) / name
            if path == source or path.is_symlink():
                continue
            relpath = path.relative_to(root).as_posix()
            try:
                text = read_text(path)
            except OSError as exc:
                scan.errors.append(f"could not read {relpath}: {exc}")
                continue
            scan.files_scanned += 1
            if text is None:
                scan.binary_skipped += 1
                continue
            lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            if name == "log.md":
                count = count_log_entries(text, rx)
                if count:
                    scan.log_entries[path] = count
                continue
            fm_end = 0
            hit_lines: dict[int, str] = {}
            if name.lower().endswith(".md"):
                bounds = frontmatter_bounds("\n".join(lines))
                if bounds is not None:
                    block = "\n".join(lines)[bounds[0]:bounds[1]]
                    fm_end = 2 + block.count("\n")  # file line of the block's last line
                    entries = _entries_of(parse_frontmatter_block(block))
                    top = relpath.split("/")[0]
                    matched = [entry for entry in entries if rx.entry_refers(ctx, path, entry, top == "wiki", target_exists, slugs)]
                    if matched:
                        scan.entries[relpath] = matched
                        hit_lines.update(_source_lines(lines[1:fm_end], matched, offset=2))
            for index, line in enumerate(lines):
                number = index + 1
                if number in hit_lines:
                    continue
                if not rx.line_refers(relpath, line):
                    continue
                if number <= fm_end:
                    hit_lines[number] = "frontmatter-other"
                elif name == "_index.md":
                    hit_lines[number] = "index-row" if line.lstrip().startswith(("|", "- ", "* ")) else "index-text"
                else:
                    hit_lines[number] = _kind_of_body_line(line)
            scan.hits.extend(Hit(relpath, number, kind) for number, kind in sorted(hit_lines.items()))
    return scan


def _source_lines(block_lines: list[str], matched: list[str], offset: int) -> dict[int, str]:
    """Lines (1-based in the file) of the ``sources:`` key holding ``matched`` entries."""
    found: dict[int, str] = {}
    in_sources = False
    wanted = set(matched)
    for index, line in enumerate(block_lines):
        key = re.match(r"^([A-Za-z_][\w-]*)\s*:(.*)$", line)
        if key:
            in_sources = key.group(1) == "sources"
            if in_sources and (key.group(2).strip().startswith("[") or strip_matching_quotes(key.group(2).strip()) in wanted):
                found[index + offset] = "frontmatter-source"
        elif in_sources and re.match(r"^\s+-\s", line):
            item = strip_matching_quotes(line.strip()[1:].strip())
            if item in wanted:
                found[index + offset] = "frontmatter-source"
    return found


def drop_entries(block: str, matched: list[str]) -> str:
    """The frontmatter block with the ``matched`` ``sources:`` entries removed; other lines untouched."""
    wanted = set(matched)
    out: list[str] = []
    in_sources = False
    sources_line = -1
    kept_items = 0
    for line in block.split("\n"):
        key = re.match(r"^([A-Za-z_][\w-]*)\s*:(.*)$", line)
        if key:
            in_sources = key.group(1) == "sources"
            if in_sources:
                if key.group(2).strip().startswith("["):  # flow list: rebuild from the parsed entries
                    remaining = [entry for entry in _entries_of(parse_frontmatter_block(block)) if entry not in wanted]
                    return _frontmatter.set_field_in_block(
                        block, "sources", [yaml_quote(entry) for entry in remaining] if remaining else "[]"
                    )
                if strip_matching_quotes(key.group(2).strip()) in wanted:  # scalar value: the key stays, emptied
                    out.append("sources: []")
                    continue
                sources_line = len(out)
        elif in_sources and re.match(r"^\s+-\s", line):
            if strip_matching_quotes(line.strip()[1:].strip()) in wanted:
                continue
            kept_items += 1
        out.append(line)
    if sources_line >= 0 and kept_items == 0 and out[sources_line].rstrip().endswith(":"):
        out[sources_line] = out[sources_line].rstrip() + " []"
    return "\n".join(out)


def _remove_logs(path: Path, rx: Matcher) -> int:
    data = path.read_bytes()
    text = data.decode("utf-8")
    rewritten, removed = without_log_entries(text, rx)
    if removed:
        _fsio.atomic_write_bytes(path, rewritten.encode("utf-8"), mode=path.stat().st_mode & 0o7777)
    return removed


def _decrement_sources(root: Path, before: int) -> bool:
    """Lower the master ``Sources: N`` statistic by one when it matched the count before the delete."""
    index = root / "_index.md"
    if not index.is_file():
        return False
    text = index.read_bytes().decode("utf-8")
    match = STATS_RE.search(text)
    if match is None or int(match.group(2)) != before:
        return False
    rewritten = text[: match.start(2)] + str(before - 1) + text[match.end(2):]
    _fsio.atomic_write_bytes(index, rewritten.encode("utf-8"), mode=index.stat().st_mode & 0o7777)
    return True


def select_root(args: argparse.Namespace) -> Path:
    for attr, flag in LITERAL_ONLY:
        if getattr(args, attr, False):
            raise RetractSourceError(f"{flag} belongs to the literal-value mode and cannot be combined with --source")
    if getattr(args, "max_file_bytes", 0):
        raise RetractSourceError("--max-file-bytes belongs to the literal-value mode and cannot be combined with --source")
    if args.path and (args.local or args.wiki):
        raise RetractSourceError("a path, --local and --wiki cannot be combined")
    if args.path:
        root = expand_leading_tilde(str(args.path))
    else:
        # Retraction may target archived material: archive state is context, not a gate.
        args.include_archived = True
        try:
            root = resolve_wiki_root_args(args)
        except SystemExit as exc:
            raise RetractSourceError(str(exc.code)) from None
    root = root.resolve()
    if not root.is_dir():
        raise RetractSourceError(f"no wiki root at {root}")
    if is_hub(root):
        raise RetractSourceError(f"{root} is a hub; pass --wiki <slug>, --local or a topic path")
    return root


def run_source(args: argparse.Namespace) -> int:
    try:
        root = select_root(args)
        rel, source = resolve_source(root, args.source)
    except RetractSourceError as exc:
        print(f"retract --source: {exc}", file=sys.stderr)
        return 2
    rx = Matcher(root, rel)
    ctx = LintContext(root)
    scan = scan_root(root, rel, source, rx, ctx)
    hub = hub_of_topic(root)
    hub_log = hub / "log.md" if hub is not None else None
    if hub_log is not None and hub_log.is_file():
        try:
            text = read_text(hub_log)
            count = count_log_entries(text, rx) if text is not None else 0
        except OSError as exc:
            scan.errors.append(f"could not read hub log.md: {exc}")
            count = 0
        if count:
            scan.log_entries[hub_log] = count

    existed = source.exists()
    changes: dict[str, Any] = {
        "source_deleted": False, "frontmatter_entries_removed": 0, "files_edited": [],
        "index_changes": [], "log_entries_removed": 0, "master_sources_decremented": False,
    }
    errors = list(scan.errors)
    final = scan
    if args.apply:
        sources_before = len(content_markdown_files(root / "raw"))
        for relpath, matched in sorted(scan.entries.items()):
            try:
                if _frontmatter.update(root / relpath, lambda block, matched=matched: drop_entries(block, matched)):
                    changes["files_edited"].append(relpath)
                    changes["frontmatter_entries_removed"] += len(matched)
            except (OSError, _frontmatter.FrontmatterError, UnicodeDecodeError) as exc:
                errors.append(f"could not edit {relpath}: {exc}")
        if existed:
            try:
                ensure_contained(root, source)  # again, at the moment of the write
                source.unlink()
                changes["source_deleted"] = True
            except (OSError, RetractSourceError) as exc:
                errors.append(f"could not delete {rel}: {exc}")
        if changes["source_deleted"]:
            index_changes, left = _index_cli.rebuild_indexes(root)
            changes["index_changes"] = index_changes
            if _decrement_sources(root, sources_before):
                changes["master_sources_decremented"] = True
        if args.remove_from_logs:
            for log in sorted(scan.log_entries):
                try:
                    changes["log_entries_removed"] += _remove_logs(log, rx)
                except (OSError, UnicodeDecodeError) as exc:
                    errors.append(f"could not rewrite {log.name}: {exc}")
        if changes["source_deleted"] or changes["files_edited"] or changes["log_entries_removed"]:
            for logroot in [root] + ([hub] if hub is not None else []):
                try:
                    append_log(logroot, "retract", GENERIC_LOG)
                except OSError as exc:
                    errors.append(f"could not append the operation log: {exc}")
        final = scan_root(root, rel, source, rx, LintContext(root))
        hub_left = 0
        if hub_log is not None and hub_log.is_file():
            text = read_text(hub_log)
            hub_left = count_log_entries(text, rx) if text is not None else 0
            if hub_left:
                final.log_entries[hub_log] = hub_left
        errors += final.errors

    remaining = [hit for hit in final.hits if hit.kind != "log-entry"] if args.apply else []
    log_left = sum(final.log_entries.values())
    if errors:
        status = "incomplete"
    elif not args.apply:
        status = "dry-run"
    elif remaining or source.exists() or (args.remove_from_logs and log_left):
        status = "incomplete"
    else:
        status = "verified"

    counts: dict[str, int] = {}
    for hit in scan.hits:
        counts[hit.kind] = counts.get(hit.kind, 0) + 1
    report = {
        "schema": SCHEMA,
        "ok": status in ("dry-run", "verified"),
        "status": status,
        "mode": "apply" if args.apply else "dry-run",
        "root": str(root),
        "source": {"path": rel, "exists": existed},
        "files_scanned": scan.files_scanned,
        "binary_files_skipped": scan.binary_skipped,
        "references": [hit.to_json() for hit in scan.hits],
        "reference_counts": dict(sorted(counts.items())),
        "log_entries_matched": sum(scan.log_entries.values()),
        "log_entries_kept": 0 if args.remove_from_logs else log_left,
        "remove_from_logs": bool(args.remove_from_logs),
        "needs_agent": [hit.to_json() for hit in (remaining if args.apply else scan.hits) if hit.action == "agent"],
        "applied": changes if args.apply else None,
        "remaining": [hit.to_json() for hit in remaining] if args.apply else None,
        "boundaries": ["hidden version-control metadata (.git) was not scanned or changed"],
        "errors": errors,
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)
    return 2 if status == "incomplete" else 0


def print_report(report: dict[str, Any]) -> None:
    print(f"Retraction status: {report['status']}")
    print(f"Mode: {report['mode']}")
    state = "present" if report["source"]["exists"] else "absent (verification of an earlier retraction)"
    print(f"Source: {report['source']['path']} ({state})")
    print(f"Files scanned: {report['files_scanned']}")
    counts = ", ".join(f"{count} {kind}" for kind, count in report["reference_counts"].items()) or "none"
    print(f"References: {counts}; {report['log_entries_matched']} log entries")
    for item in report["references"][:100]:
        print(f"- {item['path']}:{item['line']} {item['kind']} -> {item['action']}")
    if len(report["references"]) > 100:
        print(f"- ... {len(report['references']) - 100} more")
    applied = report["applied"]
    if applied is not None:
        print(
            "Applied: "
            f"source deleted {applied['source_deleted']}, "
            f"{applied['frontmatter_entries_removed']} sources entries removed from {len(applied['files_edited'])} files, "
            f"{len(applied['index_changes'])} index changes, {applied['log_entries_removed']} log entries removed"
        )
        print(f"Remaining references: {len(report['remaining'])}")
        for item in report["remaining"]:
            print(f"- {item['path']}:{item['line']} {item['kind']} (needs the agent)")
    if report["log_entries_kept"]:
        print(f"{report['log_entries_kept']} log entries name the source and were kept; add --remove-from-logs to remove them.")
    for line in report["boundaries"]:
        print(f"Boundary: {line}.")
    for error in report["errors"]:
        print(f"Error: {error}", file=sys.stderr)
    if report["mode"] == "dry-run":
        print("Dry run only. Re-run with --apply to write changes.")
