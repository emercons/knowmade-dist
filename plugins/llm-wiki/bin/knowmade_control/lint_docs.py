"""Per-file lint checks on a wiki root, and their ``--fix`` repairs.

Structure (C1), frontmatter schema and the legacy-frontmatter repair (C2),
the C13 alias tables (``fix_frontmatter_aliases``),
index consistency and in-place index repair (C3), source provenance and the
source-reference rewrite (C4b, C18), tags (C5), coverage and its repair (C6),
projects (C8), placement (C11), unknown files (C12) and source lifecycle
pointers (C20). The former agent passes made deterministic by
emercons/knowmade#83 (master index statistics C3, one-way See Also C4, tag
spelling variants C5, orphan articles C6, legacy manifests and staleness C8,
project candidates C9, inventory/dataset migration candidates C16/C17) report
through ``agent_pass_issue``: info in rules/3, the message ending in its
``(kind)``. Freshness and volatility (C14, C15) live in ``freshness``
(knowmade#108). The order these run in, and therefore the order the repairs
happen in, is ``lint.lint_steps``. Split out of ``lint`` (emercons/knowmade#105
R3).
"""

from __future__ import annotations

import datetime as dt
import os
import re
import shutil
import urllib.parse
from pathlib import Path

from . import frontmatter as _frontmatter
from . import projects as _projects
from . import rule_lint as _rule_lint
from . import rules as _rules
from . import source_lifecycle as _source_lifecycle
from .common import (
    PRIVATE_SUFFIX,
    as_string_list,
    first_body_summary,
    parse_date,
    is_under,
    strip_matching_quotes,
    table_cell,
)
from .indexing import (
    extract_link_destinations,
    index_contract,
    index_contract_entries,
    index_contract_label,
    index_links_to,
    is_index_checked_directory,
    is_local_markdown_link,
    refresh_index_rows,
    regenerate_directory_index,
    repair_directory_index,
    stale_index_rows,
)
from .lint_links import WIKILINK_RE, markdown_relative_link, resolve_link_target, slugify, strip_code_blocks
from .lint_report import Document, LintContext, document_text, markdown_files
from .markdown import (
    default_schema_text,
    split_markdown_frontmatter,
    today_date,
    write_text_preserving,
    yaml_quote,
)
from .roots import (
    WINDOWS_ABSOLUTE_PATH_RE,
    expand_leading_tilde,
    is_hub,
    is_permission_denied,
    permission_denied_message,
    windows_absolute_path,
)


RAW_TYPES = {"articles", "papers", "repos", "notes", "data"}
ARTICLE_CATEGORIES = {"concept", "topic", "reference"}
ARTICLE_DIRS = {
    "concept": "concepts",
    "topic": "topics",
    "reference": "references",
}
INVENTORY_KINDS = {
    "item",
    "idea",
    "ingest-candidate",
    "entity",
    "corpus",
    "question",
    "task",
    "artifact",
    "watch",
}
INVENTORY_STATUSES = {
    "proposed",
    "active",
    "blocked",
    "ingested",
    "superseded",
    "archived",
}
INVENTORY_PRIORITIES = {"p0", "p1", "p2", "p3", "p4"}
DATASET_STATUSES = {"proposed", "active", "external", "archived", "unavailable"}
DATASET_STORAGE = {"local", "remote", "external", "hybrid"}
SCHEMA_STATUSES = {"unknown", "inferred", "declared", "validated"}
CONFIDENCE_VALUES = {"high", "medium-high", "medium", "medium-low", "low"}
VOLATILITY_VALUES = {"hot", "warm", "cold"}
SCHEMELESS_WEB_SOURCE_RE = re.compile(
    r"^(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?::\d+)?(?:[/#?]|$)"
)

ROOT_ALLOWED = {
    "_index.md",
    "README.md",
    "config.md",
    "goals.md",
    "schema.md",
    "log.md",
    # Agent-instruction and boundary files are non-content by nature: a topic
    # owner (or the hub, per topic) may ship them at the topic root.
    "AGENTS.md",
    "CLAUDE.md",
    "PRIVATE_BOUNDARY.md",
    "raw",
    "wiki",
    "inventory",
    "datasets",
    "output",
    "inbox",
    ".obsidian",
    ".librarian",
    ".audit",
    ".research-session.json",
    ".thesis-session.json",
    ".session-events.jsonl",
    ".session-checkpoint.json",
    ".sessions",
}
HUB_ALLOWED = {
    "wikis.json",
    "_index.md",
    "README.md",
    "log.md",
    "topics",
    "inbox",
    ".sessions",
    ".skills",
    # Hub init writes the private-topic ignore rule here before the hub is
    # necessarily a git repository; it is hub metadata, not stray content.
    ".gitignore",
}
# When a wiki or hub root is itself a git repository root, these conventional
# VCS/project files legitimately coexist with the wiki schema and must never
# be treated as unexpected content to quarantine into inbox/.unknown.
GIT_ROOT_META_ALLOWED = {
    ".git",
    ".github",
    "AGENTS.md",
    "CLAUDE.md",
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "LICENSE.md",
    "SECURITY.md",
    ".gitignore",
    ".gitattributes",
    ".gitmodules",
    # Conventional project directories that share a repository root with the
    # wiki or hub (the hub keeps its tooling in scripts/).
    "scripts",
    "docs",
    "tools",
    ".githooks",
}

# File names the operating system or its file browser creates on its own. They
# are never content: lint ignores them everywhere and ``--fix`` never moves
# them. ``._*`` is the AppleDouble companion macOS writes next to a file.
OS_METADATA_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}
APPLEDOUBLE_PREFIX = "._"
# Root-level names a corpus may never widen: layout directories whose contents
# have their own allowlists.
LINT_ALLOW_ROOT_KEY = "lint_allow_root"
LINT_ALLOW_ROOT_FORBIDDEN = {"raw", "wiki", "inventory", "datasets", "output", "inbox"}
RAW_ALLOWED = {"_index.md", "articles", "papers", "repos", "notes", "data"}
WIKI_ALLOWED = {"_index.md", "concepts", "topics", "references", "theses"}
INVENTORY_ALLOWED = {
    "_index.md",
    "items",
    "ideas",
    "candidates",
    "entities",
    "corpora",
    "views",
}
DATASET_CHILD_ALLOWED = {"_index.md", "MANIFEST.md", "samples", "profiles", "queries"}


# The frontmatter text helpers live in ``frontmatter`` (one parser, one writer);
# lint keeps these local names for its own repair code.
frontmatter_field_value = _frontmatter.field_value
frontmatter_has_value = _frontmatter.has_value
insert_frontmatter_after_title = _frontmatter.insert_after_title
append_frontmatter_lines = _frontmatter.append_lines
set_frontmatter_list = _frontmatter.set_list


def read_document(ctx: LintContext, path: Path) -> Document | None:
    try:
        loaded = _frontmatter.load(path)
    except UnicodeDecodeError:
        ctx.issue("critical", "Markdown file is not valid UTF-8.", path, check="C1")
        return None
    except OSError as exc:
        if is_permission_denied(exc):
            ctx.issue("critical", permission_denied_message(path, "read file", exc), path, check="C1")
            return None
        ctx.issue("critical", f"Could not read file: {exc}", path, check="C1")
        return None
    except _frontmatter.FrontmatterError as exc:
        if exc.kind == "unterminated":
            ctx.issue("critical", "Markdown file has unterminated YAML frontmatter.", path, check="C1")
        else:
            ctx.issue("critical", "Markdown file is missing YAML frontmatter.", path, check="C1")
        return None
    return Document(
        path=path,
        frontmatter=loaded.fields,
        body=loaded.body,
        newline=loaded.newline,
        bom=loaded.bom,
        raw_text=loaded.text,
    )


def content_markdown_files(root: Path) -> list[Path]:
    return [
        path
        for path in markdown_files(root)
        if path.name != "_index.md" and path.name != "config.md"
    ]


def load_documents(ctx: LintContext) -> None:
    ctx.documents = {}
    for path in content_markdown_files(ctx.root):
        if not is_schema_checked_path(ctx.root, path):
            continue
        doc = read_document(ctx, path)
        if doc is not None:
            ctx.documents[path.resolve()] = doc


def is_schema_checked_path(root: Path, path: Path) -> bool:
    try:
        rel = path.resolve().relative_to(root)
    except ValueError:
        return False
    parts = rel.parts
    if not parts:
        return False
    return parts[0] in {"raw", "wiki", "inventory", "datasets"}


def require_fields(ctx: LintContext, doc: Document, fields: list[str]) -> None:
    for field in fields:
        value = doc.frontmatter.get(field)
        if value in (None, "", []):
            ctx.issue("critical", f"Required frontmatter field is missing or empty: {field}.", doc.path, check="C2")


def check_enum(
    ctx: LintContext,
    doc: Document,
    field: str,
    allowed: set[str],
    severity: str = "critical",
) -> None:
    value = doc.frontmatter.get(field)
    if value in (None, ""):
        return
    if str(value) not in allowed:
        expected = ", ".join(sorted(allowed))
        ctx.issue(severity, f"Invalid {field}: {value!r}; expected one of {expected}.", doc.path, check="C2")


def ensure_dir_index(ctx: LintContext, directory: Path) -> None:
    if not directory.exists():
        if ctx.fix_allowed("create", directory, "create directory", check="C1"):
            directory.mkdir(parents=True, exist_ok=True)
            ctx.fixed(f"Created directory {ctx.rel(directory)}.")
        else:
            ctx.issue(
                "critical", "Required directory is missing.", directory,
                fixable=True, check="C1", fix_class="create",
            )
            return
    index = directory / "_index.md"
    if not index.exists():
        if ctx.fix_allowed("create", index, "write a generated index", check="C1"):
            regenerate_directory_index(ctx, directory)
        else:
            ctx.issue(
                "critical", "Required _index.md is missing.", index,
                fixable=True, check="C1", fix_class="create",
            )


# The directories every wiki carries, each with a generated ``_index.md``.
REQUIRED_INDEX_DIRECTORIES = (
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
)


def optional_index_directories(root: Path) -> list[Path]:
    """The index-carrying directories of the optional layers that exist, in check order.

    ``inventory/`` and its record directories, ``datasets/`` and, per dataset
    manifest, the dataset directory and its ``samples``, ``profiles`` and
    ``queries`` directories. Shared by the structure check and ``index``.
    """
    found: list[Path] = []
    if (root / "inventory").exists():
        found.append(root / "inventory")
        for name in ("items", "ideas", "candidates", "entities", "corpora", "views"):
            path = root / "inventory" / name
            if path.exists():
                found.append(path)
    if (root / "datasets").exists():
        found.append(root / "datasets")
        for manifest in sorted((root / "datasets").glob("*/MANIFEST.md")):
            dataset_dir = manifest.parent
            found.append(dataset_dir)
            for name in ("samples", "profiles", "queries"):
                path = dataset_dir / name
                if path.exists():
                    found.append(path)
    return found


def check_structure(ctx: LintContext) -> None:
    if not ctx.root.exists():
        ctx.issue("critical", "Wiki root does not exist.", ctx.root, check="C1")
        return
    if not (ctx.root / "_index.md").exists():
        # No repair writes the master index (it is the curated entry point),
        # so the finding is not fixable (knowmade#111).
        ctx.issue("critical", "Master _index.md is missing.", ctx.root / "_index.md", check="C1")
    if not (ctx.root / "config.md").exists():
        ctx.issue("critical", "config.md is missing.", ctx.root / "config.md", check="C1")
    ensure_topic_schema(ctx)

    for rel in REQUIRED_INDEX_DIRECTORIES:
        ensure_dir_index(ctx, ctx.root / rel)
    for directory in optional_index_directories(ctx.root):
        ensure_dir_index(ctx, directory)

    inbox = ctx.root / "inbox"
    if not inbox.exists():
        if ctx.fix_allowed("create", inbox, "create inbox/ and inbox/.processed/", check="C1"):
            inbox.mkdir(parents=True, exist_ok=True)
            (inbox / ".processed").mkdir(exist_ok=True)
            ctx.fixed("Created inbox/ and inbox/.processed/.")
        else:
            ctx.issue(
                "warning", "inbox/ is missing.", inbox, fixable=True, check="C1", fix_class="create"
            )


def ensure_topic_schema(ctx: LintContext) -> None:
    """Nudge or create the default topic guide.

    Topic guides are default from v0.15 onward, but existing wikis should not
    become structurally broken merely because they have not adopted one yet.
    Missing schema.md is therefore an info-level adoption prompt during
    read-only lint and an additive file creation during lint --fix.
    """
    schema = ctx.root / "schema.md"
    if schema.exists():
        return
    if ctx.fix_allowed("create", schema, "write the starter topic guide", check="C1"):
        schema.write_bytes(default_schema_text(ctx.root).encode("utf-8"))
        ctx.fixed(f"Created starter topic guide at {ctx.rel(schema)}.")
    else:
        ctx.issue(
            "info",
            "Optional topic guide missing. Existing wiki is valid. Run `llm-wiki schema adopt` or `llm-wiki lint --fix` to add a starter `schema.md`.",
            schema,
            fixable=True,
            check="C1",
            fix_class="create",
        )


def check_frontmatter_schema(ctx: LintContext) -> None:
    for doc in sorted(ctx.documents.values(), key=lambda item: str(item.path)):
        rel = doc.path.resolve().relative_to(ctx.root)
        parts = rel.parts
        if not parts:
            continue
        if parts[0] == "raw":
            require_fields(ctx, doc, ["title", "source", "type", "ingested", "tags", "summary"])
            check_enum(ctx, doc, "type", RAW_TYPES)
        elif parts[0] == "wiki":
            if doc.frontmatter.get("type") == "thesis":
                require_fields(ctx, doc, ["title", "created", "updated", "tags", "summary"])
            else:
                require_fields(ctx, doc, ["title", "category", "created", "updated", "tags", "summary"])
                check_enum(ctx, doc, "category", ARTICLE_CATEGORIES)
            if "confidence" in doc.frontmatter:
                check_enum(ctx, doc, "confidence", CONFIDENCE_VALUES, severity="warning")
            if "volatility" in doc.frontmatter:
                check_enum(ctx, doc, "volatility", VOLATILITY_VALUES, severity="warning")
        elif parts[0] == "inventory":
            if len(parts) >= 2 and parts[1] == "views":
                require_fields(ctx, doc, ["title", "view", "updated", "summary"])
            else:
                require_fields(
                    ctx,
                    doc,
                    ["title", "kind", "status", "priority", "created", "updated", "tags", "summary"],
                )
                check_enum(ctx, doc, "kind", INVENTORY_KINDS)
                check_enum(ctx, doc, "status", INVENTORY_STATUSES)
                check_enum(ctx, doc, "priority", INVENTORY_PRIORITIES)
        elif parts[0] == "datasets" and doc.path.name == "MANIFEST.md":
            require_fields(
                ctx,
                doc,
                [
                    "title",
                    "dataset_id",
                    "status",
                    "storage",
                    "locations",
                    "formats",
                    "schema_status",
                    "created",
                    "updated",
                    "tags",
                    "summary",
                ],
            )
            check_enum(ctx, doc, "status", DATASET_STATUSES)
            check_enum(ctx, doc, "storage", DATASET_STORAGE)
            check_enum(ctx, doc, "schema_status", SCHEMA_STATUSES)

        tags = doc.frontmatter.get("tags")
        if "tags" in doc.frontmatter and (not isinstance(tags, list) or not tags):
            ctx.issue("warning", "tags must be a non-empty list.", doc.path, check="C2")


# --- C13: legacy frontmatter aliases -----------------------------------------
# The alias tables of references/linting.md ("Alias tables (C13)"): append-only,
# never remove an entry. An entry lands here and in that reference together.
#   KEY_ALIASES    old_key -> canonical_key
#   VALUE_ALIASES  canonical_key -> {old_value: canonical_value}
#   VALUE_ALIAS_LAYERS  canonical_key -> the top-level directories its value
#                  aliases apply to. Every value alias names its layers; a field
#                  of VALUE_ALIASES with no entry here applies nowhere and lint
#                  reports it once as a critical engine problem. `category` is a
#                  wiki article field, but inventory items carry a free-form
#                  `category` of their own, so it is scoped to wiki/.
KEY_ALIASES: dict[str, str] = {"compiled_from": "compiled-from"}
VALUE_ALIASES: dict[str, dict[str, str]] = {"category": {"references": "reference"}}
VALUE_ALIAS_LAYERS: dict[str, frozenset[str]] = {"category": frozenset({"wiki"})}


def alias_plan(
    fields: dict[str, object], layer: str | None = None
) -> tuple[dict[str, str], dict[str, tuple[str, str]], list[str]]:
    """What the C13 tables rewrite in one frontmatter: ``(keys, values, conflicts)``.

    ``keys`` maps each legacy key present to its canonical key; ``values`` maps
    a canonical key to the ``(old, new)`` value pair its scalar carries.
    ``conflicts`` names a legacy key whose canonical key is also present: two
    values would collide, which only a human can settle, so it is reported and
    nothing in that frontmatter is rewritten, keys and values alike.
    ``layer`` is the document's top-level directory; a value alias applies only
    in the layers ``VALUE_ALIAS_LAYERS`` names for its field, nowhere when the
    field has no entry.
    """
    keys: dict[str, str] = {}
    values: dict[str, tuple[str, str]] = {}
    conflicts: list[str] = []
    for key, value in fields.items():
        canonical = KEY_ALIASES.get(key, key)
        if canonical != key:
            if canonical in fields:
                conflicts.append(key)
                continue
            keys[key] = canonical
        table = VALUE_ALIASES.get(canonical)
        if layer not in VALUE_ALIAS_LAYERS.get(canonical, frozenset()):
            continue
        if table and isinstance(value, str) and value in table:
            values[canonical] = (value, table[value])
    if conflicts:
        return {}, {}, conflicts
    return keys, values, conflicts


def apply_aliases_to_block(
    block: str, keys: dict[str, str], values: dict[str, tuple[str, str]]
) -> str:
    """``block`` with the keys renamed and then the values rewritten, line by line.

    Only the top-level ``key:`` line is touched; everything else, comments and
    quoting of other fields included, stays byte for byte.
    """
    for old, new in keys.items():
        block = re.sub(rf"(?m)^{re.escape(old)}(\s*:)", lambda match, new=new: new + match.group(1), block)
    for field, (old, new) in values.items():
        block = re.sub(
            rf"(?m)^({re.escape(field)}\s*:[ \t]*)([\"']?){re.escape(old)}\2[ \t]*$",
            lambda match, new=new: match.group(1) + new,
            block,
        )
    return block


def fix_frontmatter_aliases(ctx: LintContext) -> None:
    """Apply the C13 alias tables to ``raw/``, ``wiki/``, ``inventory/`` and ``datasets/``."""
    if not KEY_ALIASES and not VALUE_ALIASES:
        return
    for field in sorted(set(VALUE_ALIASES) - set(VALUE_ALIAS_LAYERS)):
        ctx.issue(
            "critical",
            f"Engine problem: VALUE_ALIASES has an entry for {field} but VALUE_ALIAS_LAYERS names no layer "
            "for it, so its value aliases apply nowhere.",
            check="C13",
        )
    changed = False
    for doc in sorted(ctx.documents.values(), key=lambda item: str(item.path)):
        keys, values, conflicts = alias_plan(doc.frontmatter, ctx.rel(doc.path).split("/")[0])
        for key in conflicts:
            ctx.issue(
                "warning",
                f"Legacy frontmatter key {key} and its canonical key {KEY_ALIASES[key]} are both present; "
                "merge them by hand.",
                doc.path,
                check="C13",
            )
        if not keys and not values:
            continue
        renamed = [f"key {old} -> {new}" for old, new in keys.items()] + [
            f"{field}: {old} -> {new}" for field, (old, new) in values.items()
        ]
        action = "rewrite legacy frontmatter keys and values to canonical"
        if not ctx.fix_allowed("frontmatter", doc.path, action, check="C13"):
            ctx.issue(
                "warning",
                f"Legacy frontmatter ({'; '.join(renamed)}).",
                doc.path,
                fixable=True,
                check="C13",
                fix_class="frontmatter",
            )
            continue
        try:
            _frontmatter.update(doc.path, lambda block: apply_aliases_to_block(block, keys, values))
        except _frontmatter.FrontmatterError:
            continue
        ctx.fixed(f"Rewrote legacy frontmatter in {ctx.rel(doc.path)} ({'; '.join(renamed)}).")
        changed = True
    if changed:
        load_documents(ctx)


def fix_legacy_wiki_frontmatter(ctx: LintContext) -> None:
    if not ctx.fixing:
        return
    changed = False
    today = today_date().isoformat()
    for doc in sorted(ctx.documents.values(), key=lambda item: str(item.path)):
        try:
            rel = doc.path.resolve().relative_to(ctx.root)
        except ValueError:
            continue
        if len(rel.parts) < 3 or rel.parts[0] != "wiki":
            continue
        try:
            loaded = _frontmatter.load(doc.path)
        except _frontmatter.FrontmatterError:
            continue
        frontmatter, body = loaded.block, loaded.body
        bucket = rel.parts[1]
        is_thesis = doc.frontmatter.get("type") == "thesis" or bucket == "theses"

        if not is_thesis and bucket in {"concepts", "topics", "references"} and not frontmatter_has_value(
            frontmatter, "category"
        ):
            frontmatter = insert_frontmatter_after_title(
                frontmatter,
                [f"category: {bucket[:-1] if bucket.endswith('s') else bucket}"],
            )

        if not frontmatter_has_value(frontmatter, "summary"):
            frontmatter = insert_frontmatter_after_title(
                frontmatter,
                [f"summary: {yaml_quote(first_body_summary(body))}"],
            )

        if not frontmatter_has_value(frontmatter, "tags"):
            tag = "thesis" if is_thesis else bucket[:-1] if bucket.endswith("s") else "wiki"
            frontmatter = append_frontmatter_lines(frontmatter, [f"tags: [{tag}]"])

        if not frontmatter_has_value(frontmatter, "created"):
            created = (
                frontmatter_field_value(frontmatter, "updated")
                or frontmatter_field_value(frontmatter, "verified")
                or today
            )
            frontmatter = append_frontmatter_lines(frontmatter, [f"created: {created}"])

        if not frontmatter_has_value(frontmatter, "updated"):
            updated = (
                frontmatter_field_value(frontmatter, "created")
                or frontmatter_field_value(frontmatter, "verified")
                or today
            )
            frontmatter = append_frontmatter_lines(frontmatter, [f"updated: {updated}"])

        if not frontmatter_has_value(frontmatter, "volatility"):
            frontmatter = append_frontmatter_lines(frontmatter, ["volatility: warm"])

        if frontmatter == loaded.block or not ctx.fix_allowed(
            "frontmatter", doc.path, "infer missing article frontmatter", check="C2"
        ):
            continue
        if loaded.rewrite(frontmatter):
            ctx.fixed(f"Repaired frontmatter in {ctx.rel(doc.path)}.")
            changed = True

    if changed:
        load_documents(ctx)


def canonical_path_for(root: Path, doc: Document) -> Path | None:
    rel = doc.path.resolve().relative_to(root)
    parts = rel.parts
    if not parts:
        return None
    fm = doc.frontmatter
    # Enum fields may parse as lists (``category: [concept]``); the schema
    # check reports those, and they are unhashable, so only strings are
    # matched against the placement tables here.
    if parts[0] == "raw":
        source_type = fm.get("type")
        if isinstance(source_type, str) and source_type in RAW_TYPES:
            return root / "raw" / source_type / doc.path.name
    if parts[0] == "wiki":
        if fm.get("type") == "thesis":
            return root / "wiki" / "theses" / doc.path.name
        category = fm.get("category")
        if isinstance(category, str) and category in ARTICLE_DIRS:
            return root / "wiki" / ARTICLE_DIRS[category] / doc.path.name
    if parts[0] == "inventory" and len(parts) >= 2 and parts[1] != "views":
        kind = fm.get("kind")
        if not isinstance(kind, str):
            return None
        if kind == "item":
            return root / "inventory" / "items" / doc.path.name
        if kind == "idea":
            return root / "inventory" / "ideas" / doc.path.name
        if kind == "entity":
            return root / "inventory" / "entities" / doc.path.name
        if kind == "corpus":
            return root / "inventory" / "corpora" / doc.path.name
        if kind in {"ingest-candidate", "question", "task", "artifact", "watch"}:
            return root / "inventory" / "candidates" / doc.path.name
    return None


def check_canonical_placement(ctx: LintContext) -> None:
    moved = False
    for doc in list(sorted(ctx.documents.values(), key=lambda item: str(item.path))):
        try:
            rel = doc.path.resolve().relative_to(ctx.root)
        except ValueError:
            continue
        if rel.parts[0] not in {"raw", "wiki", "inventory"}:
            continue
        expected = canonical_path_for(ctx.root, doc)
        if expected is None or expected.resolve() == doc.path.resolve():
            continue
        conflict = expected.exists()
        if conflict and ctx.applies("placement"):
            ctx.issue(
                "critical",
                f"File belongs at {ctx.rel(expected)}, but destination already exists.",
                doc.path,
                check="C11",
            )
            continue
        if not conflict and ctx.fix_allowed(
            "placement", doc.path, f"move to {ctx.rel(expected)}", check="C11"
        ):
            expected.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(doc.path), str(expected))
            ctx.issue(
                "critical",
                f"Moved misplaced file to {ctx.rel(expected)}.",
                doc.path,
                fixed=True,
                check="C11",
            )
            ctx.fixed(f"Moved {ctx.rel(doc.path)} to {ctx.rel(expected)}.")
            moved = True
        else:
            ctx.issue(
                "critical",
                f"File is in the wrong directory; expected {ctx.rel(expected)}.",
                doc.path,
                fixable=True,
                check="C11",
                fix_class="placement",
            )

    for manifest in sorted((ctx.root / "datasets").glob("*/MANIFEST.md")):
        doc = ctx.documents.get(manifest.resolve())
        if not doc:
            continue
        dataset_id = doc.frontmatter.get("dataset_id")
        if dataset_id and dataset_id != manifest.parent.name:
            ctx.issue(
                "warning",
                f"Dataset manifest directory does not match dataset_id {dataset_id!r}.",
                manifest,
                check="C11",
            )

    if moved:
        load_documents(ctx)


def check_unknown_files(ctx: LintContext) -> None:
    if not ctx.root.exists():
        return
    hub_root = is_hub(ctx.root)
    root_allowed = HUB_ALLOWED if hub_root else ROOT_ALLOWED
    if (ctx.root / ".git").exists():
        root_allowed = root_allowed | GIT_ROOT_META_ALLOWED
    root_allowed = root_allowed | configured_root_allowlist(ctx.root)
    if not hub_root and (ctx.root / "config.md").is_file():
        # A hub topic keeps its source store inside the topic (knowmade#89):
        # `.wiki-local/` and `sources/` there are lifecycle data, not strays.
        try:
            root_allowed = root_allowed | _source_lifecycle.store_root_entries(
                _source_lifecycle.load_config(ctx.root)
            )
        except _source_lifecycle.LifecycleError:
            pass  # check_source_lifecycle_pointers reports the invalid config
    for child in sorted(ctx.root.iterdir()):
        if child.name not in root_allowed:
            handle_unknown(ctx, child, "Unexpected file or directory at wiki root.")

    scan_fixed_allowed(ctx, ctx.root / "raw", RAW_ALLOWED, "raw/")
    scan_fixed_allowed(ctx, ctx.root / "wiki", WIKI_ALLOWED, "wiki/")
    scan_fixed_allowed(ctx, ctx.root / "inventory", INVENTORY_ALLOWED, "inventory/")

    datasets = ctx.root / "datasets"
    if datasets.exists():
        for child in sorted(datasets.iterdir()):
            if child.name == "_index.md":
                continue
            if child.is_dir():
                for dataset_child in sorted(child.iterdir()):
                    if dataset_child.name not in DATASET_CHILD_ALLOWED:
                        handle_unknown(
                            ctx,
                            dataset_child,
                            f"Unexpected file or directory in datasets/{child.name}/.",
                        )
                for subdir_name in ["samples", "profiles", "queries"]:
                    subdir = child / subdir_name
                    if subdir.exists():
                        for note in sorted(subdir.iterdir()):
                            if note.name == "_index.md":
                                continue
                            if not note.is_file() or note.suffix != ".md":
                                handle_unknown(
                                    ctx,
                                    note,
                                    f"Unexpected file in datasets/{child.name}/{subdir_name}/.",
                                )
            else:
                handle_unknown(ctx, child, "Unexpected file in datasets/.")

    for base, allowed_dirs in [
        (ctx.root / "raw", {"articles", "papers", "repos", "notes", "data"}),
        (ctx.root / "wiki", {"concepts", "topics", "references", "theses"}),
        (
            ctx.root / "inventory",
            {"items", "ideas", "candidates", "entities", "corpora", "views"},
        ),
    ]:
        if not base.exists():
            continue
        for subdir in allowed_dirs:
            path = base / subdir
            if not path.exists():
                continue
            for child in sorted(path.iterdir()):
                if child.name == "_index.md":
                    continue
                if not child.is_file() or child.suffix != ".md":
                    handle_unknown(ctx, child, f"Unexpected file in {ctx.rel(path)}/.")


def configured_root_allowlist(root: Path) -> set[str]:
    """Names ``config.md`` widens the root allowlist with (``lint_allow_root``).

    Opt-in and root-level only: a name must be a plain file or directory name,
    never a layout directory (``raw/``, ``wiki/``, ``inventory/`` keep their own
    allowlists) and never a ``*-PRIVATE`` name, so a corpus cannot declare a
    private sibling ordinary content.
    """
    fields = _rules.config_fields(root)
    value = fields.get(LINT_ALLOW_ROOT_KEY) if fields else None
    names = value if isinstance(value, list) else [value] if isinstance(value, str) else []
    allowed: set[str] = set()
    for name in names:
        if not isinstance(name, str):
            continue
        name = name.strip()
        if (
            not name
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
            or name in LINT_ALLOW_ROOT_FORBIDDEN
            or name.endswith(PRIVATE_SUFFIX)
        ):
            continue
        allowed.add(name)
    return allowed


def is_os_metadata(name: str) -> bool:
    """True for ``.DS_Store``, ``Thumbs.db``, ``desktop.ini`` and ``._*`` files."""
    lowered = name.lower()
    return lowered in OS_METADATA_NAMES or lowered.startswith(APPLEDOUBLE_PREFIX)


def scan_fixed_allowed(ctx: LintContext, directory: Path, allowed: set[str], label: str) -> None:
    if not directory.exists():
        return
    for child in sorted(directory.iterdir()):
        if child.name not in allowed:
            handle_unknown(ctx, child, f"Unexpected file or directory in {label}.")


def handle_unknown(ctx: LintContext, path: Path, message: str) -> None:
    if is_os_metadata(path.name):
        return
    if path.is_dir():
        ctx.issue("warning", f"{message} Unknown directories are not auto-fixed.", path, check="C12")
        return
    unknown = ctx.root / "inbox" / ".unknown"
    if ctx.fix_allowed("quarantine", path, "move to inbox/.unknown/", check="C12"):
        unknown.mkdir(parents=True, exist_ok=True)
        dest = unique_destination(unknown / path.name)
        shutil.move(str(path), str(dest))
        ctx.issue("warning", f"Moved unexpected file to {ctx.rel(dest)}.", path, fixed=True, check="C12")
        ctx.fixed(f"Moved {ctx.rel(path)} to {ctx.rel(dest)}.")
    else:
        ctx.issue("warning", message, path, fixable=True, check="C12", fix_class="quarantine")


def unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(2, 1000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"could not choose unique destination for {path}")


def check_index_consistency(ctx: LintContext) -> None:
    for directory in sorted(path for path in ctx.root.rglob("*") if path.is_dir()):
        if not is_index_checked_directory(ctx.root, directory):
            continue
        index = directory / "_index.md"
        if not index.exists():
            continue
        try:
            text = index.read_text(encoding="utf-8")
        except OSError as exc:
            ctx.issue("warning", f"Could not read index: {exc}", index, check="C3")
            continue
        contract = index_contract(ctx.root, directory)
        missing_entries, dead_links = index_problems(directory, contract, text)
        # What is left after a repair is what the repair cannot fix; before
        # one (no --fix, or the index class held back), all of it is fixable.
        repaired = (missing_entries or dead_links) and ctx.fix_allowed(
            "index", index, "regenerate or repair the index", check="C3"
        )
        if repaired:
            dead_links = repair_directory_index(
                ctx, directory, contract, missing_entries, dead_links
            )
            missing_entries = []
        for path in missing_entries:
            ctx.issue(
                "warning",
                f"Required {index_contract_label(contract)} entry is missing from directory index.",
                path,
                fixable=True,
                check="C3",
                fix_class="index",
            )
        for link in dead_links:
            ctx.issue(
                "warning", f"Index links to missing file: {link}.", index,
                fixable=not repaired, check="C3", fix_class="index",
            )


def check_index_rows(ctx: LintContext) -> None:
    """A generated index row must match its article's frontmatter (C3, #99).

    Runs after the frontmatter repairs, so one ``--fix`` run indexes an article
    with the summary and tags it was just given. ``index-row-stale`` is an info
    from rules/3 on; ``--fix`` rewrites just the stale rows, in place.
    """
    for directory in sorted(path for path in ctx.root.rglob("*") if path.is_dir()):
        index = directory / "_index.md"
        if not is_index_checked_directory(ctx.root, directory) or not index.is_file():
            continue
        try:
            text = index.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rows = stale_index_rows(ctx, directory, index_contract(ctx.root, directory), text)
        if rows and ctx.fix_allowed("index", index, "refresh stale index rows", check="C3"):
            refresh_index_rows(ctx, directory, rows)
        elif rows:
            _rule_lint.issue_versioned(
                ctx, _rules.CURRENT_RULE_SET,
                f"{len(rows)} generated index row(s) differ from the article frontmatter (index-row-stale).",
                index, check="C3", fixable=True, fix_class="index",
            )


def index_problems(directory: Path, contract: str, text: str) -> tuple[list[Path], list[str]]:
    """Return the required entries an index lacks and the dead links it carries."""
    missing_entries = [
        path for path in index_contract_entries(directory, contract)
        if not index_links_to(text, path.relative_to(directory).as_posix())
    ]
    dead_links = [
        link for link in extract_link_destinations(text)
        if is_local_markdown_link(link) and not resolve_link_target(directory, link).exists()
    ]
    return missing_entries, dead_links


def check_source_provenance(ctx: LintContext) -> None:
    for doc in sorted(ctx.documents.values(), key=lambda item: str(item.path)):
        try:
            rel = doc.path.resolve().relative_to(ctx.root)
        except ValueError:
            continue
        if rel.parts[0] == "wiki":
            sources = doc.frontmatter.get("sources")
            compiled_from = doc.frontmatter.get("compiled-from")
            exempt = isinstance(compiled_from, str) and compiled_from in {"conversation", "mixed"}
            if not sources and not exempt:
                ctx.issue(
                    "warning",
                    "Compiled article is missing sources and has no compiled-from exemption.",
                    doc.path,
                    check="C18",
                )
                continue
            if sources:
                if not isinstance(sources, list):
                    ctx.issue("warning", "sources must be a list.", doc.path, check="C4b")
                    continue
                for source in sources:
                    resolved = resolve_source_ref(ctx, doc.path, str(source), wiki_source=True)
                    if resolved is None:
                        ctx.issue("warning", f"Source reference does not resolve: {source}.", doc.path, check="C4b")
                    elif is_under(resolved, ctx.root / "raw"):
                        ctx.referenced_raw.add(resolved.resolve())
        elif rel.parts[0] == "inventory":
            sources = doc.frontmatter.get("sources")
            if sources and isinstance(sources, list):
                for source in sources:
                    if str(source).startswith(("http://", "https://")):
                        continue
                    resolved = resolve_source_ref(ctx, doc.path, str(source), wiki_source=False)
                    if resolved is None:
                        ctx.issue(
                            "warning",
                            f"Inventory source reference does not resolve: {source}.",
                            doc.path,
                            check="C4b",
                        )
        elif rel.parts[0] == "raw":
            source = doc.frontmatter.get("source")
            if source:
                candidate = raw_source_path_candidate(ctx, doc.path, str(source))
                if candidate is not None and not source_path_exists(candidate):
                    ctx.issue(
                        "warning",
                        f"Raw source reference does not resolve: {source}.",
                        doc.path,
                        check="C4b",
                    )

        if "RETRACTED-SOURCE" in doc.body:
            ctx.issue("warning", "Retracted-source marker remains in the file body.", doc.path, check="C4b")


def fix_source_references(ctx: LintContext) -> None:
    if not ctx.fixing:
        return
    changed = False
    for doc in sorted(ctx.documents.values(), key=lambda item: str(item.path)):
        try:
            rel = doc.path.resolve().relative_to(ctx.root)
        except ValueError:
            continue
        if rel.parts[0] != "wiki":
            continue
        sources = doc.frontmatter.get("sources")
        if not isinstance(sources, list) or not sources:
            continue
        new_sources: list[str] = []
        doc_changed = False
        for source in sources:
            source_text = str(source)
            resolved = resolve_source_ref(ctx, doc.path, source_text, wiki_source=True)
            if resolved is not None and is_under(resolved, ctx.root / "raw"):
                canonical = ctx.rel(resolved)
                new_sources.append(canonical)
                if canonical != strip_matching_quotes(source_text.strip()):
                    doc_changed = True
            else:
                new_sources.append(source_text)
        if not doc_changed or not ctx.fix_allowed(
            "frontmatter", doc.path, "rewrite source refs to their raw/ paths", check="C4b"
        ):
            continue
        try:
            _frontmatter.update(
                doc.path, lambda block: _frontmatter.set_list(block, "sources", new_sources)
            )
        except _frontmatter.FrontmatterError:
            continue
        ctx.fixed(f"Rewrote source refs in {ctx.rel(doc.path)}.")
        changed = True
    if changed:
        load_documents(ctx)


def resolve_source_ref(ctx: LintContext, owner: Path, ref: str, wiki_source: bool) -> Path | None:
    ref = strip_matching_quotes(ref.strip())
    if ref.startswith(("http://", "https://")):
        return None if wiki_source else Path(ref)
    candidates: list[Path] = []
    windows_ref = windows_absolute_path(ref)
    ref_path = windows_ref or Path(ref)
    if windows_ref is not None or ref_path.is_absolute():
        candidates.append(ref_path)
    elif ref.startswith(("../", "./")):
        candidates.append((owner.parent / ref).resolve())
    else:
        candidates.append((ctx.root / ref).resolve())
        # See wiki_relative_source_path: a project-local `.wiki/` also resolves
        # against the repository that contains it.
        if ctx.root.name == ".wiki":
            candidates.append((ctx.root.parent / ref).resolve())
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()

    if wiki_source:
        slug = slugify(Path(ref).stem)
        exact_matches: list[Path] = []
        contains_matches: list[Path] = []
        for raw_file in content_markdown_files(ctx.root / "raw"):
            raw_slug = slugify(raw_file.stem)
            raw_slug_without_date = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", raw_slug)
            if slug in {raw_slug, raw_slug_without_date}:
                exact_matches.append(raw_file.resolve())
            elif len(slug) >= 8 and (
                slug in raw_slug_without_date or raw_slug_without_date in slug
            ):
                contains_matches.append(raw_file.resolve())
        if len(exact_matches) == 1:
            return exact_matches[0]
        if len(exact_matches) > 1:
            ctx.issue("warning", f"Source reference is ambiguous after slug fallback: {ref}.", owner, check="C4b")
            return None
        if len(contains_matches) == 1:
            return contains_matches[0]
        if len(contains_matches) > 1:
            ctx.issue("warning", f"Source reference is ambiguous after fuzzy slug fallback: {ref}.", owner, check="C4b")
            return None
    return None


def raw_source_path_candidate(ctx: LintContext, owner: Path, ref: str) -> Path | None:
    """Return a candidate only when a raw source value is clearly a local path.

    Raw provenance also uses sentinels such as ``session`` and descriptive
    labels. Treating every non-HTTP value as a path would make those established
    values fail lint. URI schemes remain external except for ``file://``; bare
    relative paths must contain a separator and no whitespace to be considered
    unambiguous.
    """
    ref = strip_matching_quotes(ref.strip())
    if not ref:
        return None

    windows_ref = windows_absolute_path(ref)
    if windows_ref is not None:
        return windows_ref

    try:
        parsed = urllib.parse.urlsplit(ref)
    except ValueError:
        parsed = None
    if parsed is not None and parsed.scheme:
        # Do not mistake a Windows drive letter for a URI scheme on a platform
        # where Windows paths are not native.
        if WINDOWS_ABSOLUTE_PATH_RE.match(ref):
            return Path(ref)
        if parsed.scheme.lower() != "file":
            return None
        path_text = urllib.parse.unquote(parsed.path)
        netloc = parsed.netloc
        # file://C:/dir is malformed - the canonical form is file:///C:/dir - but
        # it is what tools and humans write, and urlsplit reads the drive letter
        # as a host. Put it back on the path rather than building a UNC path.
        if re.match(r"^[A-Za-z]:$", netloc):
            path_text = f"{netloc}{path_text}"
            netloc = ""
        if netloc and netloc.lower() != "localhost":
            path_text = f"//{netloc}{path_text}"
        if os.name == "nt" and re.match(r"^/[A-Za-z]:/", path_text):
            path_text = path_text[1:]
        file_path = Path(path_text)
        return file_path if file_path.is_absolute() else ctx.root / file_path

    if SCHEMELESS_WEB_SOURCE_RE.match(ref):
        return None

    explicit_relative = ref.startswith(("../", "./", "..\\", ".\\"))
    home_relative = ref == "~" or ref.startswith(("~/", "~\\"))
    absolute = Path(ref).is_absolute()
    unambiguous_bare_relative = (
        not any(char.isspace() for char in ref) and ("/" in ref or "\\" in ref)
    )
    if not (explicit_relative or home_relative or absolute or unambiguous_bare_relative):
        return None

    ref_path = expand_leading_tilde(ref)
    if ref_path.is_absolute():
        return ref_path
    if explicit_relative:
        return owner.parent / ref_path
    return wiki_relative_source_path(ctx, ref_path)


def wiki_relative_source_path(ctx: LintContext, ref_path: Path) -> Path:
    """Resolve a root-relative source path against the wiki root.

    A project-local `.wiki/` sits inside its repository, and its raw sources
    are naturally written relative to the repo root (`docs/design.md`), not to
    the wiki root. Prefer the enclosing directory when the reference resolves
    there and not inside the wiki.
    """
    inside_wiki = ctx.root / ref_path
    if ctx.root.name == ".wiki" and not source_path_exists(inside_wiki):
        beside_wiki = ctx.root.parent / ref_path
        if source_path_exists(beside_wiki):
            return beside_wiki
    return inside_wiki


def source_path_exists(path: Path) -> bool:
    """Check a source file or directory without crashing on sandbox denials."""
    try:
        return path.exists()
    except OSError:
        return False


def check_tags(ctx: LintContext) -> None:
    tag_locations: dict[str, list[Path]] = {}
    for doc in ctx.documents.values():
        tags = doc.frontmatter.get("tags")
        if isinstance(tags, list):
            for tag in tags:
                tag_locations.setdefault(str(tag), []).append(doc.path)

    alias_groups = [
        {"ml", "machine-learning", "machinelearning"},
        {"ai", "artificial-intelligence", "artificialintelligence"},
        {"llm", "llms", "large-language-models"},
    ]
    present = set(tag_locations)
    for group in alias_groups:
        matches = sorted(group & present)
        if len(matches) > 1:
            ctx.issue("warning", f"Near-duplicate tags found: {', '.join(matches)}.", check="C5")
    spellings: dict[str, set[str]] = {}
    for tag in present:
        spellings.setdefault(tag_key(tag), set()).add(tag)
    for key in sorted(spellings):
        if len(spellings[key]) > 1:
            listed = ", ".join(f"{tag} ({len(tag_locations[tag])})" for tag in sorted(spellings[key]))
            agent_pass_issue(
                ctx,
                f"Tags differ only in case, separators or a plural s: {listed}; pick one (tag-near-duplicate).",
                None,
                check="C5",
            )


def tag_key(tag: str) -> str:
    """A tag folded for near-duplicate detection: case, ``_``/space/``-`` and one trailing ``s`` ignored."""
    key = re.sub(r"[\s_-]+", "", tag.strip().lower())
    return key[:-1] if len(key) > 3 and key.endswith("s") and not key.endswith("ss") else key


def underlying_source_of(ctx: LintContext, raw_file: Path) -> Path | None:
    """Resolve the external file a raw/ pointer record stands for."""
    doc = ctx.documents.get(raw_file.resolve())
    if doc is None:
        return None
    source = doc.frontmatter.get("source")
    if not source:
        return None
    text = str(source).strip()
    if not text or text == "MANUAL" or text.startswith(("http://", "https://")):
        return None
    try:
        return (raw_file.parent / text).resolve()
    except (OSError, ValueError):
        return None


def compiled_external_references(ctx: LintContext) -> dict[Path, list[Path]]:
    """Map each externally referenced file to the compiled articles that cite it.

    A pointer record is reported as uncompiled when nothing references the
    *record*. In practice the author usually cited the file the record points
    at, directly, and simply bypassed the pointer. Knowing which article did
    that turns an unactionable finding into an actionable one.
    """
    index: dict[Path, list[Path]] = {}

    def add(target: Path, owner: Path) -> None:
        owners = index.setdefault(target, [])
        if owner not in owners:
            owners.append(owner)

    for doc in ctx.documents.values():
        try:
            rel = doc.path.resolve().relative_to(ctx.root)
        except ValueError:
            continue
        if not rel.parts or rel.parts[0] != "wiki":
            continue

        refs: list[str] = []
        sources = doc.frontmatter.get("sources")
        if isinstance(sources, list):
            refs.extend(str(source) for source in sources)
        refs.extend(extract_link_destinations(document_text(doc)))

        for ref in refs:
            text = ref.strip()
            if not text or text.startswith(("http://", "https://", "#", "mailto:")):
                continue
            text = text.split("#", 1)[0].split("?", 1)[0]
            if not text.endswith((".md", ".json", ".jsonl", ".txt", ".csv", ".yaml", ".yml")):
                continue
            try:
                resolved = (doc.path.parent / text).resolve()
            except (OSError, ValueError):
                continue
            if is_under(resolved, ctx.root):
                continue
            add(resolved, doc.path.resolve())

    return index


def attach_pointer_to_article(ctx: LintContext, article: Path, raw_file: Path) -> str:
    """Add a raw/ pointer to an article's sources: dependency manifest.

    Returns ``attached``, ``unchanged`` (nothing to add, or no frontmatter)
    or ``held`` (the frontmatter class may not write in this run).
    """
    try:
        loaded = _frontmatter.load(article)
    except _frontmatter.FrontmatterError:
        return "unchanged"
    frontmatter = loaded.block
    doc = ctx.documents.get(article.resolve())
    existing = doc.frontmatter.get("sources") if doc else None
    values = [str(value) for value in existing] if isinstance(existing, list) else []
    reference = markdown_relative_link(article.parent, raw_file)
    if reference in values:
        return "unchanged"
    updated = set_frontmatter_list(frontmatter, "sources", [reference, *values])
    if updated == frontmatter:
        return "unchanged"
    if not ctx.fix_allowed("frontmatter", article, f"add {ctx.rel(raw_file)} to sources", check="C6"):
        return "held"
    loaded.rewrite(updated)
    if doc:
        doc.raw_text = None
    return "attached"


def check_coverage(ctx: LintContext) -> None:
    unreferenced: list[Path] = []
    for raw_file in content_markdown_files(ctx.root / "raw"):
        doc = ctx.documents.get(raw_file.resolve())
        if doc and "collection-manifest" in as_string_list(doc.frontmatter.get("tags")):
            continue
        if raw_file.resolve() not in ctx.referenced_raw:
            unreferenced.append(raw_file)
    if not unreferenced:
        return

    external = compiled_external_references(ctx)
    owners: dict[Path, list[Path]] = {}
    for raw_file in unreferenced:
        target = underlying_source_of(ctx, raw_file)
        owners[raw_file] = external.get(target, []) if target else []

    if ctx.fixing:
        outcomes = {
            raw_file: attach_pointer_to_article(ctx, owners[raw_file][0], raw_file)
            for raw_file in unreferenced
            if len(owners[raw_file]) == 1
        }
        attached = [raw_file for raw_file, outcome in outcomes.items() if outcome == "attached"]
        for raw_file in attached:
            ctx.fixed(
                f"Added {ctx.rel(raw_file)} to the sources of "
                f"{ctx.rel(owners[raw_file][0])}."
            )
        if attached:
            load_documents(ctx)
        # A pointer the frontmatter class held back is reported, not listed.
        unreferenced = [raw_file for raw_file in unreferenced if outcomes.get(raw_file) != "attached"]
        remaining = [raw_file for raw_file in unreferenced if outcomes.get(raw_file) != "held"]
        if remaining and ctx.fix_allowed(
            "create",
            ctx.root / "wiki" / "references" / COVERAGE_REFERENCE,
            f"list {len(remaining)} uncompiled raw source(s) in the coverage reference",
            check="C6",
        ):
            create_or_update_coverage_reference(ctx, remaining)
            unreferenced = [raw_file for raw_file in unreferenced if raw_file not in set(remaining)]

    for raw_file in unreferenced:
        candidates = owners[raw_file]
        if len(candidates) == 1:
            ctx.issue(
                "suggestion",
                "Raw source is not referenced by any compiled article, but "
                f"{ctx.rel(candidates[0])} cites its underlying source directly; "
                "add the pointer to that article's sources.",
                raw_file,
                fixable=True,
                check="C6",
                fix_class="frontmatter",
            )
        elif candidates:
            listed = ", ".join(sorted(ctx.rel(candidate) for candidate in candidates))
            ctx.issue(
                "suggestion",
                "Raw source is not referenced by any compiled article; these cite "
                f"its underlying source directly: {listed}.",
                raw_file,
                check="C6",
            )
        else:
            ctx.issue("suggestion", "Raw source is not referenced by any compiled article.", raw_file, check="C6")


COVERAGE_REFERENCE = "uncompiled-source-coverage.md"


def create_or_update_coverage_reference(ctx: LintContext, unreferenced: list[Path]) -> None:
    references_dir = ctx.root / "wiki" / "references"
    references_dir.mkdir(parents=True, exist_ok=True)
    coverage_path = references_dir / COVERAGE_REFERENCE
    existing_sources: list[Path] = []
    existing_doc = ctx.documents.get(coverage_path.resolve())
    if existing_doc and isinstance(existing_doc.frontmatter.get("sources"), list):
        for source in existing_doc.frontmatter["sources"]:
            resolved = resolve_source_ref(ctx, coverage_path, str(source), wiki_source=True)
            if resolved is not None and is_under(resolved, ctx.root / "raw"):
                existing_sources.append(resolved)

    all_sources = sorted({path.resolve() for path in existing_sources + unreferenced})
    today = today_date().isoformat()
    source_refs = [ctx.rel(path) for path in all_sources]
    rows: list[str] = []
    for raw_file in all_sources:
        doc = ctx.documents.get(raw_file.resolve())
        frontmatter = doc.frontmatter if doc else {}
        title = str(frontmatter.get("title") or raw_file.stem)
        summary = str(frontmatter.get("summary") or "")
        tags = ", ".join(as_string_list(frontmatter.get("tags")))
        rel_link = markdown_relative_link(references_dir, raw_file)
        rows.append(
            f"| [{table_cell(title)}]({rel_link}) | `{ctx.rel(raw_file)}` | {table_cell(summary)} | {table_cell(tags)} |"
        )

    text = "\n".join(
        [
            "---",
            'title: "Uncompiled Source Coverage"',
            "category: reference",
            "sources:",
            *[f"  - {source_ref}" for source_ref in source_refs],
            f"created: {today}",
            f"updated: {today}",
            f"verified: {today}",
            "tags: [coverage, uncompiled-sources, backlog, lint-repair]",
            "confidence: low",
            "volatility: warm",
            'summary: "Reference backlog for raw sources that existed in the wiki but were not yet referenced by compiled articles during lint repair."',
            "---",
            "",
            "# Uncompiled Source Coverage",
            "",
            "This reference page makes the remaining raw-source coverage gap explicit. These sources are now discoverable from the compiled wiki layer, but they have not all been fully synthesized into concept or topic articles. Treat this as a follow-up compilation backlog, not as evidence that every listed source has been integrated into surrounding articles.",
            "",
            "## Sources Needing Synthesis",
            "",
            "| Source | Path | Raw Summary | Tags |",
            "|--------|------|-------------|------|",
            *rows,
            "",
            "## Next Action",
            "",
            "Compile these sources selectively into existing articles when their claims materially change the wiki, then remove rows whose content is fully integrated elsewhere.",
            "",
        ]
    )
    write_text_preserving(coverage_path, text)
    ctx.fixed(f"Updated coverage reference {ctx.rel(coverage_path)}.")
    for raw_file in all_sources:
        ctx.referenced_raw.add(raw_file.resolve())
    load_documents(ctx)
    refresh_directory_index(ctx, references_dir)


def refresh_directory_index(ctx: LintContext, directory: Path) -> None:
    """Bring a directory's index up to date after a repair added a file to it.

    A curated index gains the new entry in place; a missing or generated one is
    regenerated. With the index class held back the stale index is reported.
    """
    index = directory / "_index.md"
    contract = index_contract(ctx.root, directory)
    missing_entries: list[Path] = []
    dead_links: list[str] = []
    if index.exists():
        missing_entries, dead_links = index_problems(
            directory, contract, index.read_text(encoding="utf-8")
        )
        if not (missing_entries or dead_links):
            return
    if not ctx.fix_allowed("index", index, "add the repaired file to the index", check="C3"):
        ctx.issue(
            "warning", "Directory index does not list a file lint --fix just wrote.", index,
            fixable=True, check="C3", fix_class="index",
        )
    elif not index.exists():
        regenerate_directory_index(ctx, directory)
    else:
        repair_directory_index(ctx, directory, contract, missing_entries, dead_links)


# The rule set that introduced the deterministic versions of the former agent
# passes (emercons/knowmade#83): info on a rules/3 corpus, absent before.
AGENT_PASS_RULE_SET = 3


def agent_pass_issue(ctx: LintContext, message: str, path: Path | None, *, check: str) -> None:
    """Report one finding of a former agent pass; the message ends with its ``(kind)``."""
    _rule_lint.issue_versioned(ctx, AGENT_PASS_RULE_SET, message, path, check=check)


def check_projects(ctx: LintContext) -> None:
    # ``output/projects/.archive`` holds archived projects; it is not a project.
    for project in _projects.project_folders(ctx.root):
        has_why = _projects.has_why(project)
        if not has_why:
            ctx.issue("warning", "Project is missing a non-empty WHY.md.", project, check="C8")
        if not _projects.valid_slug(project.name):
            ctx.issue("warning", "Project slug should be lowercase, hyphen-separated, <=40 chars, no dates.", project, check="C8")
        if (project / _projects.LEGACY).is_file():
            agent_pass_issue(
                ctx,
                "Legacy _project.md beside WHY.md; WHY.md is authoritative, remove _project.md after review "
                "(project-legacy-manifest)."
                if has_why
                else "Legacy project manifest; `llm-wiki project migrate-why --dry-run` previews the lossless "
                "WHY.md migration, `--apply` writes it (project-legacy-manifest).",
                project / _projects.LEGACY,
                check="C8",
            )
        check_project_staleness(ctx, project)


def check_project_staleness(ctx: LintContext, project: Path) -> None:
    """C8b: a member whose ``sources:`` include a raw source ingested after the member's ``updated:``."""
    for member in markdown_files(project):
        if member.name in {_projects.WHY, _projects.LEGACY, "_index.md"}:
            continue
        try:
            fields = _frontmatter.load(member).fields
        except (OSError, UnicodeDecodeError, _frontmatter.FrontmatterError):
            continue
        updated = parse_date(str(fields.get("updated") or ""))
        if updated is None:
            continue
        newer: list[tuple[dt.date, Path]] = []
        for ref in as_string_list(fields.get("sources")):
            if ref.strip().startswith(("http://", "https://")):
                continue
            target = resolve_source_ref(ctx, member, ref, False)
            raw = ctx.documents.get(target) if target is not None else None
            ingested = parse_date(str(raw.frontmatter.get("ingested") or "")) if raw else None
            if ingested is not None and ingested > updated:
                newer.append((ingested, target))
        if newer:
            date, newest = max(newer)
            agent_pass_issue(
                ctx,
                f"Project member cites {len(newer)} raw source(s) ingested after its updated date "
                f"{updated.isoformat()} (newest: {ctx.rel(newest)}, {date.isoformat()}); review it "
                "(project-stale).",
                member,
                check="C8",
            )


# ``output/`` subdirectories a workflow owns: project folders and collect's
# downloaded media (``output/assets/collect-<slug>/``).
OUTPUT_SANCTIONED_DIRS = {"projects", "assets"}
CANDIDATE_PREFIXES = ("article-", "output-", "report-")
CANDIDATE_NOISE_RE = re.compile(r"\d{4}-\d{2}-\d{2}|(?:^|-)v\d+(?=-|$)|(?:^|-)final(?=-|$)")


def project_candidate_stem(stem: str) -> str:
    """A loose output's name minus dates, ``-vN``, ``-final`` and an ``article-``/``output-``/``report-`` prefix."""
    stem = slugify(stem)
    for prefix in CANDIDATE_PREFIXES:
        if stem.startswith(prefix):
            stem = stem[len(prefix):]
            break
    return re.sub(r"-+", "-", CANDIDATE_NOISE_RE.sub("-", stem)).strip("-")


def check_output_candidates(ctx: LintContext) -> None:
    """C9: loose files and directories in ``output/``, and loose outputs that look like one project."""
    output = ctx.root / "output"
    if not output.is_dir():
        return
    loose: list[Path] = []
    for child in sorted(output.iterdir()):
        if child.name.startswith(".") or is_os_metadata(child.name):
            continue
        if child.is_dir():
            if child.name not in OUTPUT_SANCTIONED_DIRS and any(path.is_file() for path in child.rglob("*")):
                agent_pass_issue(
                    ctx,
                    "Output subdirectory outside projects/ holds files; after review move it into a project "
                    "folder (`wiki project new`) (output-loose-directory).",
                    child,
                    check="C9",
                )
        elif child.suffix.lower() != ".md":
            agent_pass_issue(
                ctx,
                "Loose non-markdown file in output/; assets belong in a project folder beside the markdown "
                "that uses them (output-loose-binary).",
                child,
                check="C9",
            )
        elif child.name != "_index.md":
            loose.append(child)
    groups: dict[str, list[Path]] = {}
    for path in loose:
        stem = project_candidate_stem(path.stem)
        if stem:
            groups.setdefault(stem.split("-", 1)[0], []).append(path)
    for files in groups.values():
        if len(files) < 3:
            continue
        prefix: list[str] = []
        for tokens in zip(*(project_candidate_stem(path.stem).split("-") for path in files)):
            if len(set(tokens)) != 1:
                break
            prefix.append(tokens[0])
        slug = "-".join(prefix)
        names = ", ".join(path.name for path in files)
        agent_pass_issue(
            ctx,
            f"{len(files)} loose outputs share the prefix `{slug}` ({names}); candidate project `{slug}` "
            "(project-candidate)."
            if len(slug) >= 4
            else f"{len(files)} loose outputs share a name start ({names}) but no 4-character prefix; "
            "group them by hand if they belong together (project-candidate).",
            output,
            check="C9",
        )


# Name and title words that mark an output as tracking state (inventory.md
# § Discovery) or as a dataset description (datasets.md § Discovery).
INVENTORY_CANDIDATE_WORDS = frozenset(
    "queue backlog inventory candidate candidates watch watchlist sources corpus dataset parts skus gear assets".split()
)
DATASET_CANDIDATE_WORDS = frozenset(
    "dataset datasets data corpus archive dump warehouse lake parquet sqlite duckdb csv jsonl snapshot".split()
)


def _candidate_words(path: Path) -> set[str]:
    words = set(re.split(r"[^a-z0-9]+", path.stem.lower()))
    try:
        title = _frontmatter.load(path).fields.get("title")
    except (OSError, UnicodeDecodeError, _frontmatter.FrontmatterError):
        title = None
    if isinstance(title, str):
        words |= set(re.split(r"[^a-z0-9]+", title.lower()))
    return words - {""}


def check_migration_candidates(ctx: LintContext) -> None:
    """C16/C17: outputs whose name or title says they are inventory or a dataset description."""
    output = ctx.root / "output"
    for path in markdown_files(output):
        rel = path.relative_to(output).parts
        if path.name == "_index.md" or (rel[0] == "projects" and path.name in {_projects.WHY, _projects.LEGACY}):
            continue
        words = _candidate_words(path)
        rel_path = ctx.rel(path)
        hits = sorted(words & INVENTORY_CANDIDATE_WORDS)
        if hits:
            agent_pass_issue(
                ctx,
                f"Output looks like tracking state ({', '.join(hits)}); review "
                f"`wiki inventory migrate-output {rel_path} --dry-run` (inventory-migration-candidate).",
                path,
                check="C16",
            )
        hits = sorted(words & DATASET_CANDIDATE_WORDS)
        if hits:
            agent_pass_issue(
                ctx,
                f"Output looks like a dataset description ({', '.join(hits)}); review "
                f"`wiki dataset migrate-output {rel_path} --dry-run` (dataset-migration-candidate).",
                path,
                check="C17",
            )


SEE_ALSO_HEADING_RE = re.compile(r"^##\s+see also\s*$", re.I)


def _article_links(ctx: LintContext, articles: dict[Path, Document]) -> dict[Path, tuple[set[Path], set[Path]]]:
    """For each compiled article: ``(articles it links to anywhere, articles under its See Also)``.

    Markdown links resolve relative to the file; ``[[wiki-links]]`` by slug or alias, as C4 resolves them.
    """
    slugs: dict[str, set[Path]] = {}
    for path, doc in articles.items():
        for name in {path.stem, *as_string_list(doc.frontmatter.get("aliases"))}:
            slugs.setdefault(slugify(name), set()).add(path)
    result: dict[Path, tuple[set[Path], set[Path]]] = {}
    for path, doc in articles.items():
        parts = split_markdown_frontmatter(document_text(doc))
        body = strip_code_blocks(parts[1] if parts else document_text(doc))
        everywhere: set[Path] = set()
        see_also: set[Path] = set()
        section: list[str] = []
        inside = False
        for line in body.split("\n"):
            if line.startswith("## "):
                inside = bool(SEE_ALSO_HEADING_RE.match(line))
            elif inside:
                section.append(line)
        for text, found in ((body, everywhere), ("\n".join(section), see_also)):
            for link in extract_link_destinations(text):
                if is_local_markdown_link(link):
                    target = resolve_link_target(doc.path.parent, link).resolve()
                    if target in articles:
                        found.add(target)
            for match in WIKILINK_RE.finditer(text):
                stem = match.group(1).strip().replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
                owners = slugs.get(slugify(stem[:-3] if stem.lower().endswith(".md") else stem), set())
                if len(owners) == 1:
                    found |= owners
        everywhere.discard(path)
        see_also.discard(path)
        result[path] = (everywhere, see_also)
    return result


def check_article_cross_links(ctx: LintContext) -> None:
    """C4: one-way See Also links; C6: articles no other article links to (when there are two or more)."""
    articles = {
        doc.path.resolve(): doc
        for doc in ctx.documents.values()
        if is_under(doc.path.resolve(), ctx.root / "wiki") and doc.path.name != COVERAGE_REFERENCE
    }
    links = _article_links(ctx, articles)
    for source in sorted(links):
        for target in sorted(links[source][1]):
            if source not in links[target][0]:
                agent_pass_issue(
                    ctx,
                    f"{ctx.rel(source)} lists this article under See Also, but it does not link back; "
                    "add the reverse link if it makes sense (see-also-one-way).",
                    target,
                    check="C4",
                )
    if len(articles) < 2:
        return
    linked = set().union(*(everywhere for everywhere, _ in links.values()))
    for path in sorted(set(articles) - linked):
        agent_pass_issue(
            ctx,
            "No other compiled article links to this one; add a See Also link from a related article "
            "(orphan-article).",
            path,
            check="C6",
        )


MASTER_STAT_RE = re.compile(
    r"^\s*[-*]\s+(sources|articles|inventory records|datasets|dataset manifests|outputs):\s*(\d+)\b", re.I | re.M
)


def master_index_counts(root: Path) -> dict[str, int]:
    """The master ``_index.md`` statistics, derived from files (``indexing.md`` § Master Index Statistics).

    Inventory views are derived pages, not records, so ``inventory/views/`` is not counted.
    """
    views = root / "inventory" / "views"
    return {
        "sources": len(content_markdown_files(root / "raw")),
        "articles": len(content_markdown_files(root / "wiki")),
        "inventory records": len([path for path in content_markdown_files(root / "inventory") if not is_under(path, views)]),
        "datasets": len(list((root / "datasets").glob("*/MANIFEST.md"))),
        "outputs": len(content_markdown_files(root / "output")),
    }


def check_index_statistics(ctx: LintContext) -> None:
    """C3: a statistic in the master ``_index.md`` that disagrees with the files."""
    index = ctx.root / "_index.md"
    if not index.is_file():
        return
    text = index.read_text(encoding="utf-8", errors="replace")
    counts: dict[str, int] | None = None
    for match in MASTER_STAT_RE.finditer(text):
        counts = counts or master_index_counts(ctx.root)
        label = match.group(1).lower()
        actual = counts["datasets" if label == "dataset manifests" else label]
        if int(match.group(2)) != actual:
            agent_pass_issue(
                ctx,
                f"Master index says {match.group(1)}: {match.group(2)}, but the wiki has {actual}; update the "
                "statistic (index-stats-drift).",
                index,
                check="C3",
            )


def check_source_lifecycle_pointers(ctx: LintContext) -> None:
    """Validate portable pointers without requiring local originals in a clone."""
    if is_hub(ctx.root) or not (ctx.root / "config.md").is_file():
        return
    try:
        config = _source_lifecycle.load_config(ctx.root)
    except _source_lifecycle.LifecycleError as exc:
        ctx.issue("critical", f"Invalid source_store configuration: {exc}", ctx.root / "config.md", check="C20")
        return
    for warning in _source_lifecycle.config_warnings(config):
        _rule_lint.issue_versioned(
            ctx, _rules.CURRENT_RULE_SET, f"source_store: {warning} (source-store-unknown-key).", ctx.root / "config.md",
            check="C20",
        )
    pointers, foreign = _source_lifecycle.classify_pointer_files(config)
    for pointer in pointers:
        for error in _source_lifecycle.validate_pointer(config, pointer, check_original=False):
            ctx.issue("critical", f"Invalid source lifecycle pointer: {error}", pointer, check="C20")
    # A consumer's legacy pointer is not a broken v1 pointer (knowmade#118).
    for item in foreign:
        _rule_lint.issue_versioned(
            ctx, _rules.CURRENT_RULE_SET,
            f"Foreign file in the source pointers directory ({item['schema']}), not validated; "
            "`source import-pointer` maps it (source-pointer-foreign).",
            Path(item["pointer"]), check="C20",
        )
