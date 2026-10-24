"""``source note``: write the processed ``raw/<type>/<slug>.md`` source note.

The ingestion protocol (``references/ingestion.md``, § Slug Generation and
``wiki-format.md``, § Source File Format) has the agent write the processed
extraction artifact of an ingest by hand. This module is that step as a
deterministic verb, registered under the ``source`` namespace beside
``receive``/``extract``/``decide``/``verify`` (``source_lifecycle``), because
``source note`` is what runs after ``extract``: the processed Markdown that
``source-lifecycle.md`` says the agent still writes.

It writes the note with exactly the frontmatter lint requires (``title``,
``source``, ``type``, ``ingested``, ``tags``, ``summary``; C2/C3 in
``lint_docs.check_frontmatter_schema``), derives the filename as the spec's
slug rules say, then does the two follow-ups ``ingestion.md`` orders right
after the write: ``index rebuild`` (the raw indexes, through ``index_cli``)
and the ``ingest`` log entry (through ``log_cli``). The master ``_index.md``
statistics and moving the inbox item to ``inbox/.processed/`` stay with the
agent (spec steps 4 and 5).

Slug collisions follow the spec: a slug derived from the title takes ``-2``,
``-3`` ... when the file exists; an explicit ``--slug`` that exists is refused
(exit 1). Exit codes follow ``source``: 0 written, 1 refused because the note
exists, 2 usage error or a privacy refusal (nothing written).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Callable

from . import frontmatter as _frontmatter
from . import index_cli as _index_cli
from . import log_cli as _log_cli
from .checkpoint_scan import scan_checkpoint_text
from .fsio import atomic_write_text
from .lint_docs import RAW_TYPES
from .markdown import frontmatter_bounds, today_date, yaml_quote
from .roots import is_hub

SCHEMA = "knowmade/source-note/v1"
MAX_STEM = 60
_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-")
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
# The optional frontmatter ingestion.md (PDF flow) and wiki-format.md
# (collection provenance) name. ``page_count`` is the one integer.
EXTRA_KEYS = (
    "content_format", "sha256", "page_count", "extraction_tool", "extraction_status",
    "fetched", "canonical_url", "license", "revision",
)
H1_RE = re.compile(r"^#\s+\S")


class NoteError(Exception):
    """A refusal; ``code`` is the exit code (1: exists, 2: usage or privacy)."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


def slugify_title(title: str) -> str:
    """The spec's slug words: lowercase, hyphens for spaces, special characters removed."""
    text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"[^a-z0-9-]", "", text)
    return re.sub(r"-{2,}", "-", text).strip("-")


def _fit(stem: str, suffix: str = "") -> str:
    """``stem`` + ``suffix`` within the 60-character limit, cutting the stem, not the suffix."""
    room = MAX_STEM - len(suffix)
    return stem[:room].rstrip("-") + suffix


def choose_stem(directory: Path, date: str, title: str, slug: str | None) -> str:
    """The filename stem (no ``.md``) for the note."""
    taken = {path.name.lower() for path in directory.glob("*.md")} if directory.is_dir() else set()
    if slug is not None:
        words = slug[11:] if _DATE_PREFIX_RE.match(slug) else slug
        if not _SLUG_RE.match(words):
            raise NoteError(f"--slug must be lowercase letters, digits and hyphens, not {slug!r}")
        stem = slug if _DATE_PREFIX_RE.match(slug) else f"{date}-{slug}"
        if len(stem) > MAX_STEM:
            raise NoteError(f"the filename {stem}.md exceeds {MAX_STEM} characters; shorten --slug")
        if f"{stem}.md".lower() in taken:
            raise NoteError(f"raw note already exists: {directory.name}/{stem}.md", code=1)
        return stem
    words = slugify_title(title)
    if not words:
        raise NoteError("cannot derive a slug from the title; pass --slug")
    base = _fit(f"{date}-{words}")
    stem, counter = base, 1
    while f"{stem}.md".lower() in taken:
        counter += 1
        stem = _fit(base, f"-{counter}")
    return stem


def parse_tags(raw: str) -> list[str]:
    tags: list[str] = []
    for item in raw.split(","):
        tag = re.sub(r"\s+", "-", item.strip().lower())
        if not tag:
            continue
        if not _TAG_RE.match(tag):
            raise NoteError(f"--tags: {item.strip()!r} is not a lowercase hyphenated tag")
        if tag not in tags:
            tags.append(tag)
    if not tags:
        raise NoteError("--tags needs at least one tag (lint requires a non-empty list)")
    return tags


def parse_extras(items: list[str]) -> list[tuple[str, str]]:
    extras: list[tuple[str, str]] = []
    for item in items:
        key, sep, value = item.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or key not in EXTRA_KEYS or not value:
            raise NoteError(f"--extra must be KEY=VALUE with KEY one of: {', '.join(EXTRA_KEYS)}")
        if key == "page_count" and not value.isdigit():
            raise NoteError("--extra page_count must be an integer")
        if any(existing == key for existing, _ in extras):
            raise NoteError(f"--extra {key} given twice")
        extras.append((key, value))
    return extras


def read_body(args: argparse.Namespace) -> str:
    """The note body from ``--body-file`` (``-`` or absent: standard input), LF-normalised."""
    path = args.body_file
    if path in (None, "-"):
        if path is None and sys.stdin.isatty():
            raise NoteError("no body: pass --body-file <path> or pipe the body on standard input")
        data = sys.stdin.buffer.read()
    else:
        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            raise NoteError(f"--body-file: cannot read {path}: {exc}") from exc
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise NoteError("the body is not valid UTF-8") from exc
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if not text.strip():
        raise NoteError("the body is empty")
    if frontmatter_bounds(text + "\n") is not None:
        raise NoteError("the body starts with a frontmatter block; pass only the content, the verb writes the frontmatter")
    return text


def render(
    *, title: str, source: str, type_: str, ingested: str, tags: list[str], summary: str,
    project: str | None, extras: list[tuple[str, str]], body: str,
) -> str:
    lines = [
        "---",
        f"title: {yaml_quote(title)}",
        f"source: {yaml_quote(source)}",
        f"type: {type_}",
        f"ingested: {ingested}",
        f"tags: [{', '.join(tags)}]",
        f"summary: {yaml_quote(summary)}",
    ]
    if project:
        lines.append(f"project: {project}")
    for key, value in extras:
        lines.append(f"{key}: {value}" if key == "page_count" else f"{key}: {yaml_quote(value)}")
    lines += ["---", "", ""]
    heading = "" if H1_RE.match(body.lstrip().split("\n", 1)[0]) else f"# {title}\n\n"
    return "\n".join(lines) + heading + body + "\n"


def privacy_refusal(texts: dict[str, str]) -> str | None:
    """Why a public wiki may not take this note, judged by ``log append``'s check; ``None`` if it may."""
    categories = sorted(
        {
            finding.category
            for label, text in texts.items()
            for finding in scan_checkpoint_text(text, label)
            if finding.severity == "critical"
        }
    )
    if categories:
        return (
            "this wiki is public and the note carries private or machine-local material "
            f"({', '.join(categories)}); ingest it into the -PRIVATE sibling instead"
        )
    return None


def run_note(args: argparse.Namespace, resolve_wiki_root: Callable[[Any], Path] | None) -> int:
    try:
        result = write_note(args, resolve_wiki_root)
    except NoteError as exc:
        print(f"source note: {'refused' if exc.code == 1 else 'error'}: {exc}", file=sys.stderr)
        return exc.code
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


def write_note(args: argparse.Namespace, resolve_wiki_root: Callable[[Any], Path] | None) -> dict[str, Any]:
    if resolve_wiki_root is None:
        raise NoteError("no wiki resolver is available")
    root = Path(resolve_wiki_root(args)).resolve()
    if not root.is_dir():
        raise NoteError(f"no wiki root at {root}")
    if is_hub(root):
        raise NoteError(f"{root} is a hub; pass --wiki <slug>, --local or --path <topic>")
    if not (root / "_index.md").is_file():
        raise NoteError(f"{root} is not an initialised wiki (no _index.md)")

    title = re.sub(r"\s+", " ", args.title).strip()
    source = re.sub(r"\s+", " ", args.source).strip()
    summary = re.sub(r"\s+", " ", args.summary).strip()
    for label, value in (("--title", title), ("--source", source), ("--summary", summary)):
        if not value:
            raise NoteError(f"{label} is empty")
    if args.ingested:
        try:
            ingested = dt.date.fromisoformat(args.ingested).isoformat()
        except ValueError:
            ingested = ""
        if ingested != args.ingested:
            raise NoteError(f"--ingested must be YYYY-MM-DD, not {args.ingested!r}")
    else:
        ingested = today_date().isoformat()
    tags = parse_tags(args.tags)
    extras = parse_extras(args.extra)
    if args.project:
        if not _SLUG_RE.match(args.project):
            raise NoteError(f"--project must be a lowercase hyphenated slug, not {args.project!r}")
        if not (root / "output" / "projects" / args.project / "WHY.md").is_file():
            raise NoteError(
                f'Project "{args.project}" does not exist. Create it first with /knowmade:project new {args.project} "goal".'
            )
    body = read_body(args)

    directory = root / "raw" / args.type
    stem = choose_stem(directory, ingested, title, args.slug)
    rel = f"raw/{args.type}/{stem}.md"
    public = _log_cli.is_public(root)
    log_message = f"{title} ({rel})"
    refusal = _log_cli.guard_message(log_message, public=public)
    if refusal is None and public:
        # Every field render() writes into the note is judged, extras and project included.
        texts = {rel: rel, "title": title, "source": source, "summary": summary, "tags": ", ".join(tags), "body": body}
        texts.update({f"extra:{key}": value for key, value in extras})
        if args.project:
            texts["project"] = args.project
        refusal = privacy_refusal(texts)
    if refusal:
        raise NoteError(refusal)

    text = render(
        title=title, source=source, type_=args.type, ingested=ingested, tags=tags, summary=summary,
        project=args.project, extras=extras, body=body,
    )
    path = root / rel
    directory.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise NoteError(f"raw note already exists: {rel}", code=1)
    atomic_write_text(path, text)
    try:
        _frontmatter.load(path)
    except _frontmatter.FrontmatterError as exc:  # pragma: no cover - render() always writes a block
        path.unlink()
        raise NoteError(f"internal error: the rendered note has no frontmatter ({exc})") from exc

    result: dict[str, Any] = {
        "schema": SCHEMA,
        "ok": True,
        "root": str(root),
        "path": rel,
        "type": args.type,
        "slug": stem,
        "ingested": ingested,
        "public": public,
        "index": {"status": "skipped", "changes": [], "remaining": []},
        "log": {"status": "skipped", "entry": None},
    }
    if not args.no_index:
        changes, remaining = _index_cli.rebuild_indexes(root)
        result["index"] = {"status": "incomplete" if remaining else "rebuilt", "changes": changes, "remaining": remaining}
        if remaining:
            result["ok"] = False
    if not args.no_log:
        line = _log_cli.entry_line(ingested, "ingest", log_message)
        _log_cli.append_entry(root / "log.md", line)
        result["log"] = {"status": "appended", "entry": line}
    return result


def register_note(sub: Any, add_resolution: Callable[[argparse.ArgumentParser], None]) -> None:
    """Add ``note`` to the ``source`` namespace's subparsers."""
    p = sub.add_parser(
        "note",
        help="Write the processed raw/<type>/<slug>.md source note, then rebuild the raw indexes and log the ingest.",
        description=(
            "Writes raw/<type>/<slug>.md (no LLM): lint's frontmatter, the ingestion slug (-2, -3 on a "
            "derived collision), the body from --body-file or stdin, then `index rebuild` and the `ingest` "
            "log entry. Master statistics and the inbox move stay with the agent. Exit 0 written; 1 the "
            "note exists (explicit --slug); 2 usage error or a public wiki refusing private material "
            "(nothing written), or the note written but the index rebuild incomplete (ok false)."
        ),
    )
    p.add_argument("--type", required=True, choices=sorted(RAW_TYPES), help="Source type; also the raw/ subdirectory.")
    p.add_argument("--title", required=True, help="Source title (slug words, log entry).")
    p.add_argument("--source", required=True, help="URL, file path, MANUAL or a provenance label.")
    p.add_argument("--summary", required=True, help="Two or three sentence summary.")
    p.add_argument("--tags", required=True, metavar="TAG[,TAG...]", help="Comma-separated lowercase tags; at least one.")
    p.add_argument("--slug", help="Slug words instead of deriving them from the title; an existing note is then refused.")
    p.add_argument("--ingested", metavar="YYYY-MM-DD", help="Ingest date (default: today, or LLM_WIKI_TODAY).")
    p.add_argument("--project", metavar="SLUG", help="Tag the note with this project; it must exist.")
    p.add_argument("--extra", action="append", default=[], metavar="KEY=VALUE", help=f"Extra frontmatter, one of {', '.join(EXTRA_KEYS)}; repeatable.")
    p.add_argument("--body-file", metavar="PATH", help="File holding the body, without frontmatter; default: standard input.")
    p.add_argument("--no-index", action="store_true", help="Skip the index rebuild.")
    p.add_argument("--no-log", action="store_true", help="Skip the log entry.")
    add_resolution(p)
    p.set_defaults(handler=run_note)
