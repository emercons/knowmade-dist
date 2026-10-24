"""The ``project`` namespace: deterministic project-folder maintenance (emercons/knowmade#83).

``project list``, ``project new`` and ``project add`` are the mechanical half of
``references/projects.md``: list the folders under ``output/projects/`` with
their title, goal, member count and the legacy-manifest and staleness flags,
scaffold a project (``WHY.md`` from the spec template, the ``output/_index.md``
entry, one ``log.md`` line) and move loose outputs into one. The checks are the
lint's own (``projects.valid_slug`` is C8's slug rule, staleness is
``lint_docs.check_project_staleness``, the index entry is the repair ``lint
--fix`` makes); a public wiki never gets a project that names a ``-PRIVATE``
path. All three live below the migration.

``project migrate-why`` performs the lossless legacy ``_project.md`` to
``WHY.md`` migration that ``references/linting.md`` describes as C8c. It is a
dry run unless ``--apply`` is given. For every active project folder
(``output/projects/<slug>/``) with a ``_project.md`` and no non-empty
``WHY.md``: the title and goal come from the frontmatter; derived sections
(between ``<!-- DERIVED -->`` markers, or headed ``## Members`` / ``## External
Members``) are dropped; every other section is kept verbatim and in order; when
the body has no ``## Goal`` section the frontmatter goal becomes the first
paragraph. ``WHY.md`` is ``# <title>`` plus the kept text. A manifest with no
goal, no ``## Goal`` section and no other prose is refused, never migrated to a
placeholder. ``--apply`` writes ``WHY.md``, deletes ``_project.md`` and appends
one ``log.md`` entry; a folder that already has ``WHY.md`` is skipped, so the
command is idempotent. Lint reports the folders it would touch as
``project-legacy-manifest`` (C8).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from . import frontmatter as _frontmatter
from . import log_cli as _log_cli
from .checkpoint_scan import scan_checkpoint_text
from .common import PRIVATE_SUFFIX, add_scope_arguments
from .fsio import atomic_write_text
from .indexing import extract_link_destinations, link_path_variants, repair_directory_index
from .lint_hub import is_private_placeholder
from .lint_report import LintContext, markdown_files
from .markdown import append_log, today_date, write_text_preserving
from .privacy_boundary import BoundaryChecker, BoundaryRegistryError, private_topic_dirs
from .roots import is_hub, resolve_wiki_root_args

LEGACY = "_project.md"
WHY = "WHY.md"
DERIVED_HEADINGS = {"members", "external members"}
DERIVED_START = re.compile(r"^\s*<!--\s*DERIVED\s*-->\s*$", re.I)
DERIVED_END = re.compile(r"^\s*<!--\s*/\s*DERIVED\s*-->\s*$", re.I)


def project_folders(wiki_root: Path) -> list[Path]:
    """Active project folders: ``output/projects/<slug>/``, never ``.archive``."""
    projects = wiki_root / "output" / "projects"
    if not projects.is_dir():
        return []
    return sorted(child for child in projects.iterdir() if child.is_dir() and not child.name.startswith("."))


def has_why(project: Path) -> bool:
    why = project / WHY
    return why.is_file() and bool(why.read_text(encoding="utf-8", errors="replace").strip())


def _sections(body: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """Split ``body`` into its preamble and ``(heading, lines)`` level-2 sections, derived blocks removed."""
    kept: list[str] = []
    inside = False
    for line in body.split("\n"):
        # The block is delimited by a repeated ``<!-- DERIVED -->`` or closed by ``<!-- /DERIVED -->``.
        if inside:
            inside = not (DERIVED_START.match(line) or DERIVED_END.match(line))
            continue
        if DERIVED_START.match(line):
            inside = True
            continue
        kept.append(line)
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    for line in kept:
        if line.startswith("## "):
            sections.append((line[3:].strip(), [line]))
        elif sections:
            sections[-1][1].append(line)
        elif not line.startswith("# "):
            preamble.append(line)
    return preamble, sections


def _text(value: Any) -> str:
    return str(value).strip() if value not in (None, "", []) else ""


def why_text(manifest: Path) -> tuple[str | None, int, str]:
    """``(WHY.md text or None, kept section count, reason when None)`` for one manifest."""
    loaded = _frontmatter.load(manifest)
    fields = loaded.fields
    title = _text(fields.get("title")) or manifest.parent.name
    goal = _text(fields.get("goal"))
    preamble, sections = _sections(loaded.body)
    sections = [(heading, lines) for heading, lines in sections if heading.lower() not in DERIVED_HEADINGS]
    has_goal_section = any(heading.lower() == "goal" for heading, _ in sections)
    prose = "\n".join(preamble).strip()
    parts = [f"# {title}"]
    if not has_goal_section and goal:
        parts.append(goal)
    if prose:
        parts.append(prose)
    for _heading, lines in sections:
        parts.append("\n".join(lines).strip())
    if len(parts) == 1:
        return None, 0, "no goal and no prose to preserve; write WHY.md by hand"
    return "\n\n".join(parts) + "\n", len(sections), ""


def migrate(wiki_root: Path, *, apply: bool) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for project in project_folders(wiki_root):
        manifest = project / LEGACY
        if not manifest.is_file():
            continue
        rel = manifest.relative_to(wiki_root).as_posix()
        if has_why(project):
            rows.append({"project": project.name, "manifest": rel, "action": "skip", "reason": "WHY.md exists"})
            continue
        try:
            text, kept, reason = why_text(manifest)
        except (OSError, UnicodeDecodeError, _frontmatter.FrontmatterError) as exc:
            text, kept, reason = None, 0, f"unreadable manifest: {exc}"
        if text is None:
            rows.append({"project": project.name, "manifest": rel, "action": "refuse", "reason": reason})
            continue
        row = {"project": project.name, "manifest": rel, "action": "migrate", "why": f"{rel.rsplit('/', 1)[0]}/{WHY}",
               "sections_preserved": kept, "text": text}
        if apply:
            write_text_preserving(project / WHY, text)
            manifest.unlink()
            row["applied"] = True
        rows.append(row)
    migrated = [row for row in rows if row["action"] == "migrate"]
    if apply and migrated:
        append_log(wiki_root, "project", f"migrated {len(migrated)} legacy _project.md manifest(s) to WHY.md")
    return {
        "schema": "knowmade/project-migrate-why/v1",
        "root": str(wiki_root),
        "apply": apply,
        "projects": rows,
    }


def run_migrate(args: argparse.Namespace) -> int:
    wiki_root = resolve_wiki_root_args(args)
    if not wiki_root.is_dir() or is_hub(wiki_root):
        print(f"project migrate-why: {wiki_root} is not a topic wiki root", file=sys.stderr)
        return 2
    payload = migrate(wiki_root.resolve(), apply=bool(args.apply))
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    verb = "Migrated" if args.apply else "Would migrate"
    for row in payload["projects"]:
        if row["action"] == "migrate":
            print(f"{verb} {row['manifest']} -> {row['why']} (preserved {row['sections_preserved']} sections)")
        else:
            print(f"{row['action']}: {row['manifest']} ({row['reason']})")
    if not payload["projects"]:
        print("No legacy _project.md manifests.")
    return 0


# --- list / new / add ------------------------------------------------------

LIST_SCHEMA = "knowmade/project-list/v1"
NEW_SCHEMA = "knowmade/project-new/v1"
ADD_SCHEMA = "knowmade/project-add/v1"
ARCHIVE = ".archive"
SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
DATE_IN_SLUG = re.compile(r"\d{4}-\d{2}-\d{2}")
GOAL_LIMIT = 120
MEMBER_DEPTH = 3
# Names that are the project's own files, never a member a caller can add.
RESERVED = {WHY, LEGACY, "_index.md"}
WHY_TEMPLATE = """# {title}

{goal}

## Context

<!-- Why this project exists, what triggered it, who cares. Delete this section if you don't need it. -->

## Current state

<!-- Where things stand, what's next, outstanding questions. Delete this section if you don't need it. -->
"""


class Refused(Exception):
    """A request the wiki or the spec forbids; nothing was written (exit 1)."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


class Usage(Exception):
    """A malformed request (exit 2)."""


def valid_slug(slug: str) -> bool:
    """The C8 slug rule: lowercase, hyphen-separated, at most 40 characters, no date."""
    return bool(SLUG_RE.fullmatch(slug)) and not DATE_IN_SLUG.search(slug)


def suggest_slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40].strip("-")


def title_from_slug(slug: str) -> str:
    return " ".join(token.capitalize() for token in slug.split("-") if token)


def archived_folders(wiki_root: Path) -> list[Path]:
    archive = wiki_root / "output" / "projects" / ARCHIVE
    if not archive.is_dir():
        return []
    return sorted(child for child in archive.iterdir() if child.is_dir() and not child.name.startswith("."))


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return ""


def why_summary(text: str) -> tuple[str, str]:
    """``(title, goal)`` of a ``WHY.md``: the first ``#`` heading and the first paragraph (120 characters)."""
    title = ""
    paragraph: list[str] = []
    in_comment = False
    for raw in text.splitlines():
        line = raw.strip()
        if in_comment:
            in_comment = "-->" not in line
            continue
        if line.startswith("<!--"):
            in_comment = "-->" not in line
            continue
        heading = re.match(r"#(?!#)\s*(.+)", line)
        if heading and not title:
            title = heading.group(1).strip()
        if line.startswith("#") or not line:
            if paragraph:
                break
            continue
        paragraph.append(line)
    return title, " ".join(paragraph)[:GOAL_LIMIT].rstrip()


def member_files(project: Path) -> list[Path]:
    """Files of a project folder, at most three path levels deep; ``WHY.md``, the legacy manifest and dotfiles are not members."""
    found: list[Path] = []
    for current, dirs, names in os.walk(project):
        depth = len(Path(current).relative_to(project).parts)
        dirs[:] = sorted(name for name in dirs if not name.startswith(".") and depth + 1 < MEMBER_DEPTH)
        for name in sorted(names):
            if name.startswith(".") or (depth == 0 and name in {WHY, LEGACY}):
                continue
            found.append(Path(current) / name)
    return found


def _stale_counts(wiki_root: Path, projects: list[Path]) -> dict[Path, int]:
    """Members per project that the lint's own C8b check reports as ``project-stale``."""
    from .lint_docs import check_project_staleness, load_documents  # lint_docs imports this module

    ctx = LintContext(wiki_root)
    load_documents(ctx)
    counts: dict[Path, int] = {}
    for project in projects:
        before = len(ctx.issues)
        check_project_staleness(ctx, project)
        counts[project] = sum(1 for issue in ctx.issues[before:] if "(project-stale)" in issue.message)
    return counts


def describe(wiki_root: Path, project: Path, status: str, stale: int) -> dict[str, Any]:
    why, legacy = project / WHY, project / LEGACY
    has = has_why(project)
    title, goal = why_summary(_read(why)) if has else ("", "")
    if not has and legacy.is_file():
        try:
            fields = _frontmatter.load(legacy).fields
        except (OSError, UnicodeDecodeError, _frontmatter.FrontmatterError):
            fields = {}
        title, goal = _text(fields.get("title")), _text(fields.get("goal"))[:GOAL_LIMIT]
    members = member_files(project)
    stamps = [path.stat().st_mtime for path in [*members, *(p for p in (why, legacy) if p.is_file())]]
    updated = dt.datetime.fromtimestamp(max(stamps), dt.timezone.utc).date().isoformat() if stamps else None
    return {
        "slug": project.name,
        "title": title or title_from_slug(project.name),
        "goal": goal,
        "status": status,
        "path": project.relative_to(wiki_root).as_posix(),
        "members": len(members),
        "updated": updated,
        "has_why": has,
        "legacy_manifest": legacy.is_file(),
        "stale": stale > 0,
        "stale_members": stale,
    }


def list_projects(wiki_root: Path, *, archived: bool) -> dict[str, Any]:
    active = project_folders(wiki_root)
    counts = _stale_counts(wiki_root, active)
    rows = [describe(wiki_root, project, "active", counts[project]) for project in active]
    if archived:
        rows += [describe(wiki_root, project, "archived", 0) for project in archived_folders(wiki_root)]
    return {"schema": LIST_SCHEMA, "root": str(wiki_root), "archived": archived, "projects": rows}


def _flags(row: dict[str, Any]) -> str:
    flags = []
    if row["legacy_manifest"]:
        flags.append("legacy-manifest" if not row["has_why"] else "legacy-manifest-left")
    if not row["has_why"] and not row["legacy_manifest"]:
        flags.append("no-why")
    if row["stale"]:
        flags.append(f"stale({row['stale_members']})")
    return ", ".join(flags) or "-"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def print_list(payload: dict[str, Any]) -> None:
    for status in ("active", "archived"):
        rows = [row for row in payload["projects"] if row["status"] == status]
        if status == "archived" and not payload["archived"]:
            continue
        print(f"{status.capitalize()} projects ({len(rows)})")
        if not rows:
            continue
        print("")
        print("| Slug | Title | Goal | Members | Updated | Flags |")
        print("|------|-------|------|---------|---------|-------|")
        for row in rows:
            print(
                f"| {row['slug']} | {_cell(row['title'])} | {_cell(row['goal'])} | {row['members']} | "
                f"{row['updated'] or '-'} | {_flags(row)} |"
            )
        print("")
    if not payload["projects"]:
        print(f'No projects in {payload["root"]}. Create one with `llm-wiki project new <slug> --goal "..."`.')


def _topic_root(args: argparse.Namespace, label: str) -> Path:
    """The resolved topic wiki root, or :class:`Usage`."""
    root = resolve_wiki_root_args(args)
    if not root.is_dir():
        raise Usage(f"project {label}: no wiki root at {root}")
    if is_hub(root):
        raise Usage(f"project {label}: {root} is a hub, not a topic wiki; pass --wiki <slug>, --local or --path")
    if is_private_placeholder(root):
        raise Usage(f"project {label}: {root} is a private placeholder, not checked out here")
    return root.resolve()


def _boundary(root: Path) -> BoundaryChecker | None:
    """The cross-boundary resolver for a public wiki; ``None`` for a private one (it may name its own)."""
    try:
        checker = BoundaryChecker(root, private_topic_dirs(root))
    except BoundaryRegistryError as exc:
        raise Usage(str(exc)) from None
    if checker.root_is_private():
        return None
    checker.index_slugs([])
    return checker


def private_references(checker: BoundaryChecker | None, path: Path, rel: str, text: str) -> list[str]:
    """Lines of ``text`` that name or link a ``-PRIVATE`` location; empty for a private wiki."""
    if checker is None:
        return []
    lines = {finding.line for finding in scan_checkpoint_text(text, rel) if finding.category == "private-topic-path"}
    reasons = [f"{rel} line {line} names a -PRIVATE path" for line in sorted(lines)]
    reasons += [f"{rel} line {hit.line}: {hit.message}" for hit in checker.check(path, rel, text)]
    return reasons


def _log_line(root: Path, message: str) -> str:
    """The ``project`` log entry for ``message``, or :class:`Refused` when the log guard would refuse it."""
    refusal = _log_cli.guard_message(message, public=_log_cli.is_public(root))
    if refusal:
        raise Refused([f"log.md entry: {refusal}"])
    return _log_cli.entry_line(today_date().isoformat(), "project", message)


def refresh_output_index(root: Path, entries: list[Path], moved: list[str]) -> str:
    """Make ``output/_index.md`` list ``entries`` and drop the rows of ``moved`` files, as ``lint --fix`` would.

    ``moved`` are paths relative to ``output/``. Returns ``created``, ``updated`` or ``unchanged``.
    """
    from .lint_docs import ensure_dir_index, index_problems  # lint_docs imports this module

    output = root / "output"
    index = output / "_index.md"
    ctx = LintContext(root, fix=True, fix_classes={"index", "create"})
    if not index.exists():
        ensure_dir_index(ctx, output)
        return "created"
    missing, dead = index_problems(output, "output-root", index.read_text(encoding="utf-8"))
    wanted = {path.resolve() for path in entries}
    missing = [path for path in missing if path.resolve() in wanted]
    dead = [link for link in dead if any(variant in moved for variant in link_path_variants(link))]
    if not missing and not dead:
        return "unchanged"
    repair_directory_index(ctx, output, "output-root", missing, dead)
    return "updated"


def _emit(args: argparse.Namespace, payload: dict[str, Any]) -> None:
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))


def _refuse(args: argparse.Namespace, schema: str, root: Path, label: str, reasons: list[str], **extra: Any) -> int:
    payload = {"schema": schema, "root": str(root), "status": "refused", "refusals": reasons, **extra}
    if args.json:
        _emit(args, payload)
    for reason in reasons:
        print(f"project {label}: refused: {reason}", file=sys.stderr)
    return 1


def new_project(root: Path, slug: str, title: str, goal: str, *, dry_run: bool) -> dict[str, Any]:
    projects = root / "output" / "projects"
    folder = projects / slug
    for existing in (folder, projects / ARCHIVE / slug):
        if existing.exists():
            raise Refused([f'project "{slug}" already exists at {existing.relative_to(root).as_posix()}; use a different slug'])
    text = WHY_TEMPLATE.format(title=title, goal=goal)
    why_rel = f"output/projects/{slug}/{WHY}"
    reasons = private_references(_boundary(root), folder / WHY, why_rel, text)
    if reasons:
        raise Refused([*reasons, "a project in a public wiki may not reference a -PRIVATE path; create it in the -PRIVATE sibling"])
    entry = _log_line(root, f"created project {slug}")
    payload: dict[str, Any] = {
        "schema": NEW_SCHEMA,
        "root": str(root),
        "slug": slug,
        "title": title,
        "path": f"output/projects/{slug}",
        "why": why_rel,
        "dry_run": dry_run,
        "log": entry,
    }
    if dry_run:
        payload.update(status="dry-run", index="would-update")
        return payload
    created_dir = not projects.exists()
    folder.mkdir(parents=True)
    try:
        atomic_write_text(folder / WHY, text)
    except OSError:
        shutil.rmtree(projects if created_dir else folder, ignore_errors=True)
        raise
    payload["index"] = refresh_output_index(root, [folder / WHY], [])
    _log_cli.append_entry(root / "log.md", entry)
    payload["status"] = "created"
    return payload


def run_new(args: argparse.Namespace) -> int:
    try:
        slug = args.slug
        if not valid_slug(slug):
            hint = suggest_slug(slug)
            suggestion = f' - use "{hint}" instead' if hint and valid_slug(hint) else ""
            raise Usage(
                f'project new: invalid slug "{slug}"{suggestion} (lowercase letters, digits and hyphens, '
                "at most 40 characters, no date)"
            )
        if args.goal_option and args.goal:
            raise Usage("project new: give the goal once, as the positional argument or as --goal")
        goal = (args.goal_option or args.goal or "").replace("\r\n", "\n").strip()
        if not goal:
            raise Usage("project new: a goal is required (positional or --goal): what is this project trying to accomplish?")
        title = (args.title or title_from_slug(slug)).strip()
        if not title or "\n" in title or "\r" in title:
            raise Usage("project new: --title must be one non-empty line")
        root = _topic_root(args, "new")
        payload = new_project(root, slug, title, goal, dry_run=bool(args.dry_run))
    except Refused as exc:
        return _refuse(args, NEW_SCHEMA, resolve_wiki_root_args(args), "new", exc.reasons, slug=args.slug)
    except Usage as exc:
        print(exc, file=sys.stderr)
        return 2
    if args.json:
        _emit(args, payload)
    elif payload["dry_run"]:
        print(f"Would create project \"{slug}\": {payload['why']}, an entry in output/_index.md and a log.md line; nothing written.")
    else:
        print(f"Created project \"{slug}\" at {payload['path']} (output/_index.md {payload['index']}, log.md appended).")
    return 0


def _resolve_source(root: Path, written: str) -> Path:
    """A path as given: absolute, else under the wiki root, else (when only there it exists) the working directory."""
    candidate = Path(os.path.expanduser(written))
    if candidate.is_absolute() or (root / candidate).exists() or not candidate.exists():
        return candidate if candidate.is_absolute() else root / candidate
    return candidate


def _inbound_references(root: Path, moved: dict[Path, str]) -> dict[str, list[str]]:
    """Files that link or name a file about to move: ``{old path: [referring files]}``."""
    found: dict[str, set[str]] = {}
    for directory in ("wiki", "output", "inventory", "datasets"):
        for doc in markdown_files(root / directory):
            if doc.resolve() in moved or (doc.name == "_index.md" and doc.parent == root / "output"):
                continue
            text = _read(doc)
            referring = doc.relative_to(root).as_posix()
            for old in moved.values():
                if old in text:
                    found.setdefault(old, set()).add(referring)
            for destination in extract_link_destinations(text):
                for variant in link_path_variants(destination):
                    target = None if variant.startswith("/") else (doc.parent / variant).resolve()
                    if target in moved:
                        found.setdefault(moved[target], set()).add(referring)
    return {old: sorted(files) for old, files in sorted(found.items())}


def add_to_project(root: Path, slug: str, written: list[str], *, dry_run: bool) -> dict[str, Any]:
    project = root / "output" / "projects" / slug
    if not has_why(project):
        raise Refused([f'project "{slug}" does not exist; create it first with `llm-wiki project new {slug} --goal "..."`'])
    output = (root / "output").resolve()
    folder = project.resolve()
    projects = (output / "projects").resolve()
    checker = _boundary(root)
    refusals: list[str] = []
    rows: list[dict[str, Any]] = []
    moves: dict[Path, Path] = {}
    taken: set[str] = set()
    for text in written:
        source = _resolve_source(root, text)
        row: dict[str, Any] = {"path": text, "action": "move"}
        try:
            resolved = source.resolve(strict=True)
        except OSError:
            row.update(action="refuse", reason="no such file")
        else:
            if source.is_symlink() or not resolved.is_file():
                row.update(action="refuse", reason="not a regular file")
            elif not resolved.is_relative_to(root):
                row.update(action="refuse", reason="outside the wiki")
            elif resolved.is_relative_to(folder):
                row.update(action="member", to=resolved.relative_to(root).as_posix())
            elif not resolved.is_relative_to(output):
                row.update(action="refuse", reason="only files under output/ can join a project (raw sources and wiki articles stay where they are)")
            elif resolved.is_relative_to(projects):
                row.update(action="refuse", reason="already in another project or the archive; move it with mv")
            elif resolved.name in RESERVED or resolved.name.startswith("."):
                row.update(action="refuse", reason=f"{resolved.name} cannot be a project member")
            elif (folder / resolved.name).exists() or resolved.name in taken:
                row.update(action="refuse", reason=f"{resolved.name} already exists in the project")
            else:
                rel = resolved.relative_to(root).as_posix()
                reasons = private_references(checker, resolved, rel, _read(resolved))
                if reasons:
                    row.update(action="refuse", reason="; ".join(reasons))
                else:
                    taken.add(resolved.name)
                    moves[resolved] = folder / resolved.name
                    row.update(path=rel, to=f"output/projects/{slug}/{resolved.name}")
        if row["action"] == "refuse":
            refusals.append(f"{text}: {row['reason']}")
        rows.append(row)
    if refusals:
        raise Refused(refusals)
    payload: dict[str, Any] = {
        "schema": ADD_SCHEMA, "root": str(root), "slug": slug, "dry_run": dry_run,
        "files": rows, "references": {}, "index": "unchanged", "log": None,
    }
    if not moves:
        payload["status"] = "unchanged"
        return payload
    names = [target.name for target in moves.values()]
    shown = ", ".join(names[:5]) + (f" (+{len(names) - 5} more)" if len(names) > 5 else "")
    entry = _log_line(root, f"added {len(names)} file(s) to project {slug}: {shown}")
    payload["log"] = entry
    payload["references"] = _inbound_references(root, {path: path.relative_to(root).as_posix() for path in moves})
    if dry_run:
        payload.update(status="dry-run", index="would-update")
        return payload
    for source, target in moves.items():
        shutil.move(os.fspath(source), os.fspath(target))
    payload["index"] = refresh_output_index(root, [], [path.relative_to(output).as_posix() for path in moves])
    _log_cli.append_entry(root / "log.md", entry)
    payload["status"] = "added"
    return payload


def run_add(args: argparse.Namespace) -> int:
    try:
        if not valid_slug(args.slug):
            raise Usage(f'project add: invalid slug "{args.slug}"')
        root = _topic_root(args, "add")
        payload = add_to_project(root, args.slug, list(args.files), dry_run=bool(args.dry_run))
    except Refused as exc:
        return _refuse(args, ADD_SCHEMA, resolve_wiki_root_args(args), "add", exc.reasons, slug=args.slug)
    except Usage as exc:
        print(exc, file=sys.stderr)
        return 2
    if args.json:
        _emit(args, payload)
        return 0
    verb = "Would add" if payload["dry_run"] else "Added"
    for row in payload["files"]:
        if row["action"] == "member":
            print(f"{row['to']} is already in project \"{args.slug}\".")
        else:
            print(f"{verb} {row['path']} -> {row['to']}")
    for old, files in payload["references"].items():
        print(f"warning: {old} is linked from {', '.join(files)}; those links break (lint check C4 reports them)", file=sys.stderr)
    if payload["dry_run"]:
        print("Nothing written.")
    return 0


def run_list(args: argparse.Namespace) -> int:
    try:
        root = _topic_root(args, "list")
    except Usage as exc:
        print(exc, file=sys.stderr)
        return 2
    payload = list_projects(root, archived=bool(args.archived))
    if args.json:
        _emit(args, payload)
    else:
        print_list(payload)
    return 0


def run(args: argparse.Namespace) -> int:
    """Dispatch a parsed ``project`` namespace."""
    handlers = {"migrate-why": run_migrate, "list": run_list, "new": run_new, "add": run_add}
    return handlers[args.project_command](args)


PROJECT_HELP = "Deterministic project-folder maintenance under output/projects/."


def register_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Register ``project`` on a parent ``llm-wiki`` parser; dispatch through :func:`run`."""
    project = subparsers.add_parser("project", help=PROJECT_HELP, description=PROJECT_HELP)
    sub = project.add_subparsers(dest="project_command", required=True)
    scope = {
        "local_help": "Use ./.wiki in the current directory.",
        "public_help": f"Resolve --wiki <slug>{PRIVATE_SUFFIX} to the public sibling <slug>.",
        "include_archived_help": "Allow --wiki to resolve an archived topic.",
    }
    list_parser = sub.add_parser(
        "list",
        help="List the projects of a topic wiki with goal, member count, last update and flags; read-only.",
        description=(
            "Read-only. One row per folder under output/projects/: title (first # of WHY.md), goal "
            "(first paragraph, 120 characters), member files (three levels deep, without WHY.md), "
            "last update (newest file date), and the flags the lint reports as project-legacy-manifest "
            "(C8c) and project-stale (C8b, counted by the lint's own check). Exit 0, or 2 when the root "
            "is missing, a hub or a private placeholder."
        ),
    )
    list_parser.add_argument("path", nargs="?", help="Topic wiki root to list.")
    add_scope_arguments(list_parser, **scope)
    list_parser.add_argument("--archived", action="store_true", help="Also list projects under output/projects/.archive/.")
    list_parser.add_argument("--json", action="store_true", help="Print a machine-readable JSON list.")
    new_parser = sub.add_parser(
        "new",
        help="Scaffold a project: WHY.md, an output/_index.md entry and a log.md line.",
        description=(
            "Creates output/projects/<slug>/WHY.md from the template in projects.md, adds the project "
            "to output/_index.md (creating it when missing) and appends one 'project' entry to log.md. "
            "Exit 0 created; 1 refused with nothing written (the project exists, active or archived; a "
            "public wiki's goal or title names a -PRIVATE path; the log guard refuses the entry); 2 for "
            "an invalid slug (lowercase letters, digits, hyphens, at most 40 characters, no date), a "
            "missing goal, a bad title or an unusable root."
        ),
    )
    new_parser.add_argument("slug", help="Project folder name, e.g. bitcoin-quantum-risk.")
    new_parser.add_argument("goal", nargs="?", help="What the project is trying to accomplish (or use --goal).")
    new_parser.add_argument("--goal", dest="goal_option", metavar="GOAL", help="The goal, instead of the positional argument.")
    new_parser.add_argument("--title", help="Heading of WHY.md (default: the slug, capitalized per hyphen).")
    new_parser.add_argument("--path", help="Topic wiki root path.")
    add_scope_arguments(new_parser, **scope)
    new_parser.add_argument("--dry-run", action="store_true", help="Check and report what would be written; write nothing.")
    new_parser.add_argument("--json", action="store_true", help="Print a machine-readable JSON result.")
    add_parser = sub.add_parser(
        "add",
        help="Move loose output files into a project (idempotent).",
        description=(
            "Moves each file into output/projects/<slug>/ by its base name (the folder is the "
            "membership; no frontmatter is added), drops its row from output/_index.md and appends one "
            "'project' entry to log.md. Paths are absolute or relative to the wiki root. Only regular "
            "files under output/ outside any project can move; a file already in the project is left "
            "alone. Warns about wiki/ and output/ files that link the old path. Exit 0 done (or nothing "
            "to do); 1 refused with nothing moved (no such project or file, outside the wiki, outside "
            "output/, in another project, a name clash, a public wiki's file referencing a -PRIVATE "
            "path); 2 for an invalid slug or an unusable root."
        ),
    )
    add_parser.add_argument("slug", help="Project folder name; the project must already have a WHY.md.")
    add_parser.add_argument("files", nargs="+", metavar="path", help="File to move into the project.")
    add_parser.add_argument("--path", help="Topic wiki root path.")
    add_scope_arguments(add_parser, **scope)
    add_parser.add_argument("--dry-run", action="store_true", help="Check and report what would move; write nothing.")
    add_parser.add_argument("--json", action="store_true", help="Print a machine-readable JSON result.")
    migrate_why = sub.add_parser(
        "migrate-why",
        help="Migrate legacy _project.md manifests to WHY.md (dry run unless --apply).",
        description=(
            "Lossless C8c migration: title and goal from the frontmatter, derived Members sections "
            "dropped, every other section kept in order. Skips folders that have WHY.md; refuses a "
            "manifest with nothing to preserve. Exit 0, or 2 when the root is missing or is a hub."
        ),
    )
    migrate_why.add_argument("path", nargs="?", help="Wiki root path.")
    add_scope_arguments(
        migrate_why,
        local_help="Use ./.wiki in the current directory.",
        public_help="Resolve --wiki <slug> to the public sibling without -PRIVATE.",
        include_archived_help="Allow --wiki to resolve an archived topic.",
    )
    mode = migrate_why.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Preview only (the default).")
    mode.add_argument("--apply", action="store_true", help="Write WHY.md, delete _project.md, log once.")
    migrate_why.add_argument("--json", action="store_true", help="Print JSON.")
    project.set_defaults(func=run)
    return project
