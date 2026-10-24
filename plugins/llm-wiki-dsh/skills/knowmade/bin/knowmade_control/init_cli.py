"""The ``init`` namespace: ``llm-wiki init`` scaffolds a wiki (emercons/knowmade#83).

The deterministic half of ``wiki init``: the directory structure, ``config.md``
with a fresh ``wiki_id``, the starter topic guide, every required ``_index.md``,
``log.md`` with its ``init`` entry, and (for a topic) the row in the hub's
``wikis.json`` plus the hub ``_index.md`` table. Three forms:

- ``--topic <slug>``: ``HUB/topics/<slug>/``, registered in ``HUB/wikis.json``.
  A hub that does not exist yet is created (``wikis.json``, ``_index.md``,
  ``log.md``, ``topics/`` and the ``.gitignore`` rules) when the hub path was
  chosen explicitly (``--hub``, ``LLM_WIKI_HUB`` or the config file); the
  implicit ``~/wiki`` fallback is never created. A slug ending ``-PRIVATE``
  makes a private topic: it is registered by slug, path and id only, and a git
  hub that would not ignore it gets the canonical ignore rule first.
- ``--local``: ``.wiki/`` in the current directory, ignored by git as
  ``adopt apply`` does. A project wiki is not registered in a hub.
- ``<path>``: a standalone wiki rooted at ``path``, registered nowhere.

The new wiki is built in a sibling staging directory and renamed into place (or,
in an existing empty directory, built in place with rollback), so a failure never
leaves a half-made wiki. Fail closed: an existing non-empty
directory that is not a wiki, a registry row that points elsewhere, an archived
topic of that slug or an unreadable registry refuse (exit 1) with nothing
written; a wiki that already exists is a no-op (exit 0). Indexes come from the
renderers ``lint --fix`` uses, so the result passes ``lint`` and ``index check``.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import fsio as _fsio
from . import hub as _hub
from . import identity as _identity
from . import rules as _rules
from .adopt_cli import ensure_wiki_ignored
from .adoption_plan import is_knowmade_checkout
from .common import PRIVATE_SUFFIX, is_initialized_topic, read_registry, write_registry
from .lint_docs import REQUIRED_INDEX_DIRECTORIES, ensure_dir_index
from .lint_report import LintContext
from .log_cli import HEADER as LOG_HEADER
from .log_cli import append_entry, entry_line
from .markdown import default_schema_text, today_date, yaml_quote
from .roots import is_hub, read_local_registry, resolve_hub_selection

SCHEMA = "knowmade/init/v1"
MAX_SLUG_LENGTH = 80
RESERVED_SLUGS = frozenset({"hub"})
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# The core structure ``wiki-structure.md`` gives a new wiki; ``inventory/`` and
# ``datasets/`` are created lazily by their commands, never here.
CORE_DIRECTORIES = (
    "inbox",
    "inbox/.processed",
    "raw",
    "raw/articles",
    "raw/papers",
    "raw/repos",
    "raw/notes",
    "raw/data",
    "wiki",
    "wiki/concepts",
    "wiki/topics",
    "wiki/references",
    "wiki/theses",
    "output",
    ".obsidian",
)
OBSIDIAN_FILES = {
    ".obsidian/app.json": {
        "showFrontmatter": True,
        "alwaysUpdateLinks": True,
        "newLinkFormat": "relative",
        "useMarkdownLinks": False,
    },
    ".obsidian/appearance.json": {"accentColor": ""},
    ".obsidian/graph.json": {
        "collapse-filter": False,
        "search": "",
        "showTags": True,
        "showAttachments": False,
        "showOrphans": True,
        "collapse-color-groups": False,
        "collapse-display": False,
        "showArrow": True,
        "textFadeMultiplier": 0,
        "nodeSizeMultiplier": 1,
        "lineSizeMultiplier": 1,
    },
}
WIKI_FILES = ("_index.md", "config.md", "schema.md", "log.md")
HUB_FILES = ("wikis.json", "_index.md", "log.md", ".gitignore")
INIT_MESSAGE = "Wiki initialized"


class Refusal(Exception):
    """A state that must not be written over: exit 1, nothing written."""


@dataclass
class Target:
    """Everything one ``init`` run decided before touching the disk."""

    mode: str
    root: Path
    title: str
    description: str
    slug: str | None = None
    private: bool = False
    hub: Path | None = None
    hub_source: str | None = None
    project_root: Path | None = None
    no_gitignore: bool = False
    registry: dict[str, Any] = field(default_factory=dict)


def validate_slug(slug: str) -> str | None:
    """Why ``slug`` is not a usable topic slug, or ``None``."""
    if not slug:
        return "the topic slug is empty"
    if any(ord(char) < 32 or ord(char) == 127 for char in slug):
        return "the topic slug contains a control character"
    if "/" in slug or "\\" in slug:
        return "the topic slug must not contain a path separator"
    if slug.startswith("."):
        return "the topic slug must not start with a dot"
    if ".." in slug:
        return "the topic slug must not contain '..'"
    if not _SLUG_RE.match(slug) or slug.endswith("."):
        return "the topic slug may only use letters, digits, '.', '_' and '-', starting with a letter or digit"
    if len(slug) > MAX_SLUG_LENGTH:
        return f"the topic slug is longer than {MAX_SLUG_LENGTH} characters"
    if slug in RESERVED_SLUGS:
        return f"the topic slug {slug!r} is reserved"
    if slug.lower().endswith(PRIVATE_SUFFIX.lower()) and not slug.endswith(PRIVATE_SUFFIX):
        return f"the private suffix is exactly {PRIVATE_SUFFIX} (uppercase)"
    if slug.endswith(PRIVATE_SUFFIX) and len(slug) == len(PRIVATE_SUFFIX):
        return "the topic slug is only the private suffix"
    return None


def default_title(name: str) -> str:
    base = name[: -len(PRIVATE_SUFFIX)] if name.endswith(PRIVATE_SUFFIX) else name
    return base.replace("-", " ").replace("_", " ").title()


def _one_line(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


# ---------------------------------------------------------------------------
# Content: every file the scaffold writes, rendered from one place.
# ---------------------------------------------------------------------------


def config_text(title: str, description: str, date: str, wiki_id: str) -> str:
    return (
        "---\n"
        f"title: {yaml_quote(title)}\n"
        f"description: {yaml_quote(description)}\n"
        f"created: {date}\n"
        f"wiki_id: {wiki_id}\n"
        f"{_rules.RULE_SET_KEY}: {_rules.CURRENT_RULE_SET}\n"
        "freshness_threshold: 70\n"
        "---\n\n"
        "# Wiki Configuration\n\n"
        f"## Scope\n\n{description}\n\n"
        "## Conventions\n\nStandard Knowmade conventions apply.\n"
    )


def master_index_text(title: str, description: str, date: str) -> str:
    return (
        f"# {title}\n\n> {description}\n\nLast updated: {date}\n\n"
        "## Statistics\n\n- Sources: 0\n- Articles: 0\n- Outputs: 0\n\n"
        "## Quick Navigation\n\n"
        "- [All Sources](raw/_index.md)\n"
        "- [Concepts](wiki/concepts/_index.md)\n"
        "- [Topics](wiki/topics/_index.md)\n"
        "- [References](wiki/references/_index.md)\n"
        "- [Theses](wiki/theses/_index.md)\n"
        "- [Outputs](output/_index.md)\n"
    )


def planned_files() -> list[str]:
    """Every file of a new wiki, relative to its root, in a stable order."""
    files = list(WIKI_FILES) + sorted(OBSIDIAN_FILES)
    files += [f"{rel}/_index.md" for rel in REQUIRED_INDEX_DIRECTORIES]
    return sorted(files)


def planned_directories() -> list[str]:
    return sorted(set(CORE_DIRECTORIES) | set(REQUIRED_INDEX_DIRECTORIES))


def build_wiki(stage: Path, target: Target, wiki_id: str, date: str) -> None:
    """Write the whole wiki into the empty directory ``stage``."""
    for rel in planned_directories():
        (stage / rel).mkdir(parents=True, exist_ok=True)
    _fsio.atomic_write_text(stage / "config.md", config_text(target.title, target.description, date, wiki_id))
    _fsio.atomic_write_text(stage / "schema.md", default_schema_text(stage))
    _fsio.atomic_write_text(stage / "log.md", LOG_HEADER + entry_line(date, "init", INIT_MESSAGE) + "\n")
    _fsio.atomic_write_text(stage / "_index.md", master_index_text(target.title, target.description, date))
    for rel, payload in OBSIDIAN_FILES.items():
        _fsio.write_json_atomic(stage / rel, payload)
    # The shared lint renderers write the sub-indexes. Children first: a category
    # pointer index lists the child indexes that already exist.
    ctx = LintContext(stage, fix=True, fix_classes={"index", "create"})
    for rel in reversed(REQUIRED_INDEX_DIRECTORIES):
        ensure_dir_index(ctx, stage / rel)


# ---------------------------------------------------------------------------
# Inspecting what is already on disk.
# ---------------------------------------------------------------------------


def classify_root(root: Path) -> str:
    """``absent``, ``empty``, ``wiki`` or (raising) a refusal for anything else."""
    if root.is_symlink():
        raise Refusal(f"{root} is a symbolic link; init will not write through it")
    if not root.exists():
        return "absent"
    if not root.is_dir():
        raise Refusal(f"{root} exists and is not a directory")
    if not any(root.iterdir()):
        return "empty"
    if is_initialized_topic(root):
        return "wiki"
    raise Refusal(f"{root} exists, is not empty and is not a Knowmade wiki; nothing was changed")


def missing_pieces(root: Path) -> list[str]:
    return [rel for rel in planned_directories() + planned_files() if not (root / rel).exists()]


def inspect_hub(target: Target) -> dict[str, Any]:
    """Decide the hub side of a topic init; returns the hub plan, raises ``Refusal``."""
    hub = target.hub
    assert hub is not None and target.slug is not None
    if is_knowmade_checkout(hub):
        raise Refusal(f"{hub} is the Knowmade engine checkout, not a hub; pass --hub <path>")
    exists = hub.exists()
    if exists and not hub.is_dir():
        raise Refusal(f"hub path {hub} exists and is not a directory")
    if exists and is_hub(hub):
        target.registry = read_registry(hub)
        return {"path": str(hub), "created": False}
    if exists and any(hub.iterdir()):
        raise Refusal(
            f"{hub} is not empty and is not a hub (a hub is wikis.json plus topics/); "
            "pass --hub <path> or run `llm-wiki hub where`"
        )
    if target.hub_source == "default":
        raise Refusal(
            f"no hub is configured and {hub} does not exist; pass --hub <path>, set LLM_WIKI_HUB "
            "or put hub_path in ~/.config/llm-wiki/config.json"
        )
    return {"path": str(hub), "created": True}


def check_registration(target: Target, hub_plan: dict[str, Any]) -> str:
    """The registry action for the topic: ``add``, ``unchanged``; raises when the slug is taken."""
    hub, slug = target.hub, target.slug
    assert hub is not None and slug is not None
    expected = f"topics/{slug}"
    if hub_plan["created"]:
        return "add"
    wikis = target.registry.get("wikis", {})
    topics_dir = hub / "topics"
    known = set(wikis) | ({p.name for p in topics_dir.iterdir()} if topics_dir.is_dir() else set())
    for other in sorted(known):
        if other != slug and other.casefold() == slug.casefold():
            raise Refusal(f"topic {other!r} differs from {slug!r} only by case")
    if (hub / "topics" / ".archive" / slug).exists():
        raise Refusal(
            f"archived topic {slug!r} exists; restore it with `llm-wiki archive restore {slug}` or choose another slug"
        )
    if slug in read_local_registry(hub):
        raise Refusal(f"{hub / 'wikis.local.json'} points {slug!r} at a path on this machine; remove it first")
    entry = wikis.get(slug)
    if entry is None:
        return "add"
    if not isinstance(entry, dict):
        raise Refusal(f"wikis.json entry {slug!r} is not an object")
    registered = str(entry.get("path") or "").replace("\\", "/").rstrip("/")
    if registered != expected:
        raise Refusal(f"{slug!r} is already registered in wikis.json at {registered or '(no path)'}, not {expected}")
    if str(entry.get("status") or "active") == "archived":
        raise Refusal(f"{slug!r} is registered as archived; restore it with `llm-wiki archive restore {slug}`")
    return "unchanged"


# ---------------------------------------------------------------------------
# Writing.
# ---------------------------------------------------------------------------


def stage_and_place(root: Path, target: Target, wiki_id: str, date: str) -> bool:
    """Build the wiki at ``root``; returns ``True`` when it was built inside an existing empty directory.

    An absent ``root`` is built in a sibling staging directory and renamed into
    place. An existing empty ``root`` is built in place instead: removing and
    renaming a directory fails on Windows when it is the current directory or
    held by another process. A failure removes what was created inside it.
    """
    if root.exists():  # classify_root only lets an empty directory through
        try:
            build_wiki(root, target, wiki_id, date)
        except BaseException:
            for child in list(root.iterdir()):
                _remove(child)
            raise
        return True
    root.parent.mkdir(parents=True, exist_ok=True)
    stage = root.parent / f".init-{root.name}-{uuid.uuid4().hex[:8]}"
    stage.mkdir()
    try:
        build_wiki(stage, target, wiki_id, date)
        os.rename(stage, root)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return False


def registry_row(target: Target, wiki_id: str) -> dict[str, Any]:
    row: dict[str, Any] = {"path": f"topics/{target.slug}"}
    if not target.private:
        row["description"] = target.description
    row["status"] = "active"
    row["id"] = wiki_id
    return row


def new_registry() -> dict[str, Any]:
    return {
        "hub_id": _identity.new_hub_id(),
        "default": "<HUB>",
        "wikis": {"hub": {"path": "<HUB>", "description": "Global knowledge base"}},
        "local_wikis": [],
    }


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def create_hub_skeleton(hub: Path, date: str, undo: list[Path]) -> None:
    """A new hub's own files, minus ``wikis.json`` and ``_index.md`` (written with the topic row).

    ``undo`` collects what to remove if a later step fails: the topmost directory
    created, or (for an existing empty hub directory) each file made inside it.
    """
    if not hub.exists():
        top = hub
        while not top.parent.exists():
            top = top.parent
        hub.mkdir(parents=True)
        undo.append(top)
    else:
        undo.extend(hub / name for name in HUB_FILES + ("topics",))
    (hub / "topics").mkdir()
    _fsio.atomic_write_text(hub / "log.md", LOG_HEADER + entry_line(date, "init", "Hub initialized") + "\n")
    _hub.append_private_ignore_rule(hub)
    _hub.ensure_state_ignore_rules(hub)


def register_topic(target: Target, hub_plan: dict[str, Any], wiki_id: str, date: str) -> None:
    hub = target.hub
    assert hub is not None and target.slug is not None
    original = copy.deepcopy(target.registry)
    data = new_registry() if hub_plan["created"] else target.registry
    data.setdefault("wikis", {})[target.slug] = registry_row(target, wiki_id)
    write_registry(hub, data)
    try:
        _hub.write_hub_index(hub, data)
        append_entry(hub / "log.md", entry_line(date, "init", f"topic {target.slug}"))
    except BaseException:
        if not hub_plan["created"]:
            write_registry(hub, original)
        raise


def private_ignore_needed(target: Target, hub_plan: dict[str, Any]) -> bool:
    """A private topic in a git hub that would not ignore it: the hub must get the rule first."""
    if not target.private or hub_plan["created"]:
        return False
    hub = target.hub
    assert hub is not None
    return _hub.is_inside_git_work_tree(hub) and not _hub.hub_ignores_private_topic(hub, f"topics/{target.slug}")


# ---------------------------------------------------------------------------
# The command.
# ---------------------------------------------------------------------------


def build_target(args: argparse.Namespace) -> Target:
    """Resolve the three forms to one ``Target``; usage errors raise ``SystemExit(2)``-style ``ValueError``."""
    forms = [bool(args.topic), bool(args.local), bool(args.path)]
    if sum(forms) != 1:
        raise ValueError("choose exactly one of --topic <slug>, --local or a <path>")
    if args.hub and not args.topic:
        raise ValueError("--hub only applies to --topic")
    if args.no_gitignore and not args.local:
        raise ValueError("--no-gitignore only applies to --local")
    description = _one_line(args.description or "")
    if args.topic:
        slug = args.topic
        problem = validate_slug(slug)
        if problem:
            raise ValueError(problem)
        selection = resolve_hub_selection(args.hub)
        title = _one_line(args.title or "") or default_title(slug)
        private = slug.endswith(PRIVATE_SUFFIX)
        return Target(
            "topic", selection.path / "topics" / slug, title,
            description or f"Knowledge base for {title}.",
            slug=slug, private=private, hub=selection.path, hub_source=selection.source,
        )
    if args.local:
        project = Path.cwd()
        title = _one_line(args.title or "") or project.name
        return Target(
            "local", project / ".wiki", title,
            description or f"Project knowledge for {project.name}.",
            private=True, project_root=project, no_gitignore=args.no_gitignore,
        )
    root = Path(os.path.abspath(args.path))
    title = _one_line(args.title or "") or default_title(root.name)
    return Target(
        "standalone", root, title, description or f"Knowledge base for {title}.",
        private=root.name.endswith(PRIVATE_SUFFIX),
    )


def execute(target: Target, dry_run: bool) -> dict[str, Any]:
    root = target.root
    date = today_date().isoformat()
    if target.mode == "local" and target.project_root and is_knowmade_checkout(target.project_root):
        raise Refusal("this is the Knowmade engine checkout; run init from the research/project directory")
    if target.mode == "standalone" and (is_knowmade_checkout(root) or is_knowmade_checkout(root.parent)):
        raise Refusal("a wiki is not part of the Knowmade engine checkout; choose another path")

    hub_plan: dict[str, Any] | None = None
    registry_action: str | None = None
    if target.mode == "topic":
        hub_plan = inspect_hub(target)
    state = classify_root(root) if hub_plan is None or not hub_plan["created"] else "absent"
    if target.mode == "topic":
        assert hub_plan is not None
        registry_action = check_registration(target, hub_plan)

    result: dict[str, Any] = {
        "schema": SCHEMA,
        "mode": target.mode,
        "dry_run": dry_run,
        "root": str(root),
        "slug": target.slug,
        "private": target.private,
        "title": target.title,
        "hub": hub_plan,
    }
    initialised = state == "wiki"
    wiki_id = _identity.read_wiki_id(root) if initialised else None
    if initialised:
        result["status"] = "already-initialised"
        result["wiki_id"] = wiki_id
        result["missing"] = missing_pieces(root)
        result["directories"] = []
        result["files"] = []
        if target.mode == "topic" and registry_action == "add" and wiki_id is None:
            raise Refusal(f"{root} has no wiki_id; run `llm-wiki identity assign` before registering it")
        entry = target.registry.get("wikis", {}).get(target.slug) if target.mode == "topic" else None
        if isinstance(entry, dict) and entry.get("id") and wiki_id and entry["id"] != wiki_id:
            raise Refusal(f"wikis.json gives {target.slug!r} the id {entry['id']} but {root} declares {wiki_id}")
    else:
        result["status"] = "would-create" if dry_run else "created"
        result["directories"] = planned_directories()
        result["files"] = planned_files()
        result["wiki_id"] = None if dry_run else _identity.new_wiki_id()
        wiki_id = result["wiki_id"]
    result["registry"] = (
        {"path": str(target.hub / "wikis.json"), "action": registry_action, "row": f"topics/{target.slug}"}
        if target.mode == "topic" and target.hub is not None
        else None
    )
    result["gitignore"] = None
    result["hub_ignore_rule_added"] = (
        target.mode == "topic"
        and hub_plan is not None
        and (not initialised or registry_action == "add")
        and private_ignore_needed(target, hub_plan)
    )
    if dry_run:
        if target.mode == "local" and target.project_root is not None:
            result["gitignore"] = ensure_wiki_ignored(target.project_root, root, target.no_gitignore, dry_run=True)
        return result

    undo: list[Path] = []
    try:
        if not initialised and target.mode == "topic" and hub_plan is not None and hub_plan["created"]:
            assert target.hub is not None
            create_hub_skeleton(target.hub, date, undo)
        if result["hub_ignore_rule_added"]:
            assert target.hub is not None
            _hub.append_private_ignore_rule(target.hub)  # before the private wiki is created or registered
        if not initialised:
            if stage_and_place(root, target, wiki_id or "", date):
                undo.extend(sorted(root.iterdir()))  # built in place: the directory itself predates init
            else:
                undo.append(root)
        if target.mode == "topic" and hub_plan is not None and registry_action == "add":
            assert target.hub is not None
            register_topic(target, hub_plan, wiki_id or "", date)
        if target.mode == "local" and target.project_root is not None:
            result["gitignore"] = ensure_wiki_ignored(target.project_root, root, target.no_gitignore)
    except BaseException:
        for path in reversed(undo):
            _remove(path)
        raise
    return result


def print_result(result: dict[str, Any]) -> None:
    status = result["status"]
    kind = {"topic": "topic wiki", "local": "project wiki", "standalone": "wiki"}[result["mode"]]
    if result["private"] and result["mode"] == "topic":
        kind = "private topic wiki"
    verb = {
        "created": "Initialised",
        "would-create": "Would initialise",
        "already-initialised": "Already initialised:",
    }[status]
    label = result["slug"] if result["slug"] else result["title"]
    print(f"{verb} {kind} {label}")
    print(f"Root:     {result['root']}")
    if result.get("wiki_id"):
        print(f"Wiki id:  {result['wiki_id']}")
    hub = result["hub"]
    if hub:
        print(f"Hub:      {hub['path']}{' (new hub)' if hub['created'] else ''}")
    registry = result["registry"]
    if registry:
        print(f"Registry: {registry['action']} {registry['row']} in {registry['path']}")
    if result.get("hub_ignore_rule_added"):
        print(f"Ignore:   appends `{_hub.PRIVATE_IGNORE_RULE}` to the hub .gitignore")
    ignore = result["gitignore"]
    if ignore and ignore["action"] in ("appended", "would-append"):
        print(f"Ignore:   {ignore['action'].replace('-', ' ')} `{ignore['rule']}` in .gitignore")
    elif ignore and ignore["action"] == "opted-out":
        print(f"WARNING:  `{ignore['rule']}` is not ignored by git; commit it deliberately or add it to .gitignore")
    if status == "already-initialised" and result["missing"]:
        print(f"Missing:  {len(result['missing'])} required paths ({', '.join(result['missing'][:5])}"
              f"{', ...' if len(result['missing']) > 5 else ''}); `llm-wiki lint --fix` creates them")
    if status != "already-initialised":
        word = "Created:" if status == "created" else "Plan:"
        print(f"{word:<10}{len(result['directories'])} directories, {len(result['files'])} files")
    if status == "would-create":
        print("No files were changed.")


def run(args: argparse.Namespace) -> int:
    try:
        target = build_target(args)
    except ValueError as exc:
        print(f"init: {exc}", file=sys.stderr)
        return 2
    try:
        result = execute(target, args.dry_run)
    except Refusal as exc:
        print(f"init: refused: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        # execute() has already rolled back whatever it created.
        print(f"init: could not write {target.root}: {exc}; nothing was left behind", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print_result(result)
    return 0


INIT_HELP = "Scaffold a topic, project-local or standalone wiki (structure, config, indexes, log, registry row)."


def register_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Register ``init`` on a parent ``llm-wiki`` parser; dispatch through :func:`run`."""
    init = subparsers.add_parser(
        "init",
        help=INIT_HELP,
        description=(
            "Deterministic wiki scaffold (no LLM). Forms: --topic <slug> (in the hub, registered in "
            "wikis.json; a -PRIVATE slug is a private topic), --local (.wiki/ here) or <path> "
            "(standalone, registered nowhere). Exit 0 on success, --dry-run and an existing wiki; 1 "
            "refused with nothing written (non-empty non-wiki target, slug registered elsewhere or "
            "archived, no hub, unreadable registry); 2 invalid usage or slug."
        ),
    )
    init.add_argument("path", nargs="?", help="Standalone wiki root.")
    init.add_argument(
        "--topic",
        metavar="SLUG",
        help=f"Create HUB/topics/<SLUG>/ and register it ({PRIVATE_SUFFIX} slug: private).",
    )
    init.add_argument("--local", action="store_true", help="Create .wiki/ here (ignored by version control in a project).")
    init.add_argument("--hub", help="Hub for --topic (default: the configured hub).")
    init.add_argument("--title", help="Wiki title (default: from the slug or directory name).")
    init.add_argument("--description", help="One-line description (config.md; wikis.json if public).")
    init.add_argument("--no-gitignore", action="store_true", help="With --local, leave the ignore file alone.")
    init.add_argument("--dry-run", action="store_true", help="Print the plan, write nothing.")
    init.add_argument("--json", action="store_true", help="Print JSON.")
    init.set_defaults(func=run)
    return init
