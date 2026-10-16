"""The ``lint`` namespace: deterministic structural checks on a wiki root.

Every ``check_*`` over structure, frontmatter, placement, indexes, links,
footnotes, provenance, tags, coverage, freshness, projects, source lifecycle
pointers, the hub registry and private-topic ignore rules, plus the
``--fix`` repairs, the operation log entry and the JSON/text reports. The
lint data model (``Issue``, ``Document``, ``LintContext``) is defined in
``common`` so ``indexing`` and the other domains can share it. Moved verbatim
from ``legacy_cli`` (emercons/knowmade#67 R1.4, ``KM-CLI-001``); the
compatibility module re-exports every public name.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import urllib.parse
from pathlib import Path
from typing import Any

try:
    from . import engine as _engine
    from . import frontmatter as _frontmatter
    from . import fsio as _fsio
    from . import identity as _identity
    from . import rule_lint as _rule_lint
    from . import rules as _rules
    from . import source_lifecycle as _source_lifecycle
    from .common import (
        Document,
        LintContext,
        PRIVATE_SUFFIX,
        add_scope_arguments,
        as_string_list,
        first_body_summary,
        is_under,
        markdown_link_destination,
        parse_date,
        strip_matching_quotes,
        table_cell,
        write_registry,
    )
    from .hub import (
        PRIVATE_IGNORE_RULE,
        append_private_ignore_rule,
        ensure_state_ignore_rules,
        hub_ignores_private_topic,
        is_inside_git_work_tree,
        machine_state_files,
        state_ignore_repair_hint,
    )
    from .indexing import (
        index_contract,
        index_contract_entries,
        extract_link_destinations,
        index_contract_label,
        index_links_to,
        is_index_checked_directory,
        is_local_markdown_link,
        link_path_variants,
        regenerate_directory_index,
        repair_directory_index,
    )
    from .markdown import (
        append_text_preserving,
        default_schema_text,
        parse_frontmatter_block,
        split_markdown_frontmatter,
        sniff_style,
        today_date,
        write_text_preserving,
        yaml_quote,
    )
    from .roots import (
        WINDOWS_ABSOLUTE_PATH_RE,
        expand_leading_tilde,
        initialized_wiki_root,
        is_archived_registry_entry,
        is_hub,
        is_permission_denied,
        permission_denied_message,
        resolve_registry_path,
        resolve_wiki_root_args as resolve_wiki_root,
        windows_absolute_path,
    )
    from .specialists import (
        collect_specialist_issues,
        specialist_library_path,
    )
except ImportError:  # executed directly as a script, not imported as a package
    import engine as _engine
    import frontmatter as _frontmatter
    import fsio as _fsio
    import identity as _identity
    import rule_lint as _rule_lint
    import rules as _rules
    import source_lifecycle as _source_lifecycle
    from common import (
        Document,
        LintContext,
        PRIVATE_SUFFIX,
        add_scope_arguments,
        as_string_list,
        first_body_summary,
        is_under,
        markdown_link_destination,
        parse_date,
        strip_matching_quotes,
        table_cell,
        write_registry,
    )
    from hub import (
        PRIVATE_IGNORE_RULE,
        append_private_ignore_rule,
        ensure_state_ignore_rules,
        hub_ignores_private_topic,
        is_inside_git_work_tree,
        machine_state_files,
        state_ignore_repair_hint,
    )
    from indexing import (
        index_contract,
        index_contract_entries,
        extract_link_destinations,
        index_contract_label,
        index_links_to,
        is_index_checked_directory,
        is_local_markdown_link,
        link_path_variants,
        regenerate_directory_index,
        repair_directory_index,
    )
    from markdown import (
        append_text_preserving,
        default_schema_text,
        parse_frontmatter_block,
        split_markdown_frontmatter,
        sniff_style,
        today_date,
        write_text_preserving,
        yaml_quote,
    )
    from roots import (
        WINDOWS_ABSOLUTE_PATH_RE,
        expand_leading_tilde,
        initialized_wiki_root,
        is_archived_registry_entry,
        is_hub,
        is_permission_denied,
        permission_denied_message,
        resolve_registry_path,
        resolve_wiki_root_args as resolve_wiki_root,
        windows_absolute_path,
    )
    from specialists import (
        collect_specialist_issues,
        specialist_library_path,
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
PRIVATE_IGNORE_PROBE_SLUG = f"knowmade-ignore-probe{PRIVATE_SUFFIX}"
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
# these names stay for ``legacy_cli`` and its compatibility callers.
frontmatter_field_value = _frontmatter.field_value
frontmatter_has_value = _frontmatter.has_value
insert_frontmatter_after_title = _frontmatter.insert_after_title
append_frontmatter_lines = _frontmatter.append_lines
set_frontmatter_list = _frontmatter.set_list


def write_markdown_frontmatter(path: Path, frontmatter: str, body: str) -> None:
    """Rewrite ``path`` from block text and body, keeping its newline style and BOM."""
    newline, bom = sniff_style(path)
    _frontmatter.write_document(path, frontmatter.rstrip(), body, newline=newline, bom=bom)


def read_document(ctx: LintContext, path: Path) -> Document | None:
    try:
        loaded = _frontmatter.load(path)
    except UnicodeDecodeError:
        ctx.issue("critical", "Markdown file is not valid UTF-8.", path)
        return None
    except OSError as exc:
        if is_permission_denied(exc):
            ctx.issue("critical", permission_denied_message(path, "read file", exc), path)
            return None
        ctx.issue("critical", f"Could not read file: {exc}", path)
        return None
    except _frontmatter.FrontmatterError as exc:
        if exc.kind == "unterminated":
            ctx.issue("critical", "Markdown file has unterminated YAML frontmatter.", path)
        else:
            ctx.issue("critical", "Markdown file is missing YAML frontmatter.", path)
        return None
    return Document(
        path=path,
        frontmatter=loaded.fields,
        body=loaded.body,
        newline=loaded.newline,
        bom=loaded.bom,
        raw_text=loaded.text,
    )


def document_text(doc: Document) -> str:
    """Return the file's full text, reading it only if it is not cached.

    ``read_document`` caches the text it parsed. A repair that rewrites the
    file clears ``raw_text``, so this never serves text older than the file.
    """
    if doc.raw_text is None:
        doc.raw_text = doc.path.read_text(encoding="utf-8")
    return doc.raw_text


def markdown_files(root: Path) -> list[Path]:
    try:
        if not root.exists():
            return []
        return sorted(path for path in root.rglob("*.md") if path.is_file())
    except OSError as exc:
        if is_permission_denied(exc):
            raise SystemExit(permission_denied_message(root, "list markdown files under", exc)) from exc
        raise


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
            ctx.issue("critical", f"Required frontmatter field is missing or empty: {field}.", doc.path)


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
        ctx.issue(severity, f"Invalid {field}: {value!r}; expected one of {expected}.", doc.path)


def ensure_dir_index(ctx: LintContext, directory: Path) -> None:
    if not directory.exists():
        if ctx.fix:
            directory.mkdir(parents=True, exist_ok=True)
            ctx.fixed(f"Created directory {ctx.rel(directory)}.")
        else:
            ctx.issue("critical", "Required directory is missing.", directory, fixable=True)
            return
    index = directory / "_index.md"
    if not index.exists():
        if ctx.fix:
            regenerate_directory_index(ctx, directory)
        else:
            ctx.issue("critical", "Required _index.md is missing.", index, fixable=True)


def check_structure(ctx: LintContext) -> None:
    if not ctx.root.exists():
        ctx.issue("critical", "Wiki root does not exist.", ctx.root)
        return
    if is_hub(ctx.root):
        for name in ["wikis.json", "_index.md", "log.md", "topics"]:
            path = ctx.root / name
            if not path.exists():
                ctx.issue("critical", "Required hub path is missing.", path)
        return
    if not (ctx.root / "_index.md").exists():
        ctx.issue("critical", "Master _index.md is missing.", ctx.root / "_index.md", fixable=True)
    if not (ctx.root / "config.md").exists():
        ctx.issue("critical", "config.md is missing.", ctx.root / "config.md")
    ensure_topic_schema(ctx)

    required = [
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
    ]
    for rel in required:
        ensure_dir_index(ctx, ctx.root / rel)

    if (ctx.root / "inventory").exists():
        ensure_dir_index(ctx, ctx.root / "inventory")
        for rel in [
            "inventory/items",
            "inventory/ideas",
            "inventory/candidates",
            "inventory/entities",
            "inventory/corpora",
            "inventory/views",
        ]:
            path = ctx.root / rel
            if path.exists():
                ensure_dir_index(ctx, path)

    if (ctx.root / "datasets").exists():
        ensure_dir_index(ctx, ctx.root / "datasets")

        for manifest in sorted((ctx.root / "datasets").glob("*/MANIFEST.md")):
            dataset_dir = manifest.parent
            ensure_dir_index(ctx, dataset_dir)
            for rel in ["samples", "profiles", "queries"]:
                path = dataset_dir / rel
                if path.exists():
                    ensure_dir_index(ctx, path)

    inbox = ctx.root / "inbox"
    if not inbox.exists():
        if ctx.fix:
            inbox.mkdir(parents=True, exist_ok=True)
            (inbox / ".processed").mkdir(exist_ok=True)
            ctx.fixed("Created inbox/ and inbox/.processed/.")
        else:
            ctx.issue("warning", "inbox/ is missing.", inbox, fixable=True)


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
    if ctx.fix:
        schema.write_bytes(default_schema_text(ctx.root).encode("utf-8"))
        ctx.fixed(f"Created starter topic guide at {ctx.rel(schema)}.")
    else:
        ctx.issue(
            "info",
            "Optional topic guide missing. Existing wiki is valid. Run `llm-wiki schema adopt` or `llm-wiki lint --fix` to add a starter `schema.md`.",
            schema,
            fixable=True,
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
            ctx.issue("warning", "tags must be a non-empty list.", doc.path)


def fix_legacy_wiki_frontmatter(ctx: LintContext) -> None:
    if not ctx.fix:
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
        if ctx.fix:
            expected.parent.mkdir(parents=True, exist_ok=True)
            if expected.exists():
                ctx.issue(
                    "critical",
                    f"File belongs at {ctx.rel(expected)}, but destination already exists.",
                    doc.path,
                )
                continue
            shutil.move(str(doc.path), str(expected))
            ctx.issue(
                "critical",
                f"Moved misplaced file to {ctx.rel(expected)}.",
                doc.path,
                fixed=True,
            )
            ctx.fixed(f"Moved {ctx.rel(doc.path)} to {ctx.rel(expected)}.")
            moved = True
        else:
            ctx.issue(
                "critical",
                f"File is in the wrong directory; expected {ctx.rel(expected)}.",
                doc.path,
                fixable=True,
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
        ctx.issue("warning", f"{message} Unknown directories are not auto-fixed.", path)
        return
    if ctx.fix:
        unknown = ctx.root / "inbox" / ".unknown"
        unknown.mkdir(parents=True, exist_ok=True)
        dest = unique_destination(unknown / path.name)
        shutil.move(str(path), str(dest))
        ctx.issue("warning", f"Moved unexpected file to {ctx.rel(dest)}.", path, fixed=True)
        ctx.fixed(f"Moved {ctx.rel(path)} to {ctx.rel(dest)}.")
    else:
        ctx.issue("warning", message, path, fixable=True)


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
            ctx.issue("warning", f"Could not read index: {exc}", index)
            continue
        contract = index_contract(ctx.root, directory)
        missing_entries, dead_links = index_problems(directory, contract, text)
        if ctx.fix and (missing_entries or dead_links):
            dead_links = repair_directory_index(
                ctx, directory, contract, missing_entries, dead_links
            )
            missing_entries = []
        for path in missing_entries:
            ctx.issue(
                "warning",
                f"Required {index_contract_label(contract)} entry is missing from directory index.",
                path,
            )
        for link in dead_links:
            ctx.issue("warning", f"Index links to missing file: {link}.", index)


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


def resolve_link_target(base_dir: Path, link: str) -> Path:
    """Resolve a link destination to the file it names.

    The destination may carry a title, angle brackets, a fragment, ``./`` or
    percent-encoding (see ``indexing.link_path_variants``). The decoded
    spelling is tried first and the literal one second, so a file really named
    ``my%20note.md`` still resolves; when neither exists the decoded spelling
    is returned.
    """
    windows_target = windows_absolute_path(link.split("#", 1)[0])
    if windows_target is not None:
        return windows_target
    candidates = [
        Path(variant) if variant.startswith("/") else (base_dir / variant).resolve()
        for variant in link_path_variants(link)
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if candidates else (base_dir / link.split("#", 1)[0]).resolve()


def check_links(ctx: LintContext) -> None:
    for doc in sorted(ctx.documents.values(), key=lambda item: str(item.path)):
        try:
            rel = doc.path.resolve().relative_to(ctx.root)
        except ValueError:
            continue
        if rel.parts[0] not in {"wiki", "inventory"}:
            continue
        for link in extract_link_destinations(document_text(doc)):
            if not is_local_markdown_link(link):
                continue
            target = resolve_link_target(doc.path.parent, link)
            if not target.exists():
                ctx.issue("warning", f"Markdown link points to missing file: {link}.", doc.path)


FOOTNOTE_DEF_RE = re.compile(r"^\[\^([^\]\s]+)\]:", re.M)
FOOTNOTE_REF_RE = re.compile(r"\[\^([^\]\s]+)\](?!:)")
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:\|[^\]]*)?\]\]")


INLINE_CODE_RE = re.compile(r"`+[^`\n]*`+")


def strip_code_blocks(text: str) -> str:
    """Blank out code so prose checks do not fire inside syntax examples.

    Both fenced blocks and inline spans are removed: a wiki-link or footnote
    marker shown as an example of the syntax is not a link or a citation.
    """
    out: list[str] = []
    fence: str | None = None
    for line in text.split("\n"):
        stripped = line.lstrip()
        if fence is None:
            if stripped.startswith("```") or stripped.startswith("~~~"):
                fence = stripped[:3]
                out.append("")
                continue
            out.append(line)
        else:
            if stripped.startswith(fence):
                fence = None
            out.append("")
    return INLINE_CODE_RE.sub("", "\n".join(out))


def compiled_article_bodies(ctx: LintContext):
    """Yield (doc, prose) for compiled articles, with code blocks removed."""
    for doc in sorted(ctx.documents.values(), key=lambda item: str(item.path)):
        try:
            rel = doc.path.resolve().relative_to(ctx.root)
        except ValueError:
            continue
        if not rel.parts or rel.parts[0] != "wiki" or doc.path.name == "_index.md":
            continue
        text = document_text(doc)
        parts = split_markdown_frontmatter(text)
        body = parts[1] if parts else text
        yield doc, strip_code_blocks(body)


def check_footnotes(ctx: LintContext) -> None:
    """Footnotes are the portable way to bind a claim to its evidence.

    A reference with no definition silently drops the evidence in every
    renderer; a definition with no reference is a citation orphaned by an edit.
    Both are structural, so neither needs an LLM or an editorial judgement.
    """
    for doc, prose in compiled_article_bodies(ctx):
        defined = {match.group(1) for match in FOOTNOTE_DEF_RE.finditer(prose)}
        # FOOTNOTE_REF_RE's negative lookahead already excludes definition lines.
        referenced = {match.group(1) for match in FOOTNOTE_REF_RE.finditer(prose)}
        for key in sorted(referenced - defined):
            ctx.issue(
                "warning",
                f"Footnote [^{key}] is referenced but never defined; the claim it "
                "supports has no evidence in the rendered article.",
                doc.path,
            )
        for key in sorted(defined - referenced):
            ctx.issue(
                "suggestion",
                f"Footnote [^{key}] is defined but never referenced; it supports "
                "no claim in the article.",
                doc.path,
            )


# Directories whose files a wiki-link may legitimately name without the file
# being a compiled article. ``wiki/`` articles are resolved separately.
NON_ARTICLE_LINK_DIRS = ("inventory", "raw", "output", "datasets")


INDEX_LINK_KEY = "_index"


def link_key(stem: str) -> str:
    """Lookup key of a wiki-link target: the slug, except ``_index`` stays itself."""
    return INDEX_LINK_KEY if stem == INDEX_LINK_KEY else slugify(stem)


def non_article_targets(root: Path) -> dict[str, list[Path]]:
    """Map link keys of Markdown files outside ``wiki/`` articles to their paths.

    ``_index`` maps to every index file (the master one included), so a
    ``[[_index]]`` link resolves to something that exists.
    """
    targets: dict[str, list[Path]] = {}
    candidates = [root / "_index.md"]
    for name in NON_ARTICLE_LINK_DIRS:
        candidates.extend(markdown_files(root / name))
    candidates.extend(
        path
        for path in markdown_files(root / "wiki")
        if path.name == "_index.md"
    )
    for path in candidates:
        if path.is_file():
            targets.setdefault(link_key(path.stem), []).append(path)
    return targets


def wikilink_file_target(doc_dir: Path, root: Path, target: str) -> Path | None:
    """Resolve a path-style wiki-link relative to the linking file, or None.

    Only a target that stays inside the wiki root and names an existing
    Markdown file counts; anything else falls back to the basename lookup.
    """
    if "/" not in target:
        return None
    relative = target[:-3] if target.lower().endswith(".md") else target
    candidate = Path(os.path.normpath(doc_dir / (relative + ".md")))
    try:
        candidate.resolve().relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def check_wikilinks(ctx: LintContext) -> None:
    """Resolve [[slug]] / [[slug|text]] targets against compiled articles.

    Markdown links are already validated. Wiki-links are permitted alongside
    them, so a wiki-link to a slug that does not exist looks exactly like a
    working cross-reference while pointing nowhere.

    A dir-qualified target (``[[../topics/x]]``) resolves by its path relative
    to the linking file and, failing that, by its last segment. A target that
    exists but is not a compiled article (a file in ``inventory/``, ``raw/``,
    ``output/`` or an ``_index.md``) is reported as ``info``, not as a defect.
    """
    slugs: dict[str, list[Path]] = {}
    for doc in ctx.documents.values():
        try:
            rel = doc.path.resolve().relative_to(ctx.root)
        except ValueError:
            continue
        if not rel.parts or rel.parts[0] != "wiki" or doc.path.name == "_index.md":
            continue
        names = {slugify(doc.path.stem)}
        for alias in as_string_list(doc.frontmatter.get("aliases")):
            names.add(slugify(alias))
        for name in names:
            if name:
                slugs.setdefault(name, []).append(doc.path)

    others: dict[str, list[Path]] | None = None
    article_paths = {path.resolve() for owners in slugs.values() for path in owners}
    for doc, prose in compiled_article_bodies(ctx):
        seen: set[str] = set()
        for match in WIKILINK_RE.finditer(prose):
            written = match.group(1).strip()
            target = written.replace("\\", "/")
            stem = target.rstrip("/").rsplit("/", 1)[-1]
            if stem.lower().endswith(".md"):
                stem = stem[:-3]
            basename = link_key(stem)
            if not basename or written in seen:
                continue
            seen.add(written)

            by_path = wikilink_file_target(doc.path.parent, ctx.root, target)
            if by_path is not None:
                if by_path.resolve() in article_paths:
                    continue
                owners: list[Path] = [by_path]
                article = False
            else:
                owners = slugs.get(basename, [])
                article = bool(owners)
            if not owners:
                if others is None:
                    others = non_article_targets(ctx.root)
                found = others.get(basename, [])
                nearest = doc.path.parent / "_index.md"
                if basename == INDEX_LINK_KEY and nearest.is_file():
                    found = [nearest]
                if found:
                    listed = ", ".join(sorted(ctx.rel(path) for path in found)[:3])
                    ctx.issue(
                        "info",
                        f"Wiki-link [[{written}]] points to an existing file that is "
                        f"not a compiled article: {listed}.",
                        doc.path,
                    )
                else:
                    ctx.issue(
                        "warning",
                        f"Wiki-link [[{written}]] resolves to no compiled article.",
                        doc.path,
                    )
            elif not article:
                ctx.issue(
                    "info",
                    f"Wiki-link [[{written}]] points to an existing file that is "
                    f"not a compiled article: {ctx.rel(owners[0])}.",
                    doc.path,
                )
            elif len(owners) > 1:
                listed = ", ".join(sorted(ctx.rel(owner) for owner in owners))
                ctx.issue(
                    "suggestion",
                    f"Wiki-link [[{written}]] is ambiguous: {listed}.",
                    doc.path,
                )


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
                )
                continue
            if sources:
                if not isinstance(sources, list):
                    ctx.issue("warning", "sources must be a list.", doc.path)
                    continue
                for source in sources:
                    resolved = resolve_source_ref(ctx, doc.path, str(source), wiki_source=True)
                    if resolved is None:
                        ctx.issue("warning", f"Source reference does not resolve: {source}.", doc.path)
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
                    )

        if "RETRACTED-SOURCE" in doc.body:
            ctx.issue("warning", "Retracted-source marker remains in the file body.", doc.path)


def fix_source_references(ctx: LintContext) -> None:
    if not ctx.fix:
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
        if not doc_changed:
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
            ctx.issue("warning", f"Source reference is ambiguous after slug fallback: {ref}.", owner)
            return None
        if len(contains_matches) == 1:
            return contains_matches[0]
        if len(contains_matches) > 1:
            ctx.issue("warning", f"Source reference is ambiguous after fuzzy slug fallback: {ref}.", owner)
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


def slugify(value: str) -> str:
    value = value.lower().replace("_", "-")
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"[^a-z0-9-]", "", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value


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
            ctx.issue("warning", f"Near-duplicate tags found: {', '.join(matches)}.")


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


def attach_pointer_to_article(ctx: LintContext, article: Path, raw_file: Path) -> bool:
    """Add a raw/ pointer to an article's sources: dependency manifest."""
    try:
        loaded = _frontmatter.load(article)
    except _frontmatter.FrontmatterError:
        return False
    frontmatter = loaded.block
    doc = ctx.documents.get(article.resolve())
    existing = doc.frontmatter.get("sources") if doc else None
    values = [str(value) for value in existing] if isinstance(existing, list) else []
    reference = markdown_relative_link(article.parent, raw_file)
    if reference in values:
        return False
    updated = set_frontmatter_list(frontmatter, "sources", [reference, *values])
    if updated == frontmatter:
        return False
    loaded.rewrite(updated)
    if doc:
        doc.raw_text = None
    return True


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

    if ctx.fix:
        attached = [
            raw_file
            for raw_file in unreferenced
            if len(owners[raw_file]) == 1
            and attach_pointer_to_article(ctx, owners[raw_file][0], raw_file)
        ]
        for raw_file in attached:
            ctx.fixed(
                f"Added {ctx.rel(raw_file)} to the sources of "
                f"{ctx.rel(owners[raw_file][0])}."
            )
        remaining = [raw_file for raw_file in unreferenced if raw_file not in set(attached)]
        if attached:
            load_documents(ctx)
        if remaining:
            create_or_update_coverage_reference(ctx, remaining)
        return

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
            )
        elif candidates:
            listed = ", ".join(sorted(ctx.rel(candidate) for candidate in candidates))
            ctx.issue(
                "suggestion",
                "Raw source is not referenced by any compiled article; these cite "
                f"its underlying source directly: {listed}.",
                raw_file,
            )
        else:
            ctx.issue("suggestion", "Raw source is not referenced by any compiled article.", raw_file)


def create_or_update_coverage_reference(ctx: LintContext, unreferenced: list[Path]) -> None:
    references_dir = ctx.root / "wiki" / "references"
    references_dir.mkdir(parents=True, exist_ok=True)
    coverage_path = references_dir / "uncompiled-source-coverage.md"
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
    regenerated.
    """
    index = directory / "_index.md"
    if not index.exists():
        regenerate_directory_index(ctx, directory)
        return
    contract = index_contract(ctx.root, directory)
    missing_entries, dead_links = index_problems(
        directory, contract, index.read_text(encoding="utf-8")
    )
    if missing_entries or dead_links:
        repair_directory_index(ctx, directory, contract, missing_entries, dead_links)


def markdown_relative_link(base_dir: Path, target: Path) -> str:
    rel = Path(os.path.relpath(target, base_dir)).as_posix()
    return markdown_link_destination(rel)


def check_freshness(ctx: LintContext) -> None:
    today = today_date()
    thresholds = {"hot": 30, "warm": 180, "cold": 365}
    for doc in ctx.documents.values():
        try:
            rel = doc.path.resolve().relative_to(ctx.root)
        except ValueError:
            continue
        if rel.parts[0] != "wiki":
            continue
        volatility = doc.frontmatter.get("volatility")
        if volatility is None:
            ctx.issue("info", "Compiled article has no volatility field.", doc.path)
            continue
        # A non-string value (for example ``volatility: [hot]``) is already
        # reported by the enum check; it is also unhashable, so it must never
        # reach the ``in`` test against the threshold table.
        if not isinstance(volatility, str) or volatility not in thresholds:
            continue
        verified = doc.frontmatter.get("verified") or doc.frontmatter.get("updated")
        verified_date = parse_date(str(verified)) if verified else None
        if not verified_date:
            ctx.issue("warning", "Compiled article has no valid verified/updated date.", doc.path)
            continue
        age = (today - verified_date).days
        if age > thresholds[str(volatility)]:
            ctx.issue(
                "warning",
                f"Compiled article may be stale: {age} days since verification for {volatility} volatility.",
                doc.path,
            )


def check_projects(ctx: LintContext) -> None:
    projects = ctx.root / "output" / "projects"
    if not projects.exists():
        return
    for project in sorted(child for child in projects.iterdir() if child.is_dir()):
        why = project / "WHY.md"
        if not why.exists() or not why.read_text(encoding="utf-8", errors="replace").strip():
            ctx.issue("warning", "Project is missing a non-empty WHY.md.", project)
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", project.name) or re.search(
            r"\d{4}-\d{2}-\d{2}", project.name
        ):
            ctx.issue("warning", "Project slug should be lowercase, hyphen-separated, <=40 chars, no dates.", project)


def check_source_lifecycle_pointers(ctx: LintContext) -> None:
    """Validate portable pointers without requiring local originals in a clone."""
    if is_hub(ctx.root) or not (ctx.root / "config.md").is_file():
        return
    try:
        config = _source_lifecycle.load_config(ctx.root)
    except _source_lifecycle.LifecycleError as exc:
        ctx.issue("critical", f"Invalid source_store configuration: {exc}", ctx.root / "config.md")
        return
    for warning in _source_lifecycle.config_warnings(config):
        _rule_lint.issue_versioned(
            ctx, _rules.CURRENT_RULE_SET, f"source_store: {warning} (source-store-unknown-key).", ctx.root / "config.md"
        )
    for pointer in _source_lifecycle.pointer_files(config):
        for error in _source_lifecycle.validate_pointer(config, pointer, check_original=False):
            ctx.issue("critical", f"Invalid source lifecycle pointer: {error}", pointer)


def append_lint_log(ctx: LintContext) -> None:
    if not ctx.fix:
        return
    log = ctx.root / "log.md"
    if not log.exists():
        return
    counts = ctx.counts()
    today = today_date().isoformat()
    entry = (
        f"\n## [{today}] lint | local command: "
        f"{counts['critical']} critical, {counts['warning']} warnings, "
        f"{counts['suggestion']} suggestions, {len(ctx.fixes)} auto-fixed\n"
    )
    append_text_preserving(log, entry)


def check_hub_registry(ctx: LintContext) -> None:
    registry = ctx.root / "wikis.json"
    if not registry.exists():
        return
    try:
        data = _fsio.read_json_object(registry, "wiki registry")
    except _fsio.JSONReadError as exc:
        ctx.issue("critical", str(exc), registry)
        return

    changed = False
    default_path = data.get("default")
    if isinstance(default_path, str) and default_path not in {"<HUB>", "HUB", "."}:
        if ctx.fix:
            data["default"] = "<HUB>"
            ctx.fixed("Rewrote hub registry default to <HUB>.")
            changed = True
        else:
            ctx.issue(
                "suggestion",
                "Hub registry default should use the portable <HUB> token.",
                registry,
                fixable=True,
            )

    wikis = data.get("wikis")
    if not isinstance(wikis, dict):
        return
    for slug, entry in sorted(wikis.items()):
        if not isinstance(entry, dict):
            continue
        if slug.endswith(PRIVATE_SUFFIX) and entry.get("description"):
            _rule_lint.issue_versioned(
                ctx,
                _rules.CURRENT_RULE_SET,
                f"Registry entry {slug} carries a description: a private topic is registered "
                "by slug and path only, because wikis.json is committed with the hub "
                "(private-registry-description). Remove the description key.",
                registry,
            )
        raw_path = entry.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            continue
        if slug == "hub":
            if raw_path not in {"<HUB>", "HUB", "."}:
                if ctx.fix:
                    entry["path"] = "<HUB>"
                    ctx.fixed("Rewrote hub registry entry path to <HUB>.")
                    changed = True
                else:
                    ctx.issue(
                        "suggestion",
                        "Hub registry entry should use portable path <HUB>.",
                        registry,
                        fixable=True,
                    )
            continue

        relative_path = (
            f"topics/.archive/{slug}" if is_archived_registry_entry(entry) else f"topics/{slug}"
        )
        hub_topic = ctx.root / relative_path
        if initialized_wiki_root(hub_topic) and raw_path != relative_path:
            if ctx.fix:
                entry["path"] = relative_path
                ctx.fixed(f"Rewrote hub-owned registry path for {slug} to {relative_path}.")
                changed = True
            else:
                ctx.issue(
                    "suggestion",
                    f"Hub-owned wiki path should be portable: use {relative_path}.",
                    registry,
                    fixable=True,
                )
            continue

        resolved = resolve_registry_path(raw_path, ctx.root)

        # Identity beats path: an entry carrying an id can be verified against the
        # wiki it resolves to, so a moved or replaced directory is a detectable
        # error rather than a silent write to the wrong copy.
        mismatch = _identity.verify_entry(entry.get("id"), resolved)
        if mismatch:
            ctx.issue("critical", f"Registry entry {slug} {mismatch}.", registry)

        if is_private_placeholder(resolved):
            _rule_lint.issue_versioned(
                ctx,
                _rules.CURRENT_RULE_SET,
                f"Registry entry {slug} points at a private placeholder (README only): "
                "the private wiki is not checked out here.",
                registry,
            )
        if not expand_leading_tilde(raw_path).is_absolute():
            # A path the registry spells relative to the hub (``topics/x``,
            # ``<HUB>/x``, ``../x``). It must name something inside the hub.
            if not is_under(resolved, ctx.root):
                kind = "exists" if resolved.exists() else "does not exist"
                ctx.issue(
                    "info" if resolved.exists() else "warning",
                    f"Registry path for {slug} is a relative path that escapes the hub "
                    f"and {kind}: {raw_path}.",
                    registry,
                )
            elif not resolved.exists():
                _rule_lint.issue_versioned(
                    ctx,
                    _rules.CURRENT_RULE_SET,
                    f"Registry path for {slug} does not exist: {raw_path} "
                    "(registry-path-missing).",
                    registry,
                )
        elif not is_under(resolved, ctx.root):
            if resolved.exists():
                ctx.issue(
                    "info",
                    f"Registry path for {slug} is an external local absolute path that exists: {raw_path}.",
                    registry,
                )
            else:
                ctx.issue(
                    "warning",
                    f"Registry path for {slug} is an external local absolute path that does not exist: {raw_path}.",
                    registry,
                )

    if changed:
        write_registry(ctx.root, data)


PLACEHOLDER_FILES = {"README.md", ".gitkeep"}


def is_private_placeholder(path: Path) -> bool:
    """True for a ``*-PRIVATE`` directory that is only a tracked anchor.

    A hub keeps ``topics/<slug>-PRIVATE/README.md`` so the sibling is visible on
    machines where the private wiki is not checked out. With no ``config.md``
    and no ``_index.md`` it is not a wiki, and judging it as one yields
    structure criticals that carry no information.
    """
    if not path.name.endswith(PRIVATE_SUFFIX):
        return False
    try:
        if not path.is_dir():
            return False
        names = {child.name for child in path.iterdir()}
    except OSError:
        return False
    return bool(names) and names <= PLACEHOLDER_FILES


def check_hub_private_ignore(ctx: LintContext) -> None:
    """A hub inside a git work tree must ignore private topics in both locations.

    Only hubs inside a git work tree are judged; a plain directory cannot
    commit anything. "Inside" is git's answer from the hub directory, so a hub
    nested in a dotfiles or project repository is judged like a repository
    root (knowmade#87), and the probes are asked of git from the hub directory
    too. Both lifecycle locations are probed: the live
    `topics/<slug>-PRIVATE/` and the archived `topics/.archive/<slug>-PRIVATE/`
    (the legacy `topics/*-PRIVATE/` rule covers only the first).

    Severity is `critical` on introduction. COMPATIBILITY.md allows a new rule
    to bypass the info/warning/critical ladder for privacy failures, and a
    private topic that becomes committable is one; the check also only fires
    for hubs in a git work tree and `--fix` repairs it in place. The exception
    is recorded in COMPATIBILITY.md ("Documented ladder exceptions").
    """
    if not is_inside_git_work_tree(ctx.root):
        return
    probes = {
        "live": f"topics/{PRIVATE_IGNORE_PROBE_SLUG}",
        "archived": f"topics/.archive/{PRIVATE_IGNORE_PROBE_SLUG}",
    }
    gaps = [label for label, probe in probes.items() if not hub_ignores_private_topic(ctx.root, probe)]
    if not gaps:
        return
    gitignore = ctx.root / ".gitignore"
    if ctx.fix:
        append_private_ignore_rule(ctx.root)
        ctx.fixed(f"Appended `{PRIVATE_IGNORE_RULE}` to .gitignore so live and archived private topics stay ignored.")
        return
    locations = {
        "live": f"live private topics (topics/<slug>{PRIVATE_SUFFIX}/)",
        "archived": f"archived private topics (topics/.archive/<slug>{PRIVATE_SUFFIX}/)",
    }
    ctx.issue(
        "critical",
        f"Hub .gitignore does not ignore {' or '.join(locations[label] for label in gaps)}; "
        f"they are committable in this git work tree"
        + ("" if "live" in gaps else f" once `archive topic --private` moves them")
        + f". Add `{PRIVATE_IGNORE_RULE}` or run `llm-wiki lint --fix`. "
        "Critical on introduction: a committable private topic is a privacy failure "
        "(COMPATIBILITY.md severity-ladder exception).",
        gitignore,
        fixable=True,
    )


# Directories an older source-lifecycle default created directly under
# `topics/`, shared by every topic and outside every `-PRIVATE` ignore rule.
STRAY_TOPIC_STORE_DIRS = (".wiki-local", "sources")


def check_hub_topic_store_siblings(ctx: LintContext) -> None:
    """Warn about source-store directories sitting beside the topics (knowmade#89).

    Before the fix, a hub topic without an explicit `source_store` resolved its
    store to `topics/.wiki-local/` and `topics/sources/`. Originals and text of
    a `-PRIVATE` topic could land there, committable. `warning` on introduction
    rather than `info`: a documented privacy exception to the ladder (hub roots
    carry no rule-set declaration, and a private original outside its topic is
    a privacy failure); no repair is automatic because the records name their
    paths.
    """
    topics = ctx.root / "topics"
    for name in STRAY_TOPIC_STORE_DIRS:
        stray = topics / name
        if not stray.exists():
            continue
        ctx.issue(
            "warning",
            f"Source-store directory topics/{name}/ sits beside the topic wikis, outside every "
            f"`{PRIVATE_SUFFIX}` ignore rule; an older source-lifecycle default wrote originals or "
            "text of any topic here. Move each source into its topic (`<topic>/.wiki-local/`, "
            "`<topic>/sources/`) or set `source_store` explicitly in that topic's config.md.",
            stray,
        )


# The tracked-machine-state check is new in rules/3 and walks the ladder
# (info now, warning in the next rule set, critical after that).
TRACKED_MACHINE_STATE_INTRODUCED_IN = 3


def check_tracked_machine_state(ctx: LintContext, hub_root: bool) -> None:
    """Report machine-local state that git tracks or would commit (knowmade#98).

    Session state, the specialist registry, audit/librarian run state, editor
    state and local source originals (`hub.STATE_IGNORE_RULES`) carry local
    paths and private slugs. Git is asked from the lint root, so a hub or wiki
    nested in a larger repository is judged on its own subtree. Tracked files
    need `git rm --cached`; untracked ones that no rule ignores are fixed on a
    hub by `--fix`, which appends the missing rules to the hub `.gitignore`.
    """
    if not is_inside_git_work_tree(ctx.root):
        return
    if hub_root and ctx.fix:
        appended = ensure_state_ignore_rules(ctx.root)
        if appended:
            ctx.fixed(f"Appended machine-state ignore rules to .gitignore: {', '.join(appended)}.")
    found = machine_state_files(ctx.root)
    if found is None:
        return
    tracked, committable = found
    if tracked:
        shown = ", ".join(tracked[:5]) + (f" and {len(tracked) - 5} more" if len(tracked) > 5 else "")
        _rule_lint.issue_versioned(
            ctx,
            TRACKED_MACHINE_STATE_INTRODUCED_IN,
            f"Machine-local state is tracked by git: {shown}. It carries local paths, "
            "transcript locations or private slugs; untrack it with `git rm --cached` "
            "and keep it ignored.",
            ctx.root / tracked[0],
        )
    if committable:
        shown = ", ".join(committable[:5]) + (f" and {len(committable) - 5} more" if len(committable) > 5 else "")
        _rule_lint.issue_versioned(
            ctx,
            TRACKED_MACHINE_STATE_INTRODUCED_IN,
            f"Machine-local state is not ignored and would be committed: {shown}; "
            + (state_ignore_repair_hint(ctx.root) if hub_root else "add the machine-state rules to the repository's .gitignore")
            + ".",
            ctx.root / committable[0],
            fixable=hub_root,
        )


def check_engine_contract(ctx: LintContext, strict: bool = False) -> None:
    """Report drift between the engine running and the one the wiki declares.

    The engine is versioned independently of every wiki it judges, so a wiki that
    declares an engine version or checker commit is asking to be told when the
    rules move under it. A wiki that declares nothing is not nagged: the contract
    is opt-in, and each declared form is judged separately.

    Drift is a warning by default. An out-of-date declaration is a coordination
    problem, not a protocol violation, and must not block a corpus that is
    otherwise clean. ``--strict-engine`` raises it to critical for a CI lane that
    should refuse to run an engine other than the declared one.
    """
    state = _engine.contract_state(ctx.root)
    ctx.engine = state
    problem = _rules.config_problem(ctx.root)
    if problem is not None:
        # The file that carries the engine contract cannot be read, so the rule
        # set and engine pins it declares are silently ignored. Like a declared
        # rule set outside the window, it cannot be graded by any rule set.
        _rule_lint.issue_versioned(
            ctx,
            _rules.CURRENT_RULE_SET,
            f"config.md has no usable frontmatter: {problem}. The engine contract "
            f"({_rules.RULE_SET_KEY}, engine pins) cannot be read from it.",
            ctx.root / "config.md",
            exceptional_critical=True,
            target_rule_set=_rules.CURRENT_RULE_SET,
        )
    if state["state"] == "drift":
        ctx.issue(
            "critical" if strict else "warning",
            f"Wiki {_engine.drift_summary(state)}. Upgrade the declaration "
            "deliberately, or run the declared engine.",
            ctx.root / "config.md",
        )
    _rule_lint.check_rule_set_contract(ctx)


def run_lint(args: argparse.Namespace) -> int:
    root = resolve_wiki_root(args)
    ctx = LintContext(root, fix=args.fix)
    if is_private_placeholder(root):
        ctx.issue(
            "info",
            "Private placeholder, not checked out here: the directory holds only "
            "README.md/.gitkeep, so it is not linted as a wiki.",
            root,
        )
        return report_lint_result(ctx, args)
    hub_root = is_hub(root)

    check_engine_contract(ctx, strict=bool(getattr(args, "strict_engine", False)))
    check_structure(ctx)
    check_unknown_files(ctx)
    check_tracked_machine_state(ctx, hub_root)
    if hub_root:
        check_specialist_library(ctx)
        check_hub_registry(ctx)
        check_hub_private_ignore(ctx)
        check_hub_topic_store_siblings(ctx)
        append_lint_log(ctx)
        return report_lint_result(ctx, args)

    load_documents(ctx)
    fix_legacy_wiki_frontmatter(ctx)
    check_frontmatter_schema(ctx)
    check_canonical_placement(ctx)
    load_documents(ctx)
    fix_source_references(ctx)
    check_index_consistency(ctx)
    check_links(ctx)
    check_wikilinks(ctx)
    check_footnotes(ctx)
    check_source_provenance(ctx)
    check_tags(ctx)
    check_coverage(ctx)
    check_freshness(ctx)
    check_projects(ctx)
    check_source_lifecycle_pointers(ctx)
    append_lint_log(ctx)
    return report_lint_result(ctx, args)


def report_lint_result(ctx: LintContext, args: argparse.Namespace) -> int:
    """Print the lint report and return its exit code.

    One verdict feeds the JSON ``status``, the text ``Result:`` line and the
    exit code, so ``--fail-on`` can never make them disagree.
    """
    fail_on = getattr(args, "fail_on", "suggestion")
    exit_code = lint_exit_code(ctx.counts(), fail_on)
    failed = exit_code != 0
    if args.json:
        print_json_report(ctx, failed=failed, fail_on=fail_on)
    else:
        print_text_report(ctx, failed=failed)
    return exit_code


FAIL_ON_LEVELS = ("critical", "warning", "suggestion")

# How the text report names each severity in a finding line.
SEVERITY_LABELS = {
    "critical": "Critical",
    "warning": "Warning",
    "suggestion": "Suggestion",
    "info": "Info",
}


def lint_exit_code(counts: dict[str, int], fail_on: str = "suggestion") -> int:
    """Exit non-zero when a finding at or above ``fail_on`` severity exists.

    The default reproduces the historical behaviour (any finding fails). A
    stricter gate such as ``--fail-on critical`` lets CI block protocol
    violations without being blocked by advisory findings, which otherwise
    makes the exit code unusable as a gate on any wiki carrying known
    citation debt.
    """
    threshold = FAIL_ON_LEVELS.index(fail_on) if fail_on in FAIL_ON_LEVELS else len(FAIL_ON_LEVELS) - 1
    return 1 if any(counts.get(level) for level in FAIL_ON_LEVELS[: threshold + 1]) else 0


def print_json_report(
    ctx: LintContext,
    failed: bool | None = None,
    fail_on: str = "suggestion",
) -> None:
    counts = ctx.counts()
    if failed is None:
        failed = lint_exit_code(counts, fail_on) != 0
    report = {
        "engine": ctx.engine or _engine.contract_state(ctx.root),
        "root": str(ctx.root),
        "status": "fail" if failed else "pass",
        "fail_on": fail_on,
        "counts": counts,
        "fixes": ctx.fixes,
        "issues": [
            {
                "severity": issue.severity,
                "message": issue.message,
                "path": ctx.rel(issue.path) if issue.path else None,
                "fixable": issue.fixable,
            }
            for issue in sorted(ctx.active_issues(), key=lambda item: item.sort_key())
        ],
    }
    print(json.dumps(report, indent=2, sort_keys=True))


def print_text_report(ctx: LintContext, failed: bool | None = None) -> None:
    counts = ctx.counts()
    if failed is None:
        failed = lint_exit_code(counts) != 0
    has_findings = bool(counts["critical"] or counts["warning"] or counts["suggestion"])
    print(f"Knowmade lint: {ctx.root}")
    engine = ctx.engine or _engine.contract_state(ctx.root)
    if engine["commit"] or engine["version"] or engine["state"] != "undeclared":
        version = engine["version"] or "unknown"
        commit = (engine["commit"] or "unknown")[:12]
        declared = []
        if engine["declared_version"]:
            declared.append(f"version {engine['declared_version']}")
        if engine["declared"]:
            declared.append(f"commit {engine['declared'][:12]}")
        print(
            f"Engine: {version} ({commit}); declared: "
            f"{', '.join(declared) or 'none'}, {engine['state']}"
        )
    if ctx.fixes:
        print("\nAuto-fixed:")
        for fix in ctx.fixes:
            print(f"- {fix}")
    if has_findings or counts["info"]:
        print("\nFindings:")
        for issue in sorted(ctx.active_issues(), key=lambda item: item.sort_key()):
            prefix = SEVERITY_LABELS.get(issue.severity, issue.severity.title())
            location = f" ({ctx.rel(issue.path)})" if issue.path else ""
            fixable = " Run again with --fix to apply the safe fix." if issue.fixable and not ctx.fix else ""
            print(f"- {prefix}: {issue.message}{location}{fixable}")
    else:
        print("\nFindings: none")
    print(
        "\nSummary: "
        f"{counts['critical']} critical, {counts['warning']} warnings, "
        f"{counts['suggestion']} suggestions, {counts['info']} info, "
        f"{len(ctx.fixes)} auto-fixed."
    )
    print("Result: " + ("FAIL" if failed else "PASS"))


def check_specialist_library(ctx: LintContext) -> None:
    if not specialist_library_path(ctx.root).exists():
        return
    for issue in collect_specialist_issues(ctx.root):
        path = Path(issue["path"])
        ctx.issue(issue["severity"], issue["message"], path)


def register_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Register the ``lint`` namespace on a top-level subparsers action."""
    lint = subparsers.add_parser(
        "lint",
        help="Run local structural checks on a wiki root.",
        description=(
            "Run deterministic checks that do not require an LLM. Pass a wiki root "
            "path, or use --local/--wiki to resolve one through the normal hub rules."
        ),
    )
    lint.add_argument("path", nargs="?", help="Wiki root path to lint.")
    lint.add_argument("--fix", action="store_true", help="Apply unambiguous structural fixes.")
    add_scope_arguments(
        lint,
        local_help="Lint .wiki/ in the current directory.",
        public_help=f"Resolve --wiki <slug>{PRIVATE_SUFFIX} to the public sibling <slug>.",
        include_archived_help="Allow linting an explicitly targeted archived wiki.",
    )
    lint.add_argument("--json", action="store_true", help="Print a machine-readable JSON report.")
    lint.add_argument(
        "--strict-engine",
        action="store_true",
        help=(
            "Treat a mismatch between the declared and running engine as a protocol "
            "violation rather than a warning."
        ),
    )
    lint.add_argument(
        "--fail-on",
        choices=FAIL_ON_LEVELS,
        default="suggestion",
        help=(
            "Lowest severity that makes the command exit non-zero "
            "(default: suggestion, i.e. any finding)."
        ),
    )
    lint.set_defaults(func=run_lint)
    return lint


def run(args: argparse.Namespace) -> int:
    """Dispatch a parsed ``lint`` namespace."""
    return run_lint(args)
