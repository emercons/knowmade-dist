"""Directory index contracts and renderers for the deterministic lint.

Which directories carry a generated ``_index.md``, what each index must link
to, the renderers that regenerate one under ``lint --fix``, and how a
Markdown link destination is read and compared. Moved from ``legacy_cli``
(emercons/knowmade#67 R1.4, ``KM-CLI-001``); the ``lint_*`` modules are the only callers.

Link destinations are normalised in one place (emercons/knowmade#34):
``./`` prefixes, percent-encoding such as ``%20``, ``[t](path "title")``
titles, ``<angle bracketed>`` destinations, ``#fragment`` suffixes and
redundant separators never decide whether an index entry or a link counts as
present. ``lint --fix`` regenerates an index wholesale only when it is a stub
or carries the generated marker; otherwise it appends the missing entries and
drops the dead ones and touches nothing else.
"""

from __future__ import annotations

import posixpath
import re
import urllib.parse
from pathlib import Path
from typing import Any

from .common import (
    as_string_list,
    first_body_summary,
    markdown_link_destination,
    table_cell,
)
from .lint_report import LintContext
from .markdown import (
    parse_frontmatter_block,
    split_markdown_frontmatter,
    today_date,
)


RAW_INDEX_CATEGORIES = ["articles", "papers", "repos", "notes", "data"]
WIKI_INDEX_CATEGORIES = ["concepts", "topics", "references", "theses"]
INVENTORY_NAVIGATION_DIRS = ["items", "ideas", "candidates", "entities", "corpora", "views"]
INVENTORY_RECORD_DIRS = ["items", "ideas", "candidates", "entities", "corpora"]


def is_index_checked_directory(root: Path, directory: Path) -> bool:
    try:
        rel = directory.resolve().relative_to(root)
    except ValueError:
        return False
    if not rel.parts or any(part.startswith(".") for part in rel.parts):
        return False
    return rel.parts[0] in {"raw", "wiki", "inventory", "datasets", "output"}


def index_contract(root: Path, directory: Path) -> str:
    """Return the declared shape for an index, never infer it from its text."""
    rel = directory.resolve().relative_to(root).parts
    if rel == ("raw",):
        return "raw-root"
    if rel == ("wiki",):
        return "wiki-root"
    if rel == ("inventory",):
        return "inventory-root"
    if rel == ("datasets",):
        return "datasets-root"
    if rel == ("output",):
        return "output-root"
    if len(rel) == 2 and rel[0] == "datasets":
        return "dataset-root"
    return "direct-files"


def index_contract_label(contract: str) -> str:
    return {
        "raw-root": "raw category pointer",
        "wiki-root": "wiki category pointer",
        "inventory-root": "inventory navigation or record rollup",
        "datasets-root": "dataset manifest registry",
        "output-root": "output artifact or project entry",
        "dataset-root": "dataset manifest or navigation entry",
        "direct-files": "file",
    }[contract]


def index_contract_entries(directory: Path, contract: str) -> list[Path]:
    if contract == "raw-root":
        return existing_category_indexes(directory, RAW_INDEX_CATEGORIES)
    if contract == "wiki-root":
        return existing_category_indexes(directory, WIKI_INDEX_CATEGORIES)
    if contract == "inventory-root":
        navigation = existing_category_indexes(directory, INVENTORY_NAVIGATION_DIRS)
        return navigation + inventory_record_files(directory)
    if contract == "datasets-root":
        return sorted(directory.glob("*/MANIFEST.md"))
    if contract == "output-root":
        return output_loose_files(directory) + output_project_why_files(directory)
    if contract == "dataset-root":
        entries = sorted(path for path in directory.glob("*.md") if path.name != "_index.md")
        for name in ("samples", "profiles", "queries"):
            index = directory / name / "_index.md"
            if index.exists():
                entries.append(index)
        return entries
    return sorted(
        path for path in directory.glob("*.md") if path.name != "_index.md"
    )


def existing_category_indexes(directory: Path, names: list[str]) -> list[Path]:
    return [
        directory / name / "_index.md"
        for name in names
        if (directory / name).exists()
    ]


def inventory_record_files(directory: Path) -> list[Path]:
    records: list[Path] = []
    for name in INVENTORY_RECORD_DIRS:
        child = directory / name
        if child.exists():
            records.extend(path for path in child.glob("*.md") if path.name != "_index.md")
    return sorted(records, key=lambda path: path.relative_to(directory).as_posix())


def output_loose_files(directory: Path) -> list[Path]:
    return sorted(path for path in directory.glob("*.md") if path.name != "_index.md")


def output_project_why_files(directory: Path) -> list[Path]:
    projects = directory / "projects"
    if not projects.exists():
        return []
    return sorted(
        project / "WHY.md"
        for project in projects.iterdir()
        if project.is_dir() and not project.name.startswith(".") and (project / "WHY.md").exists()
    )


# A Markdown inline link: ``](destination)``, ``](<destination>)`` or either
# form followed by a title in double quotes, single quotes or parentheses. A
# bare destination may still contain spaces (``](my note.md)``), which the
# earlier, looser reader accepted and which Obsidian-style vaults contain.
_LINK_TITLE = r"""(?:"[^"\n]*"|'[^'\n]*'|\([^)\n]*\))"""
LINK_RE = re.compile(
    r"\]\(\s*(?:<([^>\n]*)>|([^\s)][^)\n]*?))(?:\s+" + _LINK_TITLE + r")?\s*\)"
)
_TRAILING_TITLE_RE = re.compile(r"^(.*?\S)\s+" + _LINK_TITLE + r"$")
_WINDOWS_DRIVE_RE = re.compile(r"^/?[A-Za-z]:[\\/]")


def extract_link_destinations(text: str) -> list[str]:
    """Return every inline link destination, without title or angle brackets.

    The destination is returned as written (still percent-encoded, still
    carrying any ``./`` or ``#fragment``), so a finding can quote it.
    """
    destinations: list[str] = []
    for match in LINK_RE.finditer(text):
        destination = match.group(1) if match.group(1) is not None else match.group(2)
        destination = destination.strip()
        if destination:
            destinations.append(destination)
    return destinations


def clean_link_destination(text: str) -> str:
    """Reduce one destination, possibly titled or bracketed, to its path text."""
    text = text.strip()
    if text.startswith("<"):
        end = text.find(">")
        if end != -1:
            return text[1:end].strip()
    titled = _TRAILING_TITLE_RE.match(text)
    return titled.group(1) if titled else text


def link_path_variants(destination: str) -> list[str]:
    """Return the path spellings a destination may mean, best first.

    The fragment is dropped. The percent-decoded spelling comes first because
    that is what editors write (``my%20note.md``); the literal spelling follows
    so a file that really is named ``my%20note.md`` still matches. Each is
    stripped of ``./``, duplicate separators and ``..`` detours and written
    with forward slashes. Absolute and drive paths are left as they are.
    """
    path = clean_link_destination(destination).split("#", 1)[0].strip()
    variants: list[str] = []
    for candidate in (urllib.parse.unquote(path), path):
        tidy = tidy_link_path(candidate)
        if tidy and tidy not in variants:
            variants.append(tidy)
    return variants


def tidy_link_path(path: str) -> str:
    path = path.replace("\\", "/")
    if not path or path.startswith("/") or _WINDOWS_DRIVE_RE.match(path):
        return path
    tidy = posixpath.normpath(path)
    return "" if tidy == "." else tidy


def index_links_to(text: str, expected: str) -> bool:
    wanted = tidy_link_path(expected)
    return any(
        wanted in link_path_variants(destination)
        for destination in extract_link_destinations(text)
    )


def index_frontmatter(ctx: LintContext, path: Path) -> dict[str, Any]:
    doc = ctx.documents.get(path.resolve())
    if doc:
        return doc.frontmatter
    try:
        split = split_markdown_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        return {}
    return parse_frontmatter_block(split[0]) if split else {}


def regenerate_directory_index(
    ctx: LintContext, directory: Path, contract: str | None = None
) -> None:
    contract = contract or index_contract(ctx.root, directory)
    renderers = {
        "raw-root": render_category_pointer_index,
        "wiki-root": render_category_pointer_index,
        "inventory-root": render_inventory_index,
        "datasets-root": render_dataset_registry_index,
        "output-root": render_output_index,
        "dataset-root": render_dataset_index,
        "direct-files": render_direct_file_index,
    }
    text = renderers[contract](ctx, directory, contract)
    index = directory / "_index.md"
    index.write_bytes(text.encode("utf-8"))
    ctx.fixed(f"Regenerated {index_contract_label(contract)} index {ctx.rel(index)}.")


GENERATED_MARKER = "Generated by local llm-wiki lint"
_ENTRY_LINE_RE = re.compile(r"^\s*(?:\||[-*+]\s|\d+[.)]\s)")
_LINK_HEADERS = {
    "file", "title", "article", "dataset", "output", "source", "project",
    "name", "entry", "page", "document",
}


def is_local_markdown_link(link: str) -> bool:
    if "://" in link or link.startswith("mailto:"):
        return False
    target = clean_link_destination(link).split("#", 1)[0]
    return target.endswith(".md")


def is_curated_index(text: str) -> bool:
    """True when an existing index holds content a regeneration would destroy.

    An index is generated, and may be regenerated wholesale, when it carries
    the generated marker or is a stub with nothing but headings. Anything else
    was written or edited by a person: ``lint --fix`` repairs it in place.
    """
    if GENERATED_MARKER in text:
        return False
    return any(
        line.strip() and not line.lstrip().startswith("#")
        for line in text.splitlines()
    )


def repair_directory_index(
    ctx: LintContext,
    directory: Path,
    contract: str,
    missing_entries: list[Path],
    dead_links: list[str],
) -> list[str]:
    """Fix an index for ``lint --fix`` and return the dead links left in it.

    A generated or stub index is regenerated. A curated one keeps every other
    line, in its order: each dead entry (a list item or table row whose local
    links all point at missing files) is removed and each missing entry is
    appended, as a row of the table that fits it when there is one. A dead link
    inside prose or beside live links is not touched and is returned so lint
    keeps reporting it.
    """
    index = directory / "_index.md"
    raw = index.read_bytes().decode("utf-8")
    if not is_curated_index(raw):
        regenerate_directory_index(ctx, directory, contract)
        return []

    crlf = "\r\n" in raw
    lines = [line.rstrip("\r") for line in raw.split("\n")]
    remaining = list(dead_links)
    kept: list[str] = []
    removed = 0
    for line in lines:
        destinations = [d for d in extract_link_destinations(line) if is_local_markdown_link(d)]
        if (
            destinations
            and _ENTRY_LINE_RE.match(line)
            and all(destination in remaining for destination in destinations)
        ):
            for destination in destinations:
                remaining.remove(destination)
            removed += 1
            continue
        kept.append(line)
    lines = kept

    for path in missing_entries:
        lines = _append_index_entry(ctx, directory, contract, lines, path)

    updated = ("\r\n" if crlf else "\n").join(lines)
    index.write_bytes(updated.encode("utf-8"))
    unplaced = [
        path for path in missing_entries
        if not index_links_to(updated, path.relative_to(directory).as_posix())
    ]
    for path in unplaced:
        ctx.issue(
            "warning",
            f"Required {index_contract_label(contract)} entry is missing from directory index.",
            path,
            check="C3",
        )
    added = len(missing_entries) - len(unplaced)
    changes = []
    if added:
        changes.append(f"added {added} missing {'entry' if added == 1 else 'entries'}")
    if removed:
        changes.append(f"removed {removed} dead {'entry' if removed == 1 else 'entries'}")
    ctx.fixed(
        f"Updated {index_contract_label(contract)} index {ctx.rel(index)} in place"
        + (f": {', '.join(changes)}." if changes else ".")
    )
    return remaining


def _append_index_entry(
    ctx: LintContext, directory: Path, contract: str, lines: list[str], path: Path
) -> list[str]:
    rel = path.relative_to(directory).as_posix()
    destination = markdown_link_destination(rel)

    if contract in ("raw-root", "wiki-root"):
        block = [f"## {path.parent.name.title()}", f"See [{rel}]({destination})", ""]
        for position, line in enumerate(lines):
            if line.strip() == "## Stats":
                return lines[:position] + block + lines[position:]
        return _append_lines(lines, block[:-1], blank_before=True)

    if path.name == "_index.md":
        bullet = f"- [{path.parent.name.title()}]({destination})"
        last = None
        for position, line in enumerate(lines):
            if re.match(r"^\s*[-*+]\s.*_index\.md\)", line):
                last = position
        if last is not None:
            return lines[: last + 1] + [bullet] + lines[last + 1 :]
        return _append_lines(lines, [bullet], blank_before=True)

    tables = _index_tables(lines)
    table = _pick_index_table(tables, path, contract)
    if table is None:
        title = _entry_title(ctx, path, contract, "title")
        return _append_lines(
            lines, [f"- [{table_cell(title)}]({destination})"], blank_before=True
        )
    _, end, headers = table
    link_column = next(
        (i for i, header in enumerate(headers) if header.lower() in _LINK_HEADERS), 0
    )
    cells = []
    for column, header in enumerate(headers):
        if column == link_column:
            title = _entry_title(ctx, path, contract, header.lower())
            cells.append(f"[{table_cell(title)}]({destination})")
        else:
            cells.append(table_cell(_entry_cell(ctx, path, header.lower())))
    row = "| " + " | ".join(cells) + " |"
    return lines[:end] + [row] + lines[end:]


def _append_lines(lines: list[str], new: list[str], blank_before: bool) -> list[str]:
    final_newline = bool(lines) and lines[-1] == ""
    body = lines[:-1] if final_newline else list(lines)
    if blank_before and body and body[-1].strip():
        body.append("")
    return body + new + ([""] if final_newline else [])


def _table_cells(line: str) -> list[str]:
    stripped = line.strip()
    stripped = stripped[1:] if stripped.startswith("|") else stripped
    stripped = stripped[:-1] if stripped.endswith("|") and not stripped.endswith("\\|") else stripped
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", stripped)]


def _index_tables(lines: list[str]) -> list[tuple[int, int, list[str]]]:
    """Return (start, end, headers) for each pipe table: a header, a rule, rows."""
    tables: list[tuple[int, int, list[str]]] = []
    position = 0
    while position + 1 < len(lines):
        rule = lines[position + 1].strip()
        if (
            lines[position].lstrip().startswith("|")
            and rule.startswith("|")
            and set(rule) <= set("|-: ")
            and "-" in rule
        ):
            end = position + 2
            while end < len(lines) and lines[end].lstrip().startswith("|"):
                end += 1
            tables.append((position, end, _table_cells(lines[position])))
            position = end
        else:
            position += 1
    return tables


def _pick_index_table(
    tables: list[tuple[int, int, list[str]]], path: Path, contract: str
) -> tuple[int, int, list[str]] | None:
    if not tables:
        return None
    if contract == "output-root":
        wanted = "project" if path.name == "WHY.md" else "output"
        for table in tables:
            if wanted in table[2][0].lower():
                return table
    return tables[-1]


def _entry_title(ctx: LintContext, path: Path, contract: str, header: str) -> str:
    if contract == "inventory-root" and header == "file":
        return path.name
    if path.name == "WHY.md":
        text = path.read_text(encoding="utf-8", errors="replace")
        heading = re.search(r"(?m)^#\s+(.+)$", text)
        return heading.group(1) if heading else path.parent.name
    title = index_frontmatter(ctx, path).get("title")
    if title:
        return str(title)
    return path.parent.name if path.name == "MANIFEST.md" else path.stem


def _entry_cell(ctx: LintContext, path: Path, header: str) -> str:
    """The value a curated table's column takes for a new row; unknown columns stay empty."""
    if header == "goal":
        return first_body_summary(path.read_text(encoding="utf-8", errors="replace"))
    frontmatter = index_frontmatter(ctx, path)
    if header in ("date", "updated", "last updated"):
        return index_updated(frontmatter)
    if header == "tags":
        return ", ".join(as_string_list(frontmatter.get("tags")))
    if header == "formats":
        return ", ".join(as_string_list(frontmatter.get("formats")))
    if header == "type":
        return str(frontmatter.get("type") or "artifact")
    if header in ("size", "records"):
        return str(frontmatter.get(header) or "unknown")
    key = "next_action" if header == "next action" else header
    if key in ("summary", "kind", "status", "priority", "storage", "next_action"):
        return str(frontmatter.get(key) or "")
    return ""


def render_category_pointer_index(ctx: LintContext, directory: Path, contract: str) -> str:
    title = "Raw Sources Index" if contract == "raw-root" else "Wiki Articles"
    categories = RAW_INDEX_CATEGORIES if contract == "raw-root" else WIKI_INDEX_CATEGORIES
    lines = [f"# {title}", ""]
    for category in categories:
        index = directory / category / "_index.md"
        if not index.exists():
            continue
        lines.extend([
            f"## {category.title()}",
            f"See [{category}/_index.md]({category}/_index.md)",
            "",
        ])
    if contract == "raw-root":
        count = len([
            path for category in RAW_INDEX_CATEGORIES
            for path in (directory / category).glob("*.md")
            if path.name != "_index.md"
        ])
        lines.extend(["## Stats", f"- Total sources: {count}", f"- Last updated: {today_date().isoformat()}", ""])
    return "\n".join(lines)


def render_inventory_index(ctx: LintContext, directory: Path, contract: str) -> str:
    records = inventory_record_files(directory)
    counts = {
        name: len([path for path in records if path.parent.name == name])
        for name in INVENTORY_RECORD_DIRS
    }
    statuses = [str(index_frontmatter(ctx, path).get("status") or "") for path in records]
    lines = [
        "# Inventory Index",
        "",
        "> Durable tracking records for items, candidates, entities, corpora, and watch items.",
        "",
        f"Last updated: {today_date().isoformat()}",
        "",
        "## Statistics",
        "",
        f"- Total records: {len(records)}",
        f"- Items: {counts['items']}",
        f"- Ideas: {counts['ideas']}",
        f"- Candidates: {counts['candidates']}",
        f"- Entities: {counts['entities']}",
        f"- Corpora: {counts['corpora']}",
        f"- Active: {statuses.count('active')}",
        f"- Blocked: {statuses.count('blocked')}",
        "",
        "## Quick Navigation",
        "",
    ]
    for name in INVENTORY_NAVIGATION_DIRS:
        index = directory / name / "_index.md"
        if index.exists():
            lines.append(f"- [{name.title()}]({name}/_index.md)")
    lines.extend(["", "## Contents", "", "| File | Kind | Status | Priority | Next Action | Updated |", "|------|------|--------|----------|-------------|---------|"])
    for path in records:
        frontmatter = index_frontmatter(ctx, path)
        link = path.relative_to(directory).as_posix()
        lines.append(
            f"| [{table_cell(path.name)}]({markdown_link_destination(link)}) | "
            f"{table_cell(str(frontmatter.get('kind') or ''))} | "
            f"{table_cell(str(frontmatter.get('status') or ''))} | "
            f"{table_cell(str(frontmatter.get('priority') or ''))} | "
            f"{table_cell(str(frontmatter.get('next_action') or ''))} | "
            f"{table_cell(index_updated(frontmatter))} |"
        )
    return "\n".join(lines) + "\n"


def render_dataset_registry_index(ctx: LintContext, directory: Path, contract: str) -> str:
    manifests = sorted(directory.glob("*/MANIFEST.md"))
    statuses = [str(index_frontmatter(ctx, path).get("status") or "") for path in manifests]
    lines = [
        "# Dataset Registry Index",
        "",
        "> Dataset manifests for large or external data indexed by this wiki.",
        "",
        f"Last updated: {today_date().isoformat()}",
        "",
        "## Statistics",
        "",
        f"- Datasets: {len(manifests)}",
        f"- Active: {statuses.count('active')}",
        f"- External: {statuses.count('external')}",
        f"- Unavailable: {statuses.count('unavailable')}",
        "",
        "## Contents",
        "",
        "| Dataset | Status | Storage | Formats | Size | Records | Updated |",
        "|---------|--------|---------|---------|------|---------|---------|",
    ]
    for path in manifests:
        frontmatter = index_frontmatter(ctx, path)
        link = path.relative_to(directory).as_posix()
        formats = ", ".join(as_string_list(frontmatter.get("formats")))
        lines.append(
            f"| [{table_cell(str(frontmatter.get('title') or path.parent.name))}]({markdown_link_destination(link)}) | "
            f"{table_cell(str(frontmatter.get('status') or ''))} | "
            f"{table_cell(str(frontmatter.get('storage') or ''))} | {table_cell(formats)} | "
            f"{table_cell(str(frontmatter.get('size') or 'unknown'))} | "
            f"{table_cell(str(frontmatter.get('records') or 'unknown'))} | "
            f"{table_cell(index_updated(frontmatter))} |"
        )
    return "\n".join(lines) + "\n"


def render_output_index(ctx: LintContext, directory: Path, contract: str) -> str:
    lines = ["# Output Artifacts", "", "## Outputs", "", "| Output | Type | Date |", "|--------|------|------|"]
    for path in output_loose_files(directory):
        frontmatter = index_frontmatter(ctx, path)
        lines.append(
            f"| [{table_cell(str(frontmatter.get('title') or path.stem))}]({markdown_link_destination(path.name)}) | "
            f"{table_cell(str(frontmatter.get('type') or 'artifact'))} | {table_cell(index_updated(frontmatter))} |"
        )
    project_files = output_project_why_files(directory)
    if project_files:
        lines.extend(["", "## Projects", "", "| Project | Goal |", "|---------|------|"])
        for path in project_files:
            text = path.read_text(encoding="utf-8", errors="replace")
            title_match = re.search(r"(?m)^#\s+(.+)$", text)
            title = title_match.group(1) if title_match else path.parent.name
            lines.append(
                f"| [{table_cell(title)}]({markdown_link_destination(path.relative_to(directory).as_posix())}) | "
                f"{table_cell(first_body_summary(text))} |"
            )
    return "\n".join(lines) + "\n"


def render_dataset_index(ctx: LintContext, directory: Path, contract: str) -> str:
    lines = render_direct_file_index(ctx, directory, contract).rstrip().splitlines()
    navigation = [
        name for name in ("samples", "profiles", "queries")
        if (directory / name / "_index.md").exists()
    ]
    if navigation:
        lines.extend(["", "## Quick Navigation", ""])
        lines.extend(f"- [{name.title()}]({name}/_index.md)" for name in navigation)
    return "\n".join(lines) + "\n"


def render_direct_file_index(ctx: LintContext, directory: Path, contract: str) -> str:
    rows: list[str] = []
    for path in index_contract_entries(directory, "direct-files"):
        frontmatter = index_frontmatter(ctx, path)
        rows.append(
            f"| [{table_cell(str(frontmatter.get('title') or path.stem))}]({markdown_link_destination(path.name)}) | "
            f"{table_cell(str(frontmatter.get('summary') or ''))} | "
            f"{table_cell(', '.join(as_string_list(frontmatter.get('tags'))))} | "
            f"{table_cell(index_updated(frontmatter))} |"
        )
    return (
        f"# {index_title(directory)}\n\n"
        f"> {GENERATED_MARKER}.\n\n"
        f"Last updated: {today_date().isoformat()}\n\n"
        "## Contents\n\n"
        "| File | Summary | Tags | Updated |\n"
        "|------|---------|------|---------|\n"
        + "\n".join(rows)
        + ("\n" if rows else "")
    )


def index_updated(frontmatter: dict[str, Any]) -> str:
    return str(
        frontmatter.get("updated")
        or frontmatter.get("ingested")
        or frontmatter.get("created")
        or ""
    )


def index_title(directory: Path) -> str:
    name = directory.name
    if name == "wiki":
        return "Wiki Index"
    if name == "raw":
        return "Raw Index"
    return name.replace("-", " ").replace("_", " ").title() + " Index"
