"""The ``project`` namespace: deterministic project-folder maintenance (emercons/knowmade#83).

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
import json
import re
import sys
from pathlib import Path
from typing import Any

from . import frontmatter as _frontmatter
from .common import add_scope_arguments
from .markdown import append_log, write_text_preserving
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


def run(args: argparse.Namespace) -> int:
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


PROJECT_HELP = "Deterministic project-folder maintenance under output/projects/."


def register_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Register ``project`` on a parent ``llm-wiki`` parser; dispatch through :func:`run`."""
    project = subparsers.add_parser("project", help=PROJECT_HELP, description=PROJECT_HELP)
    sub = project.add_subparsers(dest="project_command", required=True)
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
