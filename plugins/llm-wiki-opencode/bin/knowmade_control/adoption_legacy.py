#!/usr/bin/env python3
"""Dry-run-first brownfield adoption scanner for Knowmade.

The ``llm-wiki-adopt`` entry point lives in ``adoption``; this module holds the
scanner, classifier, and batch writer it drives.

Turn an existing populated project directory into a project-local Knowmade wiki
without blindly moving the source tree. The helper inventories existing
material, classifies it for normal ingest / collection ingest / dataset
registration / review / skip, and writes a resumable import batch under
.wiki/inbox/.imports/ when --apply is requested.

Reference mode is the default and never changes source files. Copy/move modes
stage only ordinary single-source ingest files; collections and datasets remain
referenced so the agentic workflow can use the proper specialized importer.
"""

from __future__ import annotations

import datetime as dt
import fnmatch
import hashlib
import json
import os
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from .fsio import atomic_write_bytes, atomic_write_text



SKIP_DIR_NAMES = {
    ".git", ".hg", ".svn", ".idea", ".vscode", ".cache", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".tox", ".venv", "venv", "__pycache__",
    "node_modules", "vendor", "dist", "build", "target", "coverage", ".next",
    ".nuxt", ".turbo",
}
CORPUS_DIR_NAMES = {
    "archive", "archives", "corpus", "corpora", "dump", "dumps", "export",
    "exports", "scrape", "scrapes", "scraped", "scraped-sites", "sites", "wayback",
}
DATASET_DIR_NAMES = {"data", "dataset", "datasets", "database", "databases"}
INGEST_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".rst", ".html", ".htm", ".pdf", ".docx",
    ".odt", ".rtf", ".epub",
}
SMALL_DATA_EXTENSIONS = {".csv", ".tsv", ".json", ".jsonl", ".xml", ".yaml", ".yml"}
DATASET_EXTENSIONS = {
    ".sqlite", ".sqlite3", ".db", ".parquet", ".feather", ".arrow", ".avro",
    ".orc", ".h5", ".hdf5", ".xlsx", ".xls", ".ods",
}
ARCHIVE_EXTENSIONS = {".zip", ".7z", ".rar", ".tar", ".tgz", ".gz", ".bz2", ".xz", ".zst"}
MEDIA_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".mp3", ".wav",
    ".m4a", ".flac", ".mp4", ".mov", ".mkv", ".webm",
}
CODE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".kts", ".go",
    ".rs", ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".rb", ".php",
    ".sh", ".ps1", ".sql",
}
GENERATED_EXTENSIONS = {".pyc", ".pyo", ".class", ".o", ".obj", ".dll", ".so", ".dylib"}

DEFAULT_SMALL_DATA_LIMIT = 20 * 1024 * 1024
DEFAULT_HASH_LIMIT = 64 * 1024 * 1024
DEFAULT_MAX_FILES = 10000


def today() -> str:
    value = os.environ.get("LLM_WIKI_TODAY")
    if value:
        try:
            return dt.date.fromisoformat(value[:10]).isoformat()
        except ValueError as exc:
            raise SystemExit(f"invalid LLM_WIKI_TODAY: {value!r}") from exc
    return dt.date.today().isoformat()


def slugify(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    return re.sub(r"-+", "-", value).strip("-") or "project"


def item_id(kind: str, relpath: str) -> str:
    return hashlib.sha256(f"{kind}:{relpath}".encode("utf-8")).hexdigest()[:16]


def file_sha256(path: Path, limit: int) -> str | None:
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size > limit:
        return None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def is_knowmade_checkout(path: Path) -> bool:
    return (
        (path / "scripts" / "llm-wiki").is_file()
        and (path / "claude-plugin" / "skills" / "wiki-manager" / "SKILL.md").is_file()
        and (path / "plugins" / "llm-wiki").exists()
    )


def normalize_exclude(pattern: str) -> str:
    """Match ``--exclude`` globs against POSIX-style relative paths on every OS.

    Scan rows always record ``source_path`` with forward slashes, so a pattern
    typed with backslashes on Windows is folded to the same form. Matching is
    case-sensitive everywhere: ``fnmatch.fnmatch`` folds case only on Windows,
    which made the same project yield different row ids per platform because
    ``item_id`` hashes the relative path of every row that survives exclusion.
    """
    return pattern.replace("\\", "/")


def excluded(relpath: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(relpath, normalize_exclude(pattern)) for pattern in patterns)


def approximate_tree(path: Path, cap: int = 201) -> tuple[int, int]:
    """Return bounded (file_count, size_of_seen_files) for planning only."""
    count = 0
    size = 0
    try:
        for root, dirs, files in os.walk(path):
            dirs[:] = [name for name in dirs if name not in SKIP_DIR_NAMES and not name.startswith(".git")]
            for name in files:
                count += 1
                try:
                    size += (Path(root) / name).stat().st_size
                except OSError:
                    pass
                if count >= cap:
                    return count, size
    except OSError:
        pass
    return count, size


def directory_row(path: Path, project_root: Path, classification: str, action: str, reason: str) -> dict[str, Any]:
    rel = path.relative_to(project_root).as_posix()
    count, size = approximate_tree(path)
    return {
        "item_id": item_id("directory", rel),
        "kind": "directory",
        "source_path": rel,
        "absolute_source": str(path.resolve()),
        "size_bytes": size,
        "approx_files": count,
        "sha256": None,
        "classification": classification,
        "proposed_action": action,
        "reason": reason,
        "state": "planned",
    }


def classify_file(path: Path, size: int, small_data_limit: int) -> tuple[str, str, str]:
    name = path.name.lower()
    suffix = path.suffix.lower()
    if name in {".ds_store", "thumbs.db", "desktop.ini"} or suffix in GENERATED_EXTENSIONS:
        return "skip", "skip", "generated-or-os-file"
    if suffix in INGEST_EXTENSIONS:
        return "ingest", "stage", "ordinary-single-source"
    if suffix in SMALL_DATA_EXTENSIONS:
        if size <= small_data_limit:
            return "ingest", "stage", "small-structured-data"
        return "dataset", "register-dataset", "large-structured-data"
    if suffix in DATASET_EXTENSIONS:
        return "dataset", "register-dataset", "query-oriented-or-binary-data"
    if suffix in ARCHIVE_EXTENSIONS:
        return "collection", "ingest-collection", "archive-or-compressed-corpus"
    if suffix in MEDIA_EXTENSIONS:
        return "needs-human", "review", "media-requires-context-or-specialized-extraction"
    if suffix in CODE_EXTENSIONS:
        return "needs-human", "review", "loose-source-code-may-be-tooling-or-corpus"
    if not suffix and path.is_file():
        return "needs-human", "review", "extensionless-file"
    return "needs-human", "review", "unclassified-file-type"


def scan_project(
    project_root: Path,
    wiki_root: Path,
    patterns: list[str],
    max_files: int,
    small_data_limit: int,
    hash_limit: int,
) -> tuple[list[dict[str, Any]], bool]:
    rows: list[dict[str, Any]] = []
    seen_hashes: dict[str, str] = {}
    scanned_files = 0
    truncated = False
    wiki_resolved = wiki_root.resolve(strict=False)

    for current, dirnames, filenames in os.walk(project_root, topdown=True):
        current_path = Path(current)
        kept: list[str] = []
        for dirname in sorted(dirnames):
            child = current_path / dirname
            rel = child.relative_to(project_root).as_posix()
            child_resolved = child.resolve(strict=False)
            if child_resolved == wiki_resolved or wiki_resolved in child_resolved.parents:
                rows.append(directory_row(child, project_root, "skip", "skip", "knowmade-wiki-root"))
                continue
            if excluded(rel, patterns):
                rows.append(directory_row(child, project_root, "skip", "skip", "user-excluded"))
                continue
            if dirname in SKIP_DIR_NAMES:
                rows.append(directory_row(child, project_root, "skip", "skip", f"excluded-directory:{dirname}"))
                continue
            if is_knowmade_checkout(child):
                rows.append(directory_row(child, project_root, "skip", "skip", "knowmade-runtime-checkout"))
                continue
            if (child / ".git").exists():
                rows.append(directory_row(child, project_root, "collection", "ingest-collection", "nested-git-repository"))
                continue

            lower = dirname.lower()
            approx_count, approx_size = approximate_tree(child)
            if lower in CORPUS_DIR_NAMES and approx_count >= 20:
                rows.append(directory_row(child, project_root, "collection", "ingest-collection", "corpus-like-directory"))
                continue
            if lower in DATASET_DIR_NAMES and (approx_count >= 20 or approx_size > small_data_limit * 2):
                rows.append(directory_row(child, project_root, "dataset", "register-dataset", "dataset-like-directory"))
                continue
            kept.append(dirname)
        dirnames[:] = kept

        for filename in sorted(filenames):
            path = current_path / filename
            rel = path.relative_to(project_root).as_posix()
            if excluded(rel, patterns):
                continue
            if scanned_files >= max_files:
                truncated = True
                dirnames[:] = []
                break
            scanned_files += 1
            try:
                stat = path.stat()
            except OSError as exc:
                rows.append({
                    "item_id": item_id("file", rel), "kind": "file", "source_path": rel,
                    "absolute_source": str(path.resolve(strict=False)), "size_bytes": None,
                    "sha256": None, "classification": "needs-human", "proposed_action": "review",
                    "reason": f"stat-error:{type(exc).__name__}", "state": "planned",
                })
                continue

            classification, action, reason = classify_file(path, stat.st_size, small_data_limit)
            digest = file_sha256(path, hash_limit) if classification != "skip" else None
            # Every empty file hashes to the same digest; markers such as
            # __init__.py or .gitkeep are not duplicates of one another.
            if digest and stat.st_size > 0 and digest in seen_hashes:
                classification, action, reason = "skip", "skip", f"duplicate-of:{seen_hashes[digest]}"
            elif digest and stat.st_size > 0:
                seen_hashes[digest] = rel
            rows.append({
                "item_id": item_id("file", rel), "kind": "file", "source_path": rel,
                "absolute_source": str(path.resolve()), "size_bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns, "sha256": digest, "classification": classification,
                "proposed_action": action, "reason": reason, "state": "planned",
            })
        if truncated:
            break

    rows.sort(key=lambda row: (row["source_path"], row["kind"]))
    return rows, truncated


def minimal_index(title: str, date: str) -> str:
    return (
        f"# {title} Index\n\n> Knowmade-managed index.\n\nLast updated: {date}\n\n"
        "## Contents\n\n| File | Summary | Tags | Updated |\n|------|---------|------|---------|\n"
    )


def pointer_index(title: str, categories: list[str], date: str) -> str:
    """Category roots roll up by pointing at each child index.

    lint renders these by an explicit directory contract, so a file table here
    would be reported as a missing pointer entry on the first lint run.
    """
    lines = [f"# {title}", ""]
    for name in categories:
        lines.append(f"## {name.capitalize()}")
        lines.append(f"See [{name}/_index.md]({name}/_index.md)")
        lines.append("")
    lines.append("## Stats")
    lines.append(f"- Last updated: {date}")
    lines.append("")
    return chr(10).join(lines)


def starter_schema(date: str) -> str:
    return f'''---
title: "Topic Guide"
schema_state: advisory
created: {date}
updated: {date}
summary: "Human-owned topic guide for local vocabulary and conventions."
---

# Topic Guide

## Entity Types

Use only topic-specific entity types that improve this wiki.

## Relationship Verbs

Prefer explicit evidence-backed relationships over vague association.

## Source Conventions

- Keep processed raw sources immutable under `raw/`.
- Keep generated deliverables under `output/`.
- Use dataset manifests for large or query-oriented data.

## Compile Guidance

Synthesize durable claims from evidence; do not create one article per source by default.
'''


def ensure_local_wiki(wiki_root: Path, project_root: Path, title: str) -> list[str]:
    created: list[str] = []
    date = today()
    if wiki_root.exists() and not wiki_root.is_dir():
        raise SystemExit(f"wiki root exists but is not a directory: {wiki_root}")
    if wiki_root.exists():
        existing = [child.name for child in wiki_root.iterdir()]
        markers = {"_index.md", "config.md", "raw", "wiki", "inbox"}
        if existing and not markers.intersection(existing):
            raise SystemExit(f"refusing to initialize non-empty non-wiki directory: {wiki_root}")
    else:
        wiki_root.mkdir(parents=True)
        created.append(str(wiki_root))

    directories = [
        "inbox", "inbox/.processed", "raw", "raw/articles", "raw/papers", "raw/repos",
        "raw/notes", "raw/data", "wiki", "wiki/concepts", "wiki/topics",
        "wiki/references", "wiki/theses", "output",
    ]
    for rel in directories:
        path = wiki_root / rel
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            created.append(str(path))

    config = wiki_root / "config.md"
    if not config.exists():
        safe_title = title.replace('"', '\\"')
        config.write_bytes(f'''---
title: "{safe_title}"
description: "Knowmade wiki adopted from an existing project tree."
created: {date}
---

# Wiki Configuration

## Scope

Knowledge and source material associated with `{project_root.name}`.

## Conventions

Standard Knowmade conventions apply. Brownfield source coverage is tracked through `inbox/.imports/` batches.
'''.encode("utf-8"))
        created.append(str(config))

    schema = wiki_root / "schema.md"
    if not schema.exists():
        schema.write_bytes(starter_schema(date).encode("utf-8"))
        created.append(str(schema))
    log = wiki_root / "log.md"
    if not log.exists():
        log.write_bytes("# Activity Log\n".encode("utf-8"))
        created.append(str(log))

    indexes = {
        "_index.md": title, "raw/_index.md": "Raw", "raw/articles/_index.md": "Articles",
        "raw/papers/_index.md": "Papers", "raw/repos/_index.md": "Repos",
        "raw/notes/_index.md": "Notes", "raw/data/_index.md": "Data", "wiki/_index.md": "Wiki",
        "wiki/concepts/_index.md": "Concepts", "wiki/topics/_index.md": "Topics",
        "wiki/references/_index.md": "References", "wiki/theses/_index.md": "Theses",
        "output/_index.md": "Output",
    }
    pointer_roots = {
        "raw/_index.md": ("Raw Sources Index", ["articles", "papers", "repos", "notes", "data"]),
        "wiki/_index.md": ("Wiki Articles", ["concepts", "topics", "references", "theses"]),
    }
    for rel, index_title in indexes.items():
        path = wiki_root / rel
        if path.exists():
            continue
        if rel in pointer_roots:
            heading, categories = pointer_roots[rel]
            path.write_bytes(pointer_index(heading, categories, date).encode("utf-8"))
        else:
            path.write_bytes(minimal_index(index_title, date).encode("utf-8"))
        created.append(str(path))
    return created


def make_batch_id(project_root: Path) -> str:
    digest = hashlib.sha256(str(project_root.resolve()).encode("utf-8")).hexdigest()[:8]
    return f"{today()}-{slugify(project_root.name)}-{digest}"


def summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    counter = Counter(row["classification"] for row in rows)
    return {key: counter.get(key, 0) for key in ["ingest", "collection", "dataset", "needs-human", "skip"]}


def render_plan(project_root: Path, wiki_root: Path, batch_id: str, mode: str, rows: list[dict[str, Any]], truncated: bool) -> str:
    counts = summary(rows)
    lines = [
        f"# Brownfield Adoption Plan — {project_root.name}", "", f"- Batch: `{batch_id}`",
        f"- Project root: `{project_root}`", f"- Wiki root: `{wiki_root}`", f"- Mode: `{mode}`",
        f"- Scan truncated: `{'yes' if truncated else 'no'}`", "", "## Classification Summary", "",
        "| Class | Count | Intended handling |", "|---|---:|---|",
        f"| ingest | {counts['ingest']} | normal inbox ingestion; copy/move modes may stage these files |",
        f"| collection | {counts['collection']} | structured collection ingestion from original path |",
        f"| dataset | {counts['dataset']} | dataset manifest / external data registration |",
        f"| needs-human | {counts['needs-human']} | review before promotion |",
        f"| skip | {counts['skip']} | excluded/generated/duplicate/runtime material |", "",
        "## Safety Rules", "", "- Source files remain untouched in reference mode.",
        "- Copy/move staging applies only to ordinary `ingest` file rows.",
        "- Collection and dataset roots remain external until their specialized workflow processes them.",
        "- A nested Knowmade engine checkout is never treated as source content.",
        "- Every manifest row keeps its original path and proposed handling.", "", "## Next Step", "",
        "Ask the full Knowmade workflow to process this batch manifest. It should convert `ingest` rows into normal raw evidence, use collection ingestion for `collection` rows, create dataset manifests for `dataset` rows, and leave review/skip decisions explicit.", "",
    ]
    if truncated:
        lines.extend(["## Warning", "", "The scan hit `--max-files`; this batch is incomplete. Increase the limit or adopt additional subtrees before treating brownfield coverage as complete.", ""])
    return "\n".join(lines)


MOVE_CHECKPOINT_EVERY = 50


def _manifest_bytes(rows: list[dict[str, Any]]) -> bytes:
    return "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows).encode("utf-8")


def _state_payload(
    project_root: Path, wiki_root: Path, batch_root: Path, batch_id: str, mode: str,
    rows: list[dict[str, Any]], truncated: bool, staged: int, warnings: list[str], staging: str,
    stage_error: str | None = None,
) -> dict[str, Any]:
    payload = {
        "schema": "knowmade/adoption-batch/v1", "batch_id": batch_id, "created": today(),
        "project_root": str(project_root.resolve()), "wiki_root": str(wiki_root.resolve()),
        "wiki_root_relative": Path(os.path.relpath(wiki_root.resolve(), batch_root.resolve())).as_posix(),
        "mode": mode, "status": "planned" if staged == 0 else "staged", "scan_truncated": truncated,
        "counts": summary(rows), "rows": len(rows), "staged_files": staged,
        "staging_warnings": warnings, "manifest": "manifest.jsonl", "plan": "PLAN.md",
        # "pending" until every file move finished, "complete" afterwards, and
        # "interrupted" when a move loop died part-way (knowmade#36): rows still
        # in state "planned" were not moved, rows in "staged-move" were.
        "staging": staging,
    }
    if stage_error:
        payload["stage_error"] = stage_error
    return payload


def _persist_batch(
    batch_root: Path, project_root: Path, wiki_root: Path, batch_id: str, mode: str,
    rows: list[dict[str, Any]], truncated: bool, staged: int, warnings: list[str], staging: str,
    stage_error: str | None = None,
) -> dict[str, Any]:
    """Write manifest then state atomically; the manifest never lags a moved file."""
    atomic_write_bytes(batch_root / "manifest.jsonl", _manifest_bytes(rows))
    payload = _state_payload(
        project_root, wiki_root, batch_root, batch_id, mode, rows, truncated, staged, warnings, staging, stage_error
    )
    atomic_write_text(batch_root / "state.json", json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return payload


def stage_ingest_files(
    rows: list[dict[str, Any]], project_root: Path, batch_root: Path, mode: str, confirmed_move: bool,
    checkpoint: Any = None,
) -> tuple[int, list[str]]:
    """Copy or move ordinary ingest files into ``<batch>/staged/``.

    ``checkpoint(row, staged, warnings)`` is called after every moved original so
    the caller can keep a journal (and periodically the manifest) in step with
    the disk.
    A per-file ``OSError`` is recorded on the row (``state: error``) and staging
    continues with the next file.
    """
    if mode == "reference":
        return 0, []
    if mode == "move" and not confirmed_move:
        raise SystemExit("--mode move requires --yes together with --apply")
    staged = 0
    warnings: list[str] = []
    stage_root = batch_root / "staged"
    for row in rows:
        if row["kind"] != "file" or row["classification"] != "ingest":
            continue
        source = project_root / row["source_path"]
        if not source.exists():
            row["state"], row["error"] = "error", "source-missing-before-staging"
            warnings.append(row["source_path"])
            continue
        destination = stage_root / row["source_path"]
        if destination.exists():
            row["state"], row["error"] = "error", "staging-destination-already-exists"
            warnings.append(row["source_path"])
            continue
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if mode == "copy":
                shutil.copy2(source, destination)
            else:
                shutil.move(str(source), str(destination))
        except OSError as exc:
            row["state"], row["error"] = "error", f"staging-failed:{type(exc).__name__}:{exc}"
            warnings.append(row["source_path"])
            continue
        row["state"] = "staged-copy" if mode == "copy" else "staged-move"
        row["staged_path"] = destination.relative_to(batch_root).as_posix()
        staged += 1
        if checkpoint is not None and mode == "move":
            checkpoint(row, staged, warnings)
    return staged, warnings


def write_batch(project_root: Path, wiki_root: Path, batch_id: str, mode: str, rows: list[dict[str, Any]], truncated: bool, confirmed_move: bool) -> dict[str, Any]:
    imports_root = wiki_root / "inbox" / ".imports"
    imports_root.mkdir(parents=True, exist_ok=True)
    batch_root = imports_root / batch_id
    if batch_root.exists():
        raise SystemExit(f"batch already exists: {batch_root}; choose --batch-id or resume it instead of overwriting")
    if mode == "move" and not confirmed_move:
        raise SystemExit("--mode move requires --yes together with --apply")
    batch_root.mkdir(parents=True)
    # Durability order (knowmade#36): PLAN.md, manifest and state exist, with
    # every row still "planned", before the first original is touched. Each
    # completed move is then checkpointed into the manifest, so an interrupted
    # run leaves a record of what moved and what is still pending.
    atomic_write_text(batch_root / "PLAN.md", render_plan(project_root, wiki_root, batch_id, mode, rows, truncated))
    staging_needed = mode != "reference" and any(r["kind"] == "file" and r["classification"] == "ingest" for r in rows)
    _persist_batch(batch_root, project_root, wiki_root, batch_id, mode, rows, truncated, 0, [], "pending" if staging_needed else "complete")

    def checkpoint(row: dict[str, Any], done: int, warned: list[str]) -> None:
        # One journal line per moved original (cheap, append-only) so a hard
        # crash between manifest flushes still leaves a record of every move;
        # the manifest itself is rewritten every MOVE_CHECKPOINT_EVERY moves.
        with (batch_root / "events.jsonl").open("a", encoding="utf-8", newline="\n") as journal:
            journal.write(json.dumps({
                "schema": "knowmade/adoption-batch-event/v1", "event": "stage-move", "item_id": row.get("item_id"),
                "source_path": row.get("source_path"), "staged_path": row.get("staged_path"), "state": row.get("state"),
            }, sort_keys=True) + "\n")
        if done % MOVE_CHECKPOINT_EVERY == 0:
            _persist_batch(batch_root, project_root, wiki_root, batch_id, mode, rows, truncated, done, list(warned), "pending")

    try:
        staged, warnings = stage_ingest_files(rows, project_root, batch_root, mode, confirmed_move, checkpoint)
    except BaseException as exc:
        done = sum(1 for r in rows if r.get("state") in {"staged-copy", "staged-move"})
        _persist_batch(
            batch_root, project_root, wiki_root, batch_id, mode, rows, truncated, done,
            [r["source_path"] for r in rows if r.get("state") == "error"], "interrupted",
            f"{type(exc).__name__}: {exc}",
        )
        raise
    payload = _persist_batch(batch_root, project_root, wiki_root, batch_id, mode, rows, truncated, staged, warnings, "complete")
    with (wiki_root / "log.md").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"\n## [{today()}] adopt | Brownfield batch `{batch_id}` planned from `{project_root}` in `{mode}` mode ({len(rows)} rows).\n")
    payload["batch_root"] = str(batch_root)
    return payload
