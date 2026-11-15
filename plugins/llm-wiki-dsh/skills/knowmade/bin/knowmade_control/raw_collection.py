"""``source collection``: write a collection manifest and its child sources.

``/knowmade:ingest-collection`` (``references/ingestion-collections.md``,
``collection-adapters.md``) has the agent write one manifest to ``raw/repos/``
and one immutable child source per upstream item by hand. This module is that
write as one deterministic verb beside ``source note`` (``raw_note``), whose
frontmatter writer, slug rules, privacy judgement and duplicate index it reuses.

The agent still does the adapter's semantic work (clone, parse the dump,
query the API) and hands the result over as a JSONL file, one item per line:
``title``, ``source``, ``summary``, ``upstream_id``, ``revision`` and/or ``sha``,
the body as ``body`` or ``body_file``, optionally ``upstream_type``,
``canonical_url``, ``content_format``, ``license``, ``fetched``, ``type``
(``raw/`` subdirectory; default ``articles``, ``notes`` for ``csv-messages``),
``tags``, ``slug`` and ``extra`` (further provenance such as ``authors`` or
``row_number``). The verb then:

- validates every item first (nothing is written when any item is refused);
- skips an item already held: same ``collection`` + ``upstream_id`` + ``sha``
  (``revision`` when either side has no ``sha``); a changed item is written as a
  new source, an old one is never overwritten;
- writes the manifest (``tags: [collection, collection-manifest, <adapter>]``)
  and the children with the frontmatter the protocol lists, then runs
  ``index rebuild`` once and appends one ``ingest-collection`` log line.

``--manifest-only`` (Git) writes no children: the items are the pinned
``upstream_id`` + ``sha`` rows of the manifest table, which the verb renders.

Exit codes follow ``source note``: 0 done (also when everything was a
duplicate), 1 reserved for a single refused note, 2 usage error, a refused
item or a public wiki refusing private material (nothing written), or the
files written but the index rebuild incomplete (``ok`` false).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

from . import index_cli as _index_cli
from . import log_cli as _log_cli
from .fsio import atomic_write_text
from .lint_docs import RAW_TYPES
from .markdown import today_date
from .raw_note import (
    MANIFEST_ID, NoteError, SeenItems, choose_stem, clean_body, extra_text, parse_tags, privacy_refusal,
    render, target_root,
)

SCHEMA = "knowmade/source-collection/v1"
UPSTREAM_TYPES = {
    "git": "git-file",
    "mediawiki-dump": "mediawiki-page",
    "mediawiki-api": "mediawiki-page",
    "csv-messages": "message-row",
    "wayback-cdx": "wayback-snapshot",
}
ADAPTERS = tuple(UPSTREAM_TYPES)
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_EXTRA_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
# Keys the verb writes itself; an item's ``extra`` may not shadow them.
_RESERVED = frozenset({
    "title", "source", "type", "ingested", "tags", "summary", "project", "collection", "adapter",
    "upstream_id", "upstream_type", "revision", "sha", "canonical_url", "content_format", "license",
    "fetched", "manifest_only",
})
_ITEM_KEYS = frozenset({
    "title", "source", "summary", "upstream_id", "upstream_type", "revision", "sha", "body", "body_file",
    "canonical_url", "content_format", "license", "fetched", "type", "tags", "slug", "extra",
})
SAMPLE = 10


def _text(item: dict[str, Any], key: str, where: str, *, required: bool = False) -> str:
    value = item.get(key)
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        if value is None and not required:
            return ""
        raise NoteError(f"{where}: {key} must be a non-empty string")
    text = re.sub(r"\s+", " ", str(value)).strip()
    if required and not text:
        raise NoteError(f"{where}: {key} is required")
    return text


def _iso_date(value: str, where: str) -> str:
    try:
        parsed = dt.date.fromisoformat(value).isoformat()
    except ValueError:
        parsed = ""
    if parsed != value:
        raise NoteError(f"{where} must be YYYY-MM-DD, not {value!r}")
    return parsed


def _extra_value(value: Any, where: str) -> Any:
    if isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, list) and all(isinstance(item, (str, int)) and not isinstance(item, bool) for item in value):
        return [str(item) for item in value]
    raise NoteError(f"{where}: extra values must be text, integers, booleans or lists of text")


def read_entries(path: str) -> tuple[list[Any], Path]:
    """The JSON values of a JSONL file (``-``: standard input; a JSON array is accepted too) and the directory body files resolve against."""
    if path == "-":
        if sys.stdin.isatty():
            raise NoteError("no items: pass --items <file.jsonl> or pipe the items on standard input")
        data, base = sys.stdin.buffer.read(), Path.cwd()
    else:
        try:
            data, base = Path(path).read_bytes(), Path(path).resolve().parent
        except OSError as exc:
            raise NoteError(f"--items: cannot read {path}: {exc}") from exc
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise NoteError("--items is not valid UTF-8") from exc
    if text.lstrip().startswith("["):
        try:
            return list(json.loads(text)), base
        except json.JSONDecodeError as exc:
            raise NoteError(f"--items: invalid JSON: {exc}") from exc
    entries = []
    for number, line in enumerate(text.splitlines(), 1):
        if line.strip():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise NoteError(f"--items line {number}: invalid JSON: {exc}") from exc
    return entries, base


def read_items(path: str, *, default_type: str, adapter: str) -> list[dict[str, Any]]:
    """The validated items of ``--items``."""
    entries, base = read_entries(path)
    return [_item(entry, f"item {index}", base, default_type, adapter) for index, entry in enumerate(entries, 1)]


def _item(entry: Any, where: str, base: Path, default_type: str, adapter: str) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise NoteError(f"{where}: an item must be a JSON object")
    unknown = sorted(set(entry) - _ITEM_KEYS)
    if unknown:
        raise NoteError(f"{where}: unknown key(s) {', '.join(unknown)} (provenance beyond the listed keys goes in \"extra\")")
    item: dict[str, Any] = {"where": where}
    item["upstream_id"] = _text(entry, "upstream_id", where, required=True)
    item["revision"] = _text(entry, "revision", where)
    item["sha"] = _text(entry, "sha", where)
    if not (item["revision"] or item["sha"]):
        raise NoteError(f"{where}: an item needs revision or sha for the deduplication key")
    item["title"] = _text(entry, "title", where, required=True)
    item["source"] = _text(entry, "source", where, required=True)
    item["summary"] = _text(entry, "summary", where, required=True)
    upstream_type = _text(entry, "upstream_type", where) or UPSTREAM_TYPES[adapter]
    if upstream_type != UPSTREAM_TYPES[adapter]:
        raise NoteError(f"{where}: upstream_type for adapter {adapter} is {UPSTREAM_TYPES[adapter]}, not {upstream_type!r}")
    item["upstream_type"] = upstream_type
    for key in ("canonical_url", "content_format", "license"):
        item[key] = _text(entry, key, where)
    item["fetched"] = _iso_date(_text(entry, "fetched", where), f"{where}: fetched") if entry.get("fetched") else ""
    type_ = _text(entry, "type", where) or default_type
    if type_ not in RAW_TYPES:
        raise NoteError(f"{where}: type must be one of {', '.join(sorted(RAW_TYPES))}, not {type_!r}")
    item["type"] = type_
    tags = entry.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise NoteError(f"{where}: tags must be a list of text")
    item["tags"] = tags
    slug = _text(entry, "slug", where)
    if slug and not _SLUG_RE.match(slug):
        raise NoteError(f"{where}: slug must be lowercase letters, digits and hyphens, not {slug!r}")
    item["slug"] = slug or None
    extra = entry.get("extra", {})
    if not isinstance(extra, dict):
        raise NoteError(f"{where}: extra must be an object")
    for key in extra:
        if not _EXTRA_KEY_RE.match(key) or key in _RESERVED:
            raise NoteError(f"{where}: extra key {key!r} is reserved or not a lowercase snake_case name")
    item["extra"] = {key: _extra_value(value, f"{where}: extra.{key}") for key, value in extra.items()}
    if ("body" in entry) == ("body_file" in entry):
        raise NoteError(f"{where}: give exactly one of body and body_file")
    if "body" in entry:
        if not isinstance(entry["body"], str):
            raise NoteError(f"{where}: body must be text")
        text = entry["body"]
    else:
        body_path = Path(str(entry["body_file"]))
        try:
            text = (body_path if body_path.is_absolute() else base / body_path).read_bytes().decode("utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            raise NoteError(f"{where}: cannot read body_file {entry['body_file']}: {exc}") from exc
    try:
        item["body"] = clean_body(text)
    except NoteError as exc:
        raise NoteError(f"{where}: {exc}") from exc
    return item


def read_pins(path: str) -> list[dict[str, Any]]:
    """The ``upstream_id`` + ``sha`` rows of a ``--manifest-only`` manifest (same file formats as items)."""
    rows = []
    for row in read_entries(path)[0]:
        if not isinstance(row, dict) or set(row) - {"upstream_id", "sha"}:
            raise NoteError("--manifest-only rows are objects with upstream_id and sha only")
        upstream_id, sha = str(row.get("upstream_id") or "").strip(), str(row.get("sha") or "").strip()
        if not upstream_id or not sha or "|" in upstream_id or "`" in upstream_id or "\n" in upstream_id:
            raise NoteError(f"--manifest-only row needs a plain upstream_id and a sha, got {row!r}")
        rows.append({"upstream_id": upstream_id, "sha": sha})
    if len({row["upstream_id"] for row in rows}) != len(rows):
        raise NoteError("--manifest-only rows repeat an upstream_id")
    return rows


def run_collection(args: argparse.Namespace, resolve_wiki_root: Callable[[Any], Path] | None) -> int:
    try:
        result = write_collection(args, resolve_wiki_root)
    except NoteError as exc:
        print(f"source collection: error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


def write_collection(args: argparse.Namespace, resolve_wiki_root: Callable[[Any], Path] | None) -> dict[str, Any]:
    root = target_root(args, resolve_wiki_root)
    collection = args.collection
    if not _SLUG_RE.match(collection):
        raise NoteError(f"--collection must be a lowercase hyphenated slug, not {collection!r}")
    adapter = args.adapter
    if args.manifest_only and adapter != "git":
        raise NoteError("--manifest-only is for the git adapter")
    ingested = _iso_date(args.ingested, "--ingested") if args.ingested else today_date().isoformat()
    revision = re.sub(r"\s+", " ", args.revision).strip()
    source = re.sub(r"\s+", " ", args.source).strip()
    if not revision or not source:
        raise NoteError("--revision and --source must not be empty")
    name = re.sub(r"\s+", " ", args.name or collection).strip()
    license_ = re.sub(r"\s+", " ", args.license).strip() or "unknown"
    default_type = "notes" if adapter == "csv-messages" else "articles"

    pins: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    if args.manifest_only:
        if args.manifest_body_file:
            raise NoteError("--manifest-only renders the manifest table itself; drop --manifest-body-file")
        pins = read_pins(args.items)
        if not pins:
            raise NoteError("--manifest-only needs at least one upstream_id + sha row")
    else:
        items = read_items(args.items, default_type=default_type, adapter=adapter)
    count = len(pins) or len(items)
    candidates = args.candidates if args.candidates is not None else count
    if candidates < count:
        raise NoteError(f"--candidates {candidates} is smaller than the {count} item(s) given")

    public = _log_cli.is_public(root)
    seen = SeenItems(root)
    taken: dict[str, set[str]] = {}

    def stem_for(type_: str, title: str, slug: str | None) -> str:
        directory = root / "raw" / type_
        if type_ not in taken:
            taken[type_] = {path.name.lower() for path in directory.glob("*.md")} if directory.is_dir() else set()
        stem = choose_stem(directory, ingested, title, slug, taken[type_])
        taken[type_].add(f"{stem}.md".lower())
        return stem

    planned: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    errors: list[str] = []

    def plan(**note: Any) -> None:
        refusal = None
        if public:
            texts = {note["rel"]: note["rel"], "title": note["title"], "source": note["source"], "summary": note["summary"], "tags": ", ".join(note["tags"]), "body": note["body"]}
            texts.update({f"extra:{key}": extra_text(value) for key, value in note["extras"]})
            refusal = privacy_refusal(texts)
        refusal = refusal or _log_cli.guard_message(note["title"], public=public)
        if refusal:
            errors.append(f"{note['where']}: {refusal}")
        else:
            planned.append(note)

    # The manifest.
    manifest_title = f"Collection: {name}"
    held = seen.match(collection, MANIFEST_ID, revision, "")
    manifest: dict[str, Any] = {"status": "skipped" if held else "planned", "path": held, "revision": revision}
    if not held:
        if pins:
            rows = "\n".join(f"| `{row['upstream_id']}` | `{row['sha']}` |" for row in pins)
            body = f"| Path | Blob SHA |\n|---|---|\n{rows}"
            summary = f"Manifest for a manifest-only ingest of {name}: {len(pins)} pinned files at {revision}."
        else:
            if args.manifest_body_file:
                try:
                    text = Path(args.manifest_body_file).read_bytes().decode("utf-8-sig")
                except (OSError, UnicodeDecodeError) as exc:
                    raise NoteError(f"--manifest-body-file: cannot read {args.manifest_body_file}: {exc}") from exc
                body = clean_body(text)
            else:
                body = f"Collection manifest for `{collection}` via the `{adapter}` adapter.\n\nRevision: `{revision}`.\n\nChild sources: {len(items)}."
            summary = f"Manifest for a collection ingest of {name}: {len(items)} child sources captured from {revision}."
        if args.manifest_summary:
            summary = re.sub(r"\s+", " ", args.manifest_summary).strip()
        extras: list[tuple[str, Any]] = [("collection", collection), ("adapter", adapter), ("revision", revision)]
        if args.canonical_url:
            extras.append(("canonical_url", args.canonical_url.strip()))
        extras.append(("license", license_))
        if args.manifest_only:
            extras.append(("manifest_only", True))
        rel_stem = stem_for("repos", manifest_title, None)
        rel = f"raw/repos/{rel_stem}.md"
        tags = parse_tags(f"collection,collection-manifest,{adapter}")
        plan(
            where="manifest", rel=rel, type="repos", title=manifest_title, source=source, summary=summary,
            tags=tags, extras=extras, body=body, key=None,
        )
        manifest["path"] = rel
        seen.add(collection, MANIFEST_ID, revision, "", rel)

    # The children.
    for item in items:
        held = seen.match(collection, item["upstream_id"], item["revision"], item["sha"])
        if held:
            skipped.append({"upstream_id": item["upstream_id"], "duplicate_of": held})
            continue
        extras = [("collection", collection), ("adapter", adapter), ("upstream_id", item["upstream_id"]), ("upstream_type", item["upstream_type"])]
        extras += [(key, item[key]) for key in ("revision", "sha", "canonical_url", "content_format") if item[key]]
        extras.append(("license", item["license"] or license_))
        extras += list(item["extra"].items())
        extras.append(("fetched", item["fetched"] or ingested))
        try:
            tags = parse_tags(",".join(["collection", collection, *item["tags"]]))
            stem = stem_for(item["type"], item["title"], item["slug"])
        except NoteError as exc:
            errors.append(f"{item['where']}: {exc}")
            continue
        rel = f"raw/{item['type']}/{stem}.md"
        plan(
            where=item["where"], rel=rel, type=item["type"], title=item["title"], source=item["source"],
            summary=item["summary"], tags=tags, extras=extras, body=item["body"], key=item,
        )
        seen.add(collection, item["upstream_id"], item["revision"], item["sha"], rel)

    if errors:
        shown = errors[:20]
        more = f" (and {len(errors) - 20} more)" if len(errors) > 20 else ""
        raise NoteError("nothing written; refused: " + "; ".join(shown) + more)

    new_children = [note for note in planned if note["key"] is not None]
    new_count = len(new_children) if not pins else (len(pins) if manifest["status"] == "planned" else 0)
    skipped_count = len(skipped) if not pins else (len(pins) if manifest["status"] == "skipped" else 0)
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "ok": True,
        "root": str(root),
        "collection": collection,
        "adapter": adapter,
        "ingested": ingested,
        "public": public,
        "dry_run": bool(args.dry_run),
        "manifest": manifest,
        "counts": {"candidates": candidates, "new": new_count, "skipped": skipped_count},
        "sample": [{"title": note["title"], "path": note["rel"]} for note in new_children[:SAMPLE]],
        "skipped": skipped[:20],
        "index": {"status": "skipped", "changes": [], "remaining": []},
        "log": {"status": "skipped", "entry": None},
    }
    # Judge the log line before the first write, so a refusal still writes nothing.
    message = f"{collection} via {adapter}: {new_count} new, {skipped_count} skipped, {candidates} total candidates"
    if not args.no_log:
        refusal = _log_cli.guard_message(message, public=public)
        if refusal:
            raise NoteError(f"nothing written; the log line is refused: {refusal}")
    if args.dry_run:
        return result

    for note in planned:
        text = render(
            title=note["title"], source=note["source"], type_=note["type"], ingested=ingested, tags=note["tags"],
            summary=note["summary"], project=None, extras=note["extras"], body=note["body"],
        )
        path = root / note["rel"]
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise NoteError(f"raw note already exists: {note['rel']}")
        atomic_write_text(path, text)
    manifest["status"] = "written" if manifest["status"] == "planned" else "skipped"
    if not args.no_index:
        changes, remaining = _index_cli.rebuild_indexes(root)
        result["index"] = {"status": "incomplete" if remaining else "rebuilt", "changes": changes, "remaining": remaining}
        if remaining:
            result["ok"] = False
    if not args.no_log and result["ok"]:
        line = _log_cli.entry_line(ingested, "ingest-collection", message)
        _log_cli.append_entry(root / "log.md", line)
        result["log"] = {"status": "appended", "entry": line}
    return result


def register_collection(sub: Any, add_resolution: Callable[[argparse.ArgumentParser], None]) -> None:
    """Add ``collection`` to the ``source`` namespace's subparsers."""
    p = sub.add_parser(
        "collection",
        help="Write a collection manifest and its child sources from a JSONL file, skip items already held, rebuild the indexes and log the ingest.",
        description=(
            "Writes raw/repos/<manifest>.md and one raw/<type>/<slug>.md per item (no LLM) with the frontmatter "
            "ingestion-collections.md lists, skipping an item whose collection + upstream_id + sha (revision when "
            "either side has no sha) is already held, then one `index rebuild` and one `ingest-collection` log "
            "entry. Items: one JSON object per line (title, source, summary, upstream_id, revision and/or sha, "
            "body or body_file, optional upstream_type, canonical_url, content_format, license, fetched, type, "
            "tags, slug, extra); with --manifest-only the rows are {upstream_id, sha} pins rendered as the "
            "manifest table and no children are written. Everything is validated before the first write: exit 0 "
            "done (including all-duplicate); 2 usage error or a refused item, nothing written, or written but "
            "the index rebuild incomplete (ok false). Master statistics and the inbox move stay with the agent."
        ),
    )
    p.add_argument("--collection", required=True, metavar="SLUG", help="Collection slug, e.g. bitcoin-bips.")
    p.add_argument("--adapter", required=True, choices=ADAPTERS, help="Adapter that produced the items.")
    p.add_argument("--source", required=True, help="Upstream URL or path of the collection (the manifest's source).")
    p.add_argument("--revision", required=True, help="Collection revision: commit SHA, dump filename/date, API snapshot or CDX query timestamp, dataset hash.")
    p.add_argument("--items", required=True, metavar="PATH", help="JSONL file of items (or `-` for standard input); with --manifest-only, {upstream_id, sha} rows.")
    p.add_argument("--name", help="Display name of the collection for the manifest title (default: the slug).")
    p.add_argument("--canonical-url", help="Canonical upstream URL for the manifest.")
    p.add_argument("--license", default="unknown", help="Detected license (default: unknown); children inherit it.")
    p.add_argument("--manifest-summary", help="Manifest summary instead of the generated one.")
    p.add_argument("--manifest-body-file", metavar="PATH", help="Manifest body instead of the generated one.")
    p.add_argument("--manifest-only", action="store_true", help="Git manifest-only: pin the rows in the manifest, write no children.")
    p.add_argument("--candidates", type=int, metavar="N", help="Total candidate items before filters and limit, for the log line (default: the items given).")
    p.add_argument("--ingested", metavar="YYYY-MM-DD", help="Ingest date (default: today, or LLM_WIKI_TODAY).")
    p.add_argument("--dry-run", action="store_true", help="Validate, deduplicate and report; write nothing.")
    p.add_argument("--no-index", action="store_true", help="Skip the index rebuild.")
    p.add_argument("--no-log", action="store_true", help="Skip the log entry.")
    add_resolution(p)
    p.set_defaults(handler=run_collection)
