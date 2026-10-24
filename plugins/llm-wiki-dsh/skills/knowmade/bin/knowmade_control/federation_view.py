"""Read-only federation views: ``registry inventory`` and ``registry peek`` (KM-HUB-010, KM-HUB-011).

Both are *derived*: nothing is cached, nothing is written, and every fact is
read from the root that owns it on each call (``DESIGN.md`` section 4, "Federation
views"; ``references/federation.md``, "Inventory and peek").

``inventory`` is the one deliberate walk over every registered root. Per root it
reports the registry facts (id, kind, alias, status, path, last seen). For an
available hub it adds the topics of its registry (slug, id, private or not, and
the counts of ``raw/`` sources and ``wiki/`` articles); for an available wiki,
the same counts. An unavailable root keeps its last known path. A private topic
is listed by slug, id and counts only: never a title, a description, a path or a
log date. ``--public`` leaves private roots and private topics out and says how
many.

``peek`` is target-first: it resolves exactly one target and prints one index.
An id or alias goes through the registry with the fail-closed rules of
``registry locate`` (none available or several available is exit 1, unknown is
exit 2). A ``knowmade://`` reference goes through ``xref.Resolver`` with its
replica rules. Nothing else is opened: another root is touched only for its
identity marker and its hub registry, which the privacy judgement needs, and
never for its index or content. A directory prints its ``_index.md``; a hub with
none prints the topic list of its ``wikis.json``. ``--public`` is judged over
every replica of the target before anything is resolved, as ``ref resolve``
does, and a private target is exit 3 with nothing on stdout.

Privacy is reused, not re-implemented: ``xref.Resolver.is_private`` (a
``*-PRIVATE`` component of the real path, or a private topic of any available
registered hub). When it cannot be judged (an unreadable hub registry) the root
counts as private, so the views fail closed.

Exit codes follow ``registry locate`` and ``ref resolve``: 0 success, 1 a
refusal (nothing available, ambiguous, divergent, unresolved, no index), 2
unusable input (corrupt registry, unknown id or alias, invalid reference), 3 a
private target under ``--public``. One line on stderr, never a traceback; text
read from a file never reaches the terminal with a raw control character.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import federation as _federation
from . import identity as _identity
from . import roots as _roots
from . import xref as _xref
from .common import read_registry
from .lint_docs import content_markdown_files
from .lint_report import is_private_dir_name

MAX_INDEX_BYTES = 1 << 20  # a top index is a page, not a corpus
MAX_LOG_BYTES = 4 << 20
_LOG_DATE = re.compile(r"^##\s*\[(\d{4}-\d{2}-\d{2})\]", re.MULTILINE)
INDEX_NAME = "_index.md"


# --- plain text safety ----------------------------------------------------------


def _escape(char: str) -> str:
    code = ord(char)
    return f"\\x{code:02x}" if code < 256 else f"\\u{code:04x}"


def safe_line(text: object) -> str:
    """One line of terminal output: every control character written as an escape."""
    return "".join(_escape(c) if ord(c) < 32 or 127 <= ord(c) <= 159 else c for c in str(text))


def safe_block(text: str) -> str:
    """Multi-line text: newlines and tabs stay, line ends are normalised, other controls are escaped."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(
        _escape(c) if (ord(c) < 32 and c not in "\n\t") or 127 <= ord(c) <= 159 else c for c in text
    )


# --- privacy -------------------------------------------------------------------------


class _Judge:
    """``Resolver.is_private`` that fails closed: a root that cannot be judged is private."""

    def __init__(self, resolver: _xref.Resolver) -> None:
        self.resolver = resolver

    def __call__(self, path: Path) -> bool:
        try:
            return self.resolver.is_private(path)
        except (_xref.RefError, OSError):
            return True


# --- counts from the files that own them ----------------------------------------------


def wiki_counts(root: Path) -> dict[str, int] | None:
    """Sources under ``raw/`` and articles under ``wiki/``, counted as the master index statistics are."""
    try:
        return {
            "sources": len(content_markdown_files(root / "raw")),
            "articles": len(content_markdown_files(root / "wiki")),
        }
    except OSError:
        return None


def last_log_date(root: Path) -> str | None:
    """The newest ``## [YYYY-MM-DD]`` heading of ``log.md``, or ``None``."""
    try:
        with open(root / "log.md", "rb") as handle:
            data = handle.read(MAX_LOG_BYTES)
    except OSError:
        return None
    dates = _LOG_DATE.findall(data.decode("utf-8", errors="replace"))
    return max(dates) if dates else None


def _topic_id(entry: dict[str, Any], root: Path | None) -> str | None:
    """The root's own declared id first (it is authoritative), else the registry row's."""
    if root is not None:
        declared = _identity.read_wiki_id(root)
        if declared is not None:
            return declared
    row = entry.get("id")
    return row if isinstance(row, str) and _identity.is_wiki_id(row) else None


def hub_topics(
    hub: Path, judge: _Judge, *, public: bool, detail: bool
) -> tuple[list[dict[str, Any]] | None, int, str | None]:
    """``(topic rows, private topics omitted, error)`` of the hub registry at ``hub``.

    ``detail`` adds the counts and the last log date and reads each topic's own
    ``config.md`` for its id; without it only ``wikis.json`` is read (the
    ``peek`` case). Private topics keep slug, id, archived flag and counts only.
    """
    try:
        wikis = read_registry(hub).get("wikis") or {}
        overlay = _roots.read_local_registry(hub)
    except SystemExit as exc:
        return None, 0, safe_line(exc.code)
    rows: list[dict[str, Any]] = []
    omitted = 0
    for slug, entry in wikis.items():
        slug = str(slug)
        if slug == "hub":
            continue
        if not isinstance(entry, dict):
            if public:
                omitted += 1  # a row that cannot be judged is not public
            continue
        private = is_private_dir_name(slug)
        if private and public:
            omitted += 1
            continue
        root: Path | None = None
        try:
            root = Path(os.path.abspath(_roots.registry_entry_path(hub, slug, entry, overlay)))
        except (SystemExit, OSError):
            root = None
        if root is not None:
            if _roots.same_path(root, hub):
                continue  # the hub's own row is the hub, not a topic
            private = private or judge(root)
            if private and public:
                omitted += 1
                continue
        elif public:
            omitted += 1  # no resolvable path: it cannot be judged, so it is not public
            continue
        row: dict[str, Any] = {
            "slug": slug,
            "wiki_id": _topic_id(entry, root if detail else None),
            "private": private,
            "archived": _roots.is_archived_registry_entry(entry),
        }
        if detail:
            if root is None:
                row["status"] = "invalid-path"
            elif (root / "config.md").is_file():
                row["status"] = _federation.AVAILABLE
                row["counts"] = wiki_counts(root)
                row["last_log"] = None if private else last_log_date(root)
            else:
                row["status"] = "missing" if not root.is_dir() else "uninitialized"
        rows.append(row)
    return rows, omitted, None


# --- inventory ---------------------------------------------------------------------------


def build_inventory(public: bool) -> dict[str, Any]:
    """The inventory payload: one row per registry entry, in registry order."""
    resolver = _xref.Resolver()
    entries = resolver.registry().entries
    judge = _Judge(resolver)
    rows: list[dict[str, Any]] = []
    omitted_roots = omitted_topics = 0
    for entry in entries:
        status = _federation.status_of(entry)
        root = _federation.expanded(entry["path"])
        private = judge(root)
        if private and public:
            omitted_roots += 1
            continue
        row: dict[str, Any] = {
            "id": entry["id"],
            "kind": entry["kind"],
            "alias": entry["alias"],
            "status": status["status"],
            "path": entry["path"],
            "last_seen": entry["last_seen"],
        }
        for key in ("declared_id", "forked_to", "reason"):
            if key in status:
                row[key] = status[key]
        if not public:
            row["private"] = private
        if status["status"] == _federation.AVAILABLE:
            if entry["kind"] == "hub":
                topics, omitted, error = hub_topics(root, judge, public=public, detail=True)
                omitted_topics += omitted
                if error is not None:
                    row["topics_error"] = error
                else:
                    row["topics"] = topics
            else:
                row["counts"] = wiki_counts(root)
                row["last_log"] = last_log_date(root)
        rows.append(row)
    payload: dict[str, Any] = {"public": public, "roots": rows}
    if public:
        payload["omitted"] = {"roots": omitted_roots, "topics": omitted_topics}
    return payload


def _counts_text(counts: dict[str, int] | None) -> str:
    if counts is None:
        return "counts unavailable"
    return f"sources {counts['sources']}  articles {counts['articles']}"


def _print_inventory(payload: dict[str, Any]) -> None:
    rows = payload["roots"]
    if not rows and not payload.get("omitted", {}).get("roots"):
        print("no roots registered")
    for row in rows:
        detail = _federation.status_detail(row)
        head = [
            f"{row['status']:<11}",
            f"{row['kind']:<4}",
            row["id"],
            safe_line(row["alias"]) if row["alias"] else "-",
            safe_line(row["path"]),
        ]
        if detail:
            head.append(safe_line(detail))
        if row["status"] != _federation.AVAILABLE:
            head.append(f"last seen {row['last_seen'] or 'never'}")
        elif "counts" in row:
            head.append(_counts_text(row["counts"]))
            if row.get("last_log"):
                head.append(f"log {row['last_log']}")
        print("  ".join(head))
        if "topics_error" in row:
            print(f"    topics unreadable: {row['topics_error']}")
        for topic in row.get("topics", []):
            parts = [f"    {safe_line(topic['slug'])}", topic["wiki_id"] or "no id"]
            if topic["private"]:
                parts.append("private")
            if topic["archived"]:
                parts.append("archived")
            parts.append(topic["status"])
            if topic.get("counts") is not None:
                parts.append(_counts_text(topic["counts"]))
            if topic.get("last_log"):
                parts.append(f"log {topic['last_log']}")
            print("  ".join(parts))
    omitted = payload.get("omitted")
    if omitted is not None:
        print(
            f"omitted by --public: {omitted['roots']} private root(s), {omitted['topics']} private topic(s)"
        )


def cmd_inventory(args: argparse.Namespace) -> int:
    payload = build_inventory(args.public)
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        _print_inventory(payload)
    return 0


# --- peek ---------------------------------------------------------------------------------


def _within(root: Path, path: Path) -> bool:
    return _xref._within(os.path.realpath(root), os.path.realpath(path))


def _inside_public_topic(hub: Path, directory: Path, judge: _Judge) -> bool:
    """Whether ``directory`` lies inside a topic of ``hub`` that is judged public."""
    try:
        wikis = read_registry(hub).get("wikis") or {}
        overlay = _roots.read_local_registry(hub)
    except SystemExit:
        return False
    for slug, entry in wikis.items():
        if str(slug) == "hub" or not isinstance(entry, dict) or is_private_dir_name(str(slug)):
            continue
        try:
            root = Path(os.path.abspath(_roots.registry_entry_path(hub, str(slug), entry, overlay)))
        except (SystemExit, OSError):
            continue
        if not _roots.same_path(root, hub) and _within(root, directory) and not judge(root):
            return True
    return False


def _read_index(path: Path) -> tuple[str, bool]:
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_INDEX_BYTES + 1)
    except OSError as exc:
        raise _xref.RefError(f"cannot read {_federation.display(path)}: {exc.strerror or exc}", 2) from exc
    return data[:MAX_INDEX_BYTES].decode("utf-8", errors="replace"), len(data) > MAX_INDEX_BYTES


def _topics_text(rows: list[dict[str, Any]], *, reason: str, omitted: int = 0) -> str:
    lines = [f"# Topics (from wikis.json; {reason})", ""]
    for row in rows:
        marks = ["private" if row["private"] else "", "archived" if row["archived"] else ""]
        extra = ", ".join(mark for mark in marks if mark)
        lines.append(f"- {row['slug']}  {row['wiki_id'] or 'no id'}" + (f"  ({extra})" if extra else ""))
    if omitted:
        lines.append("")
        lines.append(f"({omitted} private topic(s) omitted by --public)")
    return "\n".join(lines) + "\n"


def _emit_peek(
    args: argparse.Namespace,
    judge: _Judge | None,
    *,
    target: str,
    root: Path,
    directory: Path,
    root_id: str,
    kind: str,
    status: str,
) -> int:
    """Print the index of ``directory`` inside ``root``; the target is already chosen and cleared."""
    index = directory / INDEX_NAME
    topics: list[dict[str, Any]] | None = None
    omitted_topics = 0
    is_hub = (root / "wikis.json").is_file()
    if is_hub:
        judge = judge or _Judge(_xref.Resolver())
        topics, omitted_topics, _ = hub_topics(root, judge, public=args.public, detail=False)

    # The hub's own _index.md is free text: a private topic can leak through a
    # title, a link target or a registry path that names neither "-PRIVATE" nor
    # the slug. Redacting lines fails open, so under --public a hub that has any
    # private topic (or whose topics cannot be judged) never prints that text;
    # the topic list is rebuilt from wikis.json with private topics left out.
    # Below the root the same holds for any directory that is not inside a
    # public topic (``topics/`` itself, say): its index may list private ones.
    at_root = _roots.same_path(directory, root)
    hub_has_private = args.public and is_hub and (topics is None or omitted_topics > 0)
    if hub_has_private and not at_root and not _inside_public_topic(root, directory, judge):
        print(f"private target: {target} is a hub directory outside any public topic; refused by --public", file=sys.stderr)
        return 3
    hub_text_unsafe = hub_has_private and at_root
    source = INDEX_NAME
    if hub_text_unsafe:
        if topics is None:
            print(f"private target: {target} has a registry that cannot be judged; refused by --public", file=sys.stderr)
            return 3
        content, truncated, source = (
            _topics_text(topics, reason="private topics omitted; the hub _index.md is not shown", omitted=omitted_topics),
            False,
            "wikis.json",
        )
    elif index.is_file():
        if args.public and judge is not None and judge(index):
            print(f"private target: {target} resolves into a private location; refused by --public", file=sys.stderr)
            return 3
        if not _within(root, index):
            raise _xref.RefError(f"refused: {INDEX_NAME} of {target} leads outside its root", 1)
        content, truncated = _read_index(index)
    elif is_hub and at_root and topics is not None:
        content, truncated, source = _topics_text(topics, reason="this hub has no _index.md"), False, "wikis.json"
    else:
        raise _xref.RefError(f"no {INDEX_NAME} in {safe_line(_federation.display(directory))}", 1)

    if args.json:
        payload: dict[str, Any] = {
            "target": target,
            "id": root_id,
            "kind": kind,
            "status": status,
            "root": _federation.display(root),
            "index": _federation.display(index) if source == INDEX_NAME else _federation.display(root / "wikis.json"),
            "source": source,
            "content": content,
            "truncated": truncated,
            "public": args.public,
        }
        if topics is not None:
            payload["topics"] = topics
        if args.public:
            payload["omitted"] = {"topics": omitted_topics}
        print(json.dumps(payload, indent=2))
        return 0
    sys.stdout.write(safe_block(content) if content.endswith("\n") else safe_block(content) + "\n")
    if truncated:
        print(f"note: {INDEX_NAME} truncated at {MAX_INDEX_BYTES} bytes", file=sys.stderr)
    if args.public and omitted_topics:
        print(f"note: --public omitted {omitted_topics} private topic(s)", file=sys.stderr)
    return 0


def _peek_registry(args: argparse.Namespace) -> int:
    entries = _federation.load().entries
    chosen = [entry for entry in entries if entry["id"] == args.target]
    if not chosen:
        chosen = [entry for entry in entries if entry["alias"] == args.target]
    # An id no registry entry carries may still be a topic of a registered hub
    # (the ids `inventory` lists): peek it as the reference to that root.
    if not chosen and _xref._ROOT_ID.match(args.target) and _xref.Resolver().candidates(args.target):
        args.target = f"{_xref.SCHEME}{args.target}"
        return _peek_uri(args)
    if not chosen:
        raise _federation.RegistryError(f"unknown id or alias: {args.target}", 2)
    judge = _Judge(_xref.Resolver()) if args.public else None
    if judge is not None and any(judge(_federation.expanded(entry["path"])) for entry in chosen):
        print(f"private target: {safe_line(args.target)} is a private location; refused by --public", file=sys.stderr)
        return 3
    replicas = [{**entry, **_federation.status_of(entry)} for entry in chosen]
    root_id = replicas[0]["id"]
    available = [entry for entry in replicas if entry["status"] == _federation.AVAILABLE]
    if not available:
        print(f"no available replica of {root_id}; last known:", file=sys.stderr)
        for entry in replicas:
            print(f"  {safe_line(_federation._describe(entry))}", file=sys.stderr)
        return 1
    if len(available) > 1:
        print(f"ambiguous: {len(available)} replicas of {root_id}", file=sys.stderr)
        for entry in available:
            print(f"  {safe_line(_federation._describe(entry))}", file=sys.stderr)
        print("peek by alias to pick one", file=sys.stderr)
        return 1
    root = _federation.expanded(available[0]["path"])
    return _emit_peek(
        args,
        judge,
        target=safe_line(args.target),
        root=root,
        directory=root,
        root_id=root_id,
        kind=available[0]["kind"],
        status=_federation.AVAILABLE,
    )


def _peek_uri(args: argparse.Namespace) -> int:
    ref = _xref.parse_uri(args.target)
    resolver = _xref.Resolver()
    judge = _Judge(resolver)
    # Judged before resolving and over every replica, as `ref resolve --public` does.
    if args.public and any(replica.private for replica in resolver.replicas(ref)):
        print(f"private target: {safe_line(ref.uri)} resolves into a private location; refused by --public", file=sys.stderr)
        return 3
    resolution = resolver.resolve(ref)
    if resolution.status == _xref.DIVERGENT:
        present = [r for r in resolution.replicas if r.available and r.target_state == _xref.PRESENT]
        print(f"divergent: {len(present)} replicas of {ref.id} differ at {safe_line(ref.path or 'the root')}:", file=sys.stderr)
        for replica in present:
            print(f"  {replica.digest}  {safe_line(_federation.display(replica.target))}", file=sys.stderr)
        return 1
    if resolution.status not in (_xref.RESOLVED, _xref.EQUIVALENT) or resolution.selected is None:
        _xref._refusal(resolution)
        return 1
    selected = resolution.selected
    if not selected.target.is_dir():
        raise _xref.RefError(
            f"{safe_line(ref.uri)} names a file, not a directory; peek prints an index (use ref resolve for a file)", 2
        )
    return _emit_peek(
        args,
        judge,
        target=safe_line(ref.uri),
        root=selected.root,
        directory=selected.target,
        root_id=ref.id,
        kind=ref.kind if not ref.path else "directory",
        status=resolution.status,
    )


def cmd_peek(args: argparse.Namespace) -> int:
    if args.target.startswith(_xref.SCHEME):
        return _peek_uri(args)
    return _peek_registry(args)


# --- parser and dispatch --------------------------------------------------------------------


def register_verbs(sub: Any) -> None:
    """Add ``inventory`` and ``peek`` to the ``registry`` subparsers."""
    inventory = sub.add_parser(
        "inventory",
        help="One read-only view of every registered root, with hub topics and counts.",
        description=(
            "Read-only and derived: nothing is cached and every fact is read from the root that "
            "owns it. Per registered root: id, kind, alias, status, path and last_seen (an "
            "unavailable root keeps its last known path). An available hub adds its topics "
            "(slug, id, private or not, archived, counts of raw/ sources and wiki/ articles, last "
            "log date); an available wiki adds the same counts. A private topic is listed by "
            "slug, id and counts only. --public leaves private roots and private topics out and "
            "prints how many."
        ),
    )
    inventory.add_argument("--json", action="store_true", help="Print JSON.")
    inventory.add_argument(
        "--public",
        action="store_true",
        help="Omit private roots and private topics entirely and report how many were omitted.",
    )

    peek = sub.add_parser(
        "peek",
        help="Print the top index of exactly one target: an id, an alias or a knowmade:// reference.",
        description=(
            "Target-first: it resolves one target and prints one index, and never scans the other "
            "roots. An id or alias goes through the registry with the rules of 'registry locate' "
            "(none available or several available is exit 1, unknown is exit 2); a knowmade:// "
            "reference goes through 'ref resolve' replica rules (unresolved, divergent or missing "
            "is exit 1, invalid is exit 2). A root or directory prints its _index.md; a hub "
            "without one prints the topic list of its wikis.json. --public on a private target "
            "exits 3 with nothing on stdout."
        ),
    )
    peek.add_argument("target", help="A root id (hb-... / wk-...), a registered alias, or a knowmade:// reference.")
    peek.add_argument("--json", action="store_true", help="Print JSON.")
    peek.add_argument(
        "--public",
        action="store_true",
        help="Refuse (exit 3, nothing printed) a private target and leave private topics out of a hub's index.",
    )


def _guarded(verb: Callable[[argparse.Namespace], int]) -> Callable[[argparse.Namespace], int]:
    def run(args: argparse.Namespace) -> int:
        try:
            return verb(args)
        except _xref.RefError as exc:
            print(safe_line(exc), file=sys.stderr)
            return exc.code

    return run


VERBS: dict[str, Callable[[argparse.Namespace], int]] = {
    "inventory": _guarded(cmd_inventory),
    "peek": _guarded(cmd_peek),
}
