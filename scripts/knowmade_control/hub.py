"""Hub-level namespaces: ``archive``, plus the ``schema`` and ``identity`` wiring.

Topic archive/restore with the atomic registry write, the hub ``_index.md``
writer, and the git-ignore helpers for private topics that both ``archive``
and the hub lint checks use. The ``schema`` and ``identity`` namespaces are
implemented by the bounded ``schema`` and ``identity`` modules; their
argparse wiring is defined here (``register_archive_parser``,
``register_schema_parser``, ``register_identity_parser``) and listed in
``cli.NAMESPACES``. Moved verbatim from ``legacy_cli`` (emercons/knowmade#67
R1.4, ``KM-CLI-001``).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import fsio as _fsio
from . import identity as _identity
from .common import (
    PRIVATE_SUFFIX,
    add_scope_arguments,
    active_topic_path,
    archived_topic_path,
    is_under,
    markdown_link_destination,
    read_registry,
    table_cell,
    topic_entry,
    write_registry,
)
from .markdown import (
    append_log,
    append_text_preserving,
    sniff_style,
    today_date,
    topic_title,
    write_target,
)
from .roots import (
    initialized_wiki_root,
    is_archived_registry_entry,
    resolve_hub_args as resolve_hub,
    resolve_registry_path,
    scoped_wiki_slug_args as scoped_wiki_slug,
)
from .schema import run_schema


# Canonical hub .gitignore rule for private topics. `*` never matches `/` in
# gitignore, so the older `topics/*-PRIVATE/` form stops ignoring a private
# topic the moment `archive topic` moves it under `topics/.archive/`. The `**`
# form covers both the live and the archived location.
PRIVATE_IGNORE_RULE = "topics/**/*-PRIVATE/"

# Machine-local state the engine and its workflows write inside a hub or wiki
# tree (knowmade#98): session capture state with cwd and transcript paths, the
# specialist registry with private topic slugs, audit/librarian run state,
# per-wiki session files, editor state, and local source originals. Each entry
# is (gitignore rule, git pathspec for the same files, probe paths that the
# rule must ignore). The probes are hypothetical files: `check-ignore` matches
# patterns, so nothing has to exist.
STATE_IGNORE_COMMENT = "Knowmade machine-local state (sessions, registries, run state, local originals)"
STATE_IGNORE_RULES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("/.sessions/", ":(glob).sessions/**", (".sessions/state/knowmade-ignore-probe.json",)),
    ("/.skills/registry.json", ":(glob).skills/registry.json", (".skills/registry.json",)),
    ("**/.audit/", ":(glob)**/.audit/**", (".audit/probe.json", "topics/knowmade-ignore-probe/.audit/probe.json")),
    ("**/.librarian/", ":(glob)**/.librarian/**", (".librarian/probe.json", "topics/knowmade-ignore-probe/.librarian/probe.json")),
    ("**/.session-events.jsonl", ":(glob)**/.session-events.jsonl", (".session-events.jsonl", "topics/knowmade-ignore-probe/.session-events.jsonl")),
    ("**/.session-checkpoint.json", ":(glob)**/.session-checkpoint.json", (".session-checkpoint.json", "topics/knowmade-ignore-probe/.session-checkpoint.json")),
    ("**/.obsidian/", ":(glob)**/.obsidian/**", (".obsidian/workspace.json", "topics/knowmade-ignore-probe/.obsidian/workspace.json")),
    ("**/.wiki-local/", ":(glob)**/.wiki-local/**", (".wiki-local/originals/probe.bin", "topics/knowmade-ignore-probe/.wiki-local/originals/probe.bin")),
)


def is_git_repository_root(path: Path) -> bool:
    """True when ``path`` is a git repository root (``.git`` dir or worktree file).

    Kept for compatibility callers. The privacy guards use
    :func:`is_inside_git_work_tree`, because a hub nested in a larger
    repository is just as committable as one at the root (knowmade#87).
    """
    return (path / ".git").exists()


def _ancestor_has_git(path: Path) -> bool:
    """True when ``path`` or any ancestor carries a ``.git`` entry."""
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    return any((candidate / ".git").exists() for candidate in (resolved, *resolved.parents))


def is_inside_git_work_tree(path: Path) -> bool:
    """True when ``path`` lies inside a git work tree, at its root or nested.

    Git decides (``rev-parse --is-inside-work-tree`` run from ``path``), so a
    hub inside a dotfiles or project repository is judged like a repository
    root (knowmade#87). When git is not installed, or cannot evaluate the
    repository (for example a ``safe.directory`` refusal), a ``.git`` entry in
    ``path`` or any ancestor counts as inside: the guards then fail safe rather
    than treating a committable hub as a plain directory.
    """
    if not path.is_dir():
        return False
    git = shutil.which("git")
    if git is not None:
        try:
            result = subprocess.run(
                [git, *_fsio.GIT_READ_OPTIONS, "rev-parse", "--is-inside-work-tree"],
                cwd=str(path),
                env=_fsio.git_environment(),
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            result = None
        if result is not None:
            if result.returncode == 0:
                return result.stdout.strip() == "true"
            if "not a git repository" in (result.stderr or "").lower():
                return False
    return _ancestor_has_git(path)


def git_ignores_path(repo: Path, relative_path: str) -> bool | None:
    """Ask git whether ``relative_path`` (relative to ``repo``) is ignored.

    Returns ``True``/``False`` when git answered, or ``None`` when git is not
    installed or could not evaluate the repository. The path does not have to
    exist: ``check-ignore`` matches patterns, so a destination that is about to
    be created can be probed before anything is moved.
    """
    git = shutil.which("git")
    if git is None:
        return None
    try:
        result = subprocess.run(
            [git, *_fsio.GIT_READ_OPTIONS, "check-ignore", "-q", "--", relative_path],
            cwd=str(repo),
            env=_fsio.git_environment(),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


def gitignore_declares_private_rule(root: Path) -> bool:
    """True when ``root/.gitignore`` literally contains ``PRIVATE_IGNORE_RULE``."""
    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        return False
    try:
        lines = gitignore.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError):
        return False
    return any(line.strip() == PRIVATE_IGNORE_RULE for line in lines)


def hub_ignores_private_topic(hub: Path, relative_topic_path: str) -> bool:
    """True when a git hub ignores the private topic at ``relative_topic_path``.

    Probes a file inside the topic so directory-only patterns apply even before
    the directory exists. Falls back to the literal canonical rule when git
    cannot be consulted.
    """
    answer = git_ignores_path(hub, f"{relative_topic_path}/_index.md")
    if answer is None:
        return gitignore_declares_private_rule(hub)
    return answer


def private_ignore_repair_hint(hub: Path) -> str:
    return (
        f"add `{PRIVATE_IGNORE_RULE}` to {hub / '.gitignore'} "
        f"or run `llm-wiki lint --fix {hub}`"
    )


def append_ignore_block(root: Path, comment: str, rules: list[str]) -> None:
    """Append ``# comment`` and ``rules`` to ``root/.gitignore``.

    The existing content stays byte for byte; the appended lines use the file's
    own newline style (LF for a new file). A file without a final newline gets
    one first, so the block never fuses with the last existing rule.
    """
    gitignore = root / ".gitignore"
    lead = ""
    if gitignore.is_file():
        existing = gitignore.read_bytes()
        if existing and not existing.endswith(b"\n"):
            lead = "\n"
    block = f"# {comment}\n" + "".join(f"{rule}\n" for rule in rules)
    append_text_preserving(gitignore, lead + block)


def append_private_ignore_rule(root: Path) -> None:
    """Append the canonical private-topic rule to ``root/.gitignore``."""
    append_ignore_block(
        root,
        "Knowmade private topic wikis, live and archived "
        "(topics/<slug>-PRIVATE/ and topics/.archive/<slug>-PRIVATE/)",
        [PRIVATE_IGNORE_RULE],
    )


def gitignore_rules(root: Path) -> set[str]:
    """The stripped, non-comment lines of ``root/.gitignore`` (empty when unreadable)."""
    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        return set()
    try:
        lines = gitignore.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeDecodeError):
        return set()
    return {line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")}


def missing_state_ignore_rules(root: Path) -> list[str]:
    """The machine-state rules (``STATE_IGNORE_RULES``) that ``root`` does not ignore yet.

    Git answers per probe path, so an equivalent rule the user already wrote
    (or one in an outer repository's ``.gitignore``) counts. When git cannot be
    consulted, only the literal rule in ``root/.gitignore`` counts.
    """
    literal = gitignore_rules(root)
    missing: list[str] = []
    for rule, _pathspec, probes in STATE_IGNORE_RULES:
        answers = [git_ignores_path(root, probe) for probe in probes]
        if any(answer is None for answer in answers):
            covered = rule in literal
        else:
            covered = all(answers)
        if not covered:
            missing.append(rule)
    return missing


def ensure_state_ignore_rules(root: Path) -> list[str]:
    """Append the machine-state rules ``root`` is missing; returns what was appended.

    Idempotent: a second call finds every probe ignored and appends nothing.
    Callers decide whether ``root`` is in a git work tree; a plain directory
    gains a harmless ``.gitignore`` for the day it becomes one.
    """
    missing = missing_state_ignore_rules(root)
    if missing:
        append_ignore_block(root, STATE_IGNORE_COMMENT, missing)
    return missing


def state_ignore_repair_hint(root: Path) -> str:
    return (
        f"add the machine-state rules ({', '.join(rule for rule, _, _ in STATE_IGNORE_RULES)}) "
        f"to {root / '.gitignore'} or run `llm-wiki lint --fix {root}`"
    )


def _git_ls_files(root: Path, extra: list[str]) -> list[str] | None:
    git = shutil.which("git")
    if git is None:
        return None
    pathspecs = [pathspec for _rule, pathspec, _probes in STATE_IGNORE_RULES]
    try:
        result = subprocess.run(
            [git, *_fsio.GIT_READ_OPTIONS, "ls-files", "-z", *extra, "--", *pathspecs],
            cwd=str(root),
            env=_fsio.git_environment(),
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return sorted(
        item.decode("utf-8", errors="replace")
        for item in result.stdout.split(b"\0")
        if item
    )


def machine_state_files(root: Path) -> tuple[list[str], list[str]] | None:
    """``(tracked, committable)`` machine-state files under ``root``, or None without git.

    ``tracked`` are already in the index; ``committable`` are untracked files
    that no ignore rule covers, so a plain ``git add -A`` would take them. Paths
    are relative to ``root`` (git runs from there, so a hub nested in a larger
    repository is judged on its own subtree).
    """
    tracked = _git_ls_files(root, [])
    if tracked is None:
        return None
    committable = _git_ls_files(root, ["--others", "--exclude-standard"]) or []
    return tracked, committable
TOPICS_START = "<!-- knowmade:topics:start -->"
TOPICS_END = "<!-- knowmade:topics:end -->"
STATS_START = "<!-- knowmade:stats:start -->"
STATS_END = "<!-- knowmade:stats:end -->"
DEFAULT_TOPIC_COLUMNS = ("Wiki", "Title", "Description")
_BOM_BYTES = b"\xef\xbb\xbf"
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")


def _split_row(line: str) -> list[str]:
    """The cells of one Markdown table row (an unescaped pipe separates cells)."""
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", body)]


def _row_slug(line: str) -> str:
    cells = _split_row(line)
    first = cells[0] if cells else ""
    link = re.match(r"\[([^\]]*)\]", first)
    return (link.group(1) if link else first).replace("\\|", "|").strip("`*_ ")


def _find_topic_table(lines: list[str], start: int, stop: int) -> tuple[int, int] | None:
    """``(first, end)`` indexes of the topic table in ``lines[start:stop]``.

    The topic table is the first run of ``|`` rows whose header names a
    ``Wiki`` (or ``Slug``) column and is followed by a separator row; ``end``
    is exclusive.
    """
    for index in range(start, stop - 1):
        if not lines[index].lstrip().startswith("|"):
            continue
        if not _TABLE_SEPARATOR.match(lines[index + 1].rstrip("\r\n")):
            continue
        cells = _split_row(lines[index])
        if cells and cells[0].strip("`*_ ").lower() in {"wiki", "slug"}:
            end = index + 2
            while end < stop and lines[end].lstrip().startswith("|"):
                end += 1
            return index, end
    return None


def _topic_row(columns: list[str], slug: str, entry: tuple[str, str, str], today: str) -> str:
    raw_path, title, description = entry
    cells = []
    for column in columns:
        name = column.strip("`*_ ").lower()
        if name in {"wiki", "slug"}:
            cells.append(f"[{table_cell(slug)}]({markdown_link_destination(raw_path)})")
        elif name == "title":
            cells.append(table_cell(title))
        elif name in {"description", "summary"}:
            cells.append(table_cell(description))
        elif name == "path":
            cells.append(f"`{table_cell(raw_path)}`")
        elif name in {"updated", "last updated"}:
            cells.append(today)
        else:
            cells.append("")
    return "| " + " | ".join(cells) + " |"


_IDENTITY_FREE_COLUMNS = {"title", "description", "summary"}


def _scrub_private_row(columns: list[str], row: str) -> str:
    """``row`` with its title/description/summary cells emptied.

    A private topic is listed by slug only (knowmade#90). A row written by an
    earlier engine, or by hand, may still carry text copied out of the private
    topic; every other cell and the row's line ending stay as they were, and a
    row with nothing to scrub is returned untouched.
    """
    ending = row[len(row.rstrip("\r\n")) :]
    cells = _split_row(row)
    changed = False
    for position, column in enumerate(columns[: len(cells)]):
        if column.strip("`*_ ").lower() in _IDENTITY_FREE_COLUMNS and cells[position]:
            cells[position] = ""
            changed = True
    return ("| " + " | ".join(cells) + " |" + ending) if changed else row


def _merge_topic_table(
    existing: list[str],
    newline: str,
    active: dict[str, tuple[str, str, str]],
    archived: set[str],
    today: str,
) -> list[str]:
    """The topic table as lines, keeping every curated row of ``existing``.

    Header, separator and the rows of still-active slugs are kept byte for
    byte (so curated summaries and extra columns survive); rows of archived
    slugs are dropped; rows of slugs the registry does not know are curated and
    stay. An active slug without a row gets one built column by column from the
    header. A ``-PRIVATE`` slug is the exception to byte preservation: its row
    never carries a title, description or summary, so an existing one is emptied
    (knowmade#90).
    """
    if existing:
        header, separator, rows = existing[0], existing[1], existing[2:]
        columns = _split_row(header)
    else:
        columns = list(DEFAULT_TOPIC_COLUMNS)
        header = "| " + " | ".join(columns) + " |" + newline
        separator = "|" + "|".join("-" * (len(name) + 2) for name in columns) + "|" + newline
        rows = []
    kept = [row for row in rows if _row_slug(row) not in archived]
    kept = [
        _scrub_private_row(columns, row) if _row_slug(row).endswith(PRIVATE_SUFFIX) else row
        for row in kept
    ]
    present = {_row_slug(row) for row in kept}
    kept.extend(
        _topic_row(columns, slug, entry, today) + newline
        for slug, entry in active.items()
        if slug not in present
    )
    merged = [header, separator, *kept]
    # A row that ended the file without a newline is no longer last.
    return [
        line if line.endswith(("\n", "\r")) or position == len(merged) - 1 else line + newline
        for position, line in enumerate(merged)
    ]


def _refresh_stats(lines: list[str], active: int, archived: int, today: str) -> None:
    """Refresh the counts inside ``knowmade:stats`` markers (the template's own)."""
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == STATS_START)
        stop = next(i for i, line in enumerate(lines) if i > start and line.strip() == STATS_END)
    except StopIteration:
        return
    for index in range(start + 1, stop):
        ending = lines[index][len(lines[index].rstrip("\r\n")) :]
        text = lines[index].rstrip("\r\n")
        if re.fullmatch(r"- Active topics: \d+", text):
            lines[index] = f"- Active topics: {active}{ending}"
        elif re.fullmatch(r"- Archived topics: \d+", text):
            lines[index] = f"- Archived topics: {archived}{ending}"
        elif re.fullmatch(r"Last updated: \d{4}-\d{2}-\d{2}", text):
            lines[index] = f"Last updated: {today}{ending}"


def _locate_topic_table(
    lines: list[str],
) -> tuple[int | None, int | None, tuple[int, int] | None]:
    """``(first, end, found)`` of the generated topic table in ``lines``.

    Between ``knowmade:topics`` markers when both are present (an empty marked
    block yields ``first == end`` and ``found`` None), otherwise the first table
    with a ``Wiki`` header anywhere; ``(None, None, None)`` when there is none.
    """
    start = next((i for i, line in enumerate(lines) if line.strip() == TOPICS_START), None)
    stop = None
    if start is not None:
        stop = next((i for i, line in enumerate(lines) if i > start and line.strip() == TOPICS_END), None)
    if start is not None and stop is not None:
        found = _find_topic_table(lines, start + 1, stop)
        first, end = found if found else (start + 1, start + 1)
        return first, end, found
    found = _find_topic_table(lines, 0, len(lines))
    return (found[0], found[1], found) if found else (None, None, None)


def topic_table_rows(text: str) -> list[str]:
    """The data rows of the generated topic table in ``text`` (header excluded)."""
    lines = text.splitlines(keepends=True)
    _first, _end, found = _locate_topic_table(lines)
    if not found:
        return []
    return lines[found[0] + 2 : found[1]]


def topic_row_slug(row: str) -> str:
    """The slug a topic-table row lists (the link text of its first cell)."""
    return _row_slug(row)


def stats_last_updated(text: str) -> str | None:
    """The ``Last updated`` date inside the ``knowmade:stats`` markers, if any."""
    inside = False
    for line in text.splitlines():
        if line.strip() == STATS_START:
            inside = True
        elif line.strip() == STATS_END:
            return None
        elif inside:
            match = re.fullmatch(r"Last updated: (\d{4}-\d{2}-\d{2})", line.strip())
            if match:
                return match.group(1)
    return None


def refresh_index_text(
    text: str,
    newline: str,
    active: dict[str, tuple[str, str, str]],
    archived: set[str],
    today: str,
) -> str:
    """``text`` with only its generated topic table (and marked counts) updated."""
    lines = text.splitlines(keepends=True)
    first, end, found = _locate_topic_table(lines)
    if first is None:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += newline
        block = [
            newline,
            f"## Topic Wikis{newline}",
            newline,
            f"{TOPICS_START}{newline}",
            *_merge_topic_table([], newline, active, archived, today),
            f"{TOPICS_END}{newline}",
        ]
        if not lines:
            block = block[1:]
        lines.extend(block)
    else:
        existing = lines[first:end] if found else []
        lines[first:end] = _merge_topic_table(existing, newline, active, archived, today)
    _refresh_stats(lines, len(active), len(archived), today)
    return "".join(lines)


def hub_index_entries(
    hub: Path, data: dict[str, Any]
) -> tuple[dict[str, tuple[str, str, str]], set[str]]:
    """``(active, archived)`` registry topics as the hub index lists them.

    ``active`` maps a slug to ``(registry path, title, description)``; a
    ``-PRIVATE`` slug carries empty title and description and its
    ``config.md`` is never read (knowmade#90).
    """
    active: dict[str, tuple[str, str, str]] = {}
    archived: set[str] = set()
    for slug, entry in sorted(data.get("wikis", {}).items()):
        if slug == "hub" or not isinstance(entry, dict):
            continue
        if is_archived_registry_entry(entry):
            archived.add(slug)
            continue
        raw_path = str(entry.get("path") or f"topics/{slug}")
        if slug.endswith(PRIVATE_SUFFIX):
            # The index is committed with the hub: a private topic is listed by
            # slug only, and its own config.md is never read (knowmade#90).
            active[slug] = (raw_path, "", "")
            continue
        title = topic_title(resolve_registry_path(raw_path, hub), slug)
        active[slug] = (raw_path, title, str(entry.get("description") or ""))
    return active, archived


_INDEX_TEMPLATE = (
    "# Wiki Hub Index",
    "",
    "> Registry of active topic wikis.",
    "",
    "## Statistics",
    "",
    STATS_START,
    "Last updated: 1970-01-01",
    "",
    "- Active topics: 0",
    "- Archived topics: 0",
    STATS_END,
    "",
    "## Topic Wikis",
    "",
    TOPICS_START,
    TOPICS_END,
    "",
    "## Archive",
    "",
    "Archived topics are preserved under `topics/.archive/`.",
    "",
)


def render_hub_index(
    hub: Path, data: dict[str, Any], today: str | None = None
) -> tuple[Path, bytes | None, bytes]:
    """``(target, current, refreshed)`` bytes of ``hub/_index.md``.

    ``current`` is None when the index does not exist; ``refreshed`` is what
    :func:`write_hub_index` writes, BOM and newline style included. ``today``
    dates the statistics and new rows (default: today).
    """
    today = today or today_date().isoformat()
    active, archived = hub_index_entries(hub, data)
    target = hub / "_index.md"
    newline, has_bom = sniff_style(target)
    current: bytes | None = None
    if target.is_file():
        current = target.read_bytes()
        body = current[len(_BOM_BYTES) :] if current.startswith(_BOM_BYTES) else current
        text = body.decode("utf-8")
    else:
        text = newline.join(_INDEX_TEMPLATE)
    payload = refresh_index_text(text, newline, active, archived, today).encode("utf-8")
    return target, current, (_BOM_BYTES + payload) if has_bom else payload


def write_hub_index(hub: Path, data: dict[str, Any]) -> bool:
    """Refresh the topic table of ``hub/_index.md`` and nothing else.

    Only the generated table changes: the one between ``knowmade:topics``
    markers when present, otherwise the table found by its ``Wiki`` header row.
    ``-PRIVATE`` topics are listed by slug only: no title, description or
    summary, and their ``config.md`` is not read (knowmade#90).
    Curated sections, extra columns and rows, the BOM and the file's newline
    style are kept byte for byte. A missing ``_index.md`` gets the template; an
    index without any table gets the marked table appended. ``archive topic``
    and ``archive restore`` both call this, and so does ``hub index --write``
    (:func:`render_hub_index` is the shared renderer), so they cannot diverge.
    Returns whether the file changed; an index already current is not written.
    """
    target, current, payload = render_hub_index(hub, data)
    if current == payload:
        return False
    _fsio.atomic_write_bytes(write_target(target), payload)
    return True


def ensure_hub_for_archive(hub: Path) -> dict[str, Any]:
    if not (hub / "wikis.json").exists() or not (hub / "topics").exists():
        raise SystemExit(f"initialized hub not found: {hub}")
    return read_registry(hub)


def run_archive(args: argparse.Namespace) -> int:
    if args.private and args.public:  # one before the subcommand, one after
        raise SystemExit("llm-wiki archive: --private and --public are mutually exclusive")
    hub = resolve_hub(args)
    data = ensure_hub_for_archive(hub)
    subcommand = args.archive_command
    if subcommand == "list":
        return archive_list(hub, data, include_archived=args.archived, json_output=args.json)
    if subcommand == "topic":
        return archive_topic(hub, data, scoped_wiki_slug(args, args.slug), args.reason)
    if subcommand == "restore":
        return restore_topic(hub, data, scoped_wiki_slug(args, args.slug))
    raise SystemExit("unknown archive subcommand")


def archive_list(
    hub: Path,
    data: dict[str, Any],
    include_archived: bool,
    json_output: bool = False,
) -> int:
    active: list[dict[str, str]] = []
    archived: list[dict[str, str]] = []
    for slug, entry in sorted(data.get("wikis", {}).items()):
        if slug == "hub" or not isinstance(entry, dict):
            continue
        path_text = str(entry.get("path") or f"topics/{slug}")
        row = {
            "slug": slug,
            "path": path_text,
            "description": str(entry.get("description") or ""),
            "archived": str(entry.get("archived") or ""),
            "reason": str(entry.get("archive_reason") or ""),
        }
        if is_archived_registry_entry(entry):
            archived.append(row)
        else:
            active.append(row)

    archive_dir = hub / "topics" / ".archive"
    if archive_dir.exists():
        registered = {row["slug"] for row in archived}
        for topic in sorted(child for child in archive_dir.iterdir() if child.is_dir()):
            if topic.name not in registered and initialized_wiki_root(topic):
                archived.append(
                    {
                        "slug": topic.name,
                        "path": f"topics/.archive/{topic.name}",
                        "description": "(missing from registry)",
                        "archived": "",
                        "reason": "registry repair needed",
                    }
                )

    if json_output:
        print(json.dumps({"hub": str(hub), "active": active, "archived": archived}, indent=2))
        return 0

    print(f"Knowmade archive: {hub}")
    print(f"\nActive topics ({len(active)})")
    print("| Slug | Path | Description |")
    print("|------|------|-------------|")
    for row in active:
        print(f"| {row['slug']} | {row['path']} | {table_cell(row['description'])} |")
    print(f"\nArchived topics: {len(archived)}")
    if include_archived:
        print("\nArchived topics")
        print("| Slug | Path | Archived | Reason |")
        print("|------|------|----------|--------|")
        for row in archived:
            print(
                f"| {row['slug']} | {row['path']} | {row['archived']} | {table_cell(row['reason'])} |"
            )
    elif archived:
        print("Run `llm-wiki archive list --archived` to show archived topics.")
    return 0


def archive_topic(hub: Path, data: dict[str, Any], slug: str, reason: str | None) -> int:
    if slug == "hub":
        raise SystemExit("cannot archive the synthetic hub entry")
    entry = topic_entry(data, slug)
    source = active_topic_path(hub, slug, entry)
    fallback_source = hub / "topics" / slug
    if not initialized_wiki_root(source) and initialized_wiki_root(fallback_source):
        source = fallback_source
    if entry and is_archived_registry_entry(entry):
        raise SystemExit(f"topic already archived: {slug}")
    if not initialized_wiki_root(source):
        raise SystemExit(f"active topic not found: {source}")
    topics_dir = hub / "topics"
    if not is_under(source, topics_dir):
        raise SystemExit(
            f"cannot archive {slug}: its path {source} is not under {topics_dir}. "
            "Archiving only moves hub-local topics; an externally stored topic stays "
            "where it is. Move it under topics/ and re-register it first."
        )
    archive_dir = topics_dir / ".archive"
    dest = archive_dir / slug
    if dest.exists():
        raise SystemExit(f"archive destination already exists: {dest}")
    if slug.endswith(PRIVATE_SUFFIX) and is_inside_git_work_tree(hub):
        if not hub_ignores_private_topic(hub, f"topics/.archive/{slug}"):
            raise SystemExit(
                f"refusing to archive private topic {slug}: {dest} is not ignored by "
                f"this hub's git configuration, so the archived private wiki would "
                f"become committable. {private_ignore_repair_hint(hub)}, then retry."
            )

    wikis = data.setdefault("wikis", {})
    if not isinstance(wikis, dict):
        raise SystemExit("invalid registry: wikis must be an object")

    archive_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(dest))

    entry = wikis.setdefault(slug, {})
    if not isinstance(entry, dict):
        entry = {}
        wikis[slug] = entry
    if not slug.endswith(PRIVATE_SUFFIX):
        entry.setdefault("description", topic_title(dest, slug))
    entry["path"] = f"topics/.archive/{slug}"
    entry["status"] = "archived"
    entry["archived"] = today_date().isoformat()
    if reason:
        entry["archive_reason"] = reason
    # The directory is moved first and the registry written second, so a crash
    # between the two leaves a stray archived directory that `archive list`
    # reports as "registry repair needed" rather than a registry entry pointing
    # at nothing. If the registry write itself fails, the rename is undone, so
    # the tree and wikis.json never disagree (knowmade#48).
    try:
        write_registry(hub, data)
    except BaseException:
        shutil.move(str(dest), str(source))
        raise
    write_hub_index(hub, data)
    append_log(hub, "archive", f"archived topic {slug}")
    append_log(dest, "archive", f"archived topic {slug}")
    print(f"Archived topic `{slug}`")
    print(f"Old path: {source}")
    print(f"New path: {dest}")
    print(f"Restore with: llm-wiki archive --hub {hub} restore {slug}")
    return 0


def restore_topic(hub: Path, data: dict[str, Any], slug: str) -> int:
    if slug == "hub":
        raise SystemExit("cannot restore the synthetic hub entry")
    entry = topic_entry(data, slug)
    source = archived_topic_path(hub, slug, entry)
    fallback_source = hub / "topics" / ".archive" / slug
    if not initialized_wiki_root(source) and initialized_wiki_root(fallback_source):
        source = fallback_source
    dest = hub / "topics" / slug
    if dest.exists():
        raise SystemExit(f"active topic destination already exists: {dest}")
    if not initialized_wiki_root(source):
        raise SystemExit(f"archived topic not found: {source}")
    if slug.endswith(PRIVATE_SUFFIX) and is_inside_git_work_tree(hub):
        if not hub_ignores_private_topic(hub, f"topics/{slug}"):
            raise SystemExit(
                f"refusing to restore private topic {slug}: {dest} is not ignored by "
                f"this hub's git configuration, so the restored private wiki would "
                f"become committable. {private_ignore_repair_hint(hub)}, then retry."
            )

    wikis = data.setdefault("wikis", {})
    if not isinstance(wikis, dict):
        raise SystemExit("invalid registry: wikis must be an object")

    shutil.move(str(source), str(dest))

    entry = wikis.setdefault(slug, {})
    if not isinstance(entry, dict):
        entry = {}
        wikis[slug] = entry
    if not slug.endswith(PRIVATE_SUFFIX):
        entry.setdefault("description", topic_title(dest, slug))
    entry["path"] = f"topics/{slug}"
    entry["status"] = "active"
    entry.pop("archived", None)
    entry.pop("archive_reason", None)
    entry["restored"] = today_date().isoformat()
    # Same ordering as archive_topic: move, then registry, undo the move if the
    # registry write fails (knowmade#48).
    try:
        write_registry(hub, data)
    except BaseException:
        shutil.move(str(dest), str(source))
        raise
    write_hub_index(hub, data)
    append_log(hub, "archive", f"restored topic {slug}")
    append_log(dest, "archive", f"restored topic {slug}")
    print(f"Restored topic `{slug}`")
    print(f"Path: {dest}")
    return 0


def register_archive_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Register ``archive`` on a parent ``llm-wiki`` parser; dispatch through :func:`run_archive`."""
    archive = subparsers.add_parser(
        "archive",
        help="Archive, restore, or list hub topic wikis.",
        description=(
            "Move whole topic wikis between topics/<slug> and topics/.archive/<slug>, "
            "updating wikis.json so archived topics stay out of default context."
        ),
    )
    archive.add_argument("--hub", help="Override hub path for archive operations.")
    archive_scope = archive.add_mutually_exclusive_group()
    archive_scope.add_argument(
        "--private",
        action="store_true",
        help=f"Resolve topic slugs to private siblings ending in {PRIVATE_SUFFIX}.",
    )
    archive_scope.add_argument(
        "--public",
        action="store_true",
        help=f"Normalize slugs ending in {PRIVATE_SUFFIX} to public siblings.",
    )
    archive_sub = archive.add_subparsers(dest="archive_command", required=True)

    archive_list_parser = archive_sub.add_parser("list", help="List active and archived topics.")
    archive_list_parser.add_argument(
        "--archived",
        action="store_true",
        help="Show archived topics in addition to active topics.",
    )
    archive_list_parser.add_argument("--json", action="store_true", help="Print JSON.")

    archive_topic_parser = archive_sub.add_parser("topic", help="Archive an active topic wiki.")
    archive_topic_parser.add_argument("slug", help="Topic slug to archive.")
    archive_topic_parser.add_argument("--reason", help="Optional archive reason.")

    archive_restore_parser = archive_sub.add_parser("restore", help="Restore an archived topic wiki.")
    archive_restore_parser.add_argument("slug", help="Topic slug to restore.")
    for writer in (archive_topic_parser, archive_restore_parser):
        writer.add_argument(
            "--require-cwd-hub",
            action="store_true",
            help="Refuse unless the selected hub is the hub enclosing the current directory.",
        )

    # The scope flags are accepted after the subcommand too (`archive list --hub X`),
    # as `schema` and `identity` do; hidden there so the reference lists them once.
    for child in (archive_list_parser, archive_topic_parser, archive_restore_parser):
        child.add_argument("--hub", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        child_scope = child.add_mutually_exclusive_group()
        for flag in ("--private", "--public"):
            child_scope.add_argument(flag, action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)

    archive.set_defaults(func=run_archive)
    return archive


def register_schema_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Register ``schema`` on a parent ``llm-wiki`` parser; dispatch through ``schema.run_schema``."""
    schema = subparsers.add_parser(
        "schema",
        help="Manage the topic guide (`schema.md`).",
        description=(
            "Topic guides are default for topic wikis. Use status to inspect a wiki, "
            "or adopt to add an advisory starter topic guide (`schema.md`)."
        ),
    )
    add_scope_arguments(
        schema,
        local_help="Target .wiki/ in the current directory.",
        public_help=f"Normalize --wiki <slug>{PRIVATE_SUFFIX} to the public sibling <slug>.",
        include_archived_help="Allow schema operations on an explicitly targeted archived wiki.",
    )
    schema.add_argument("--json", action="store_true", help="Print JSON.")
    schema_sub = schema.add_subparsers(dest="schema_command", required=True)

    schema_status = schema_sub.add_parser("status", help="Show topic-guide state.")
    schema_status.add_argument("path", nargs="?", help="Wiki root path.")
    schema_status.add_argument("--local", action="store_true", default=argparse.SUPPRESS, help="Target .wiki/ in the current directory.")
    schema_status.add_argument("--wiki", default=argparse.SUPPRESS, help="Named wiki from the hub registry.")
    schema_status.add_argument("--hub", default=argparse.SUPPRESS, help="Override hub path for --wiki resolution.")
    schema_status.add_argument(
        "--include-archived",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Allow schema operations on an explicitly targeted archived wiki.",
    )
    schema_status.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Print JSON.")

    schema_migrate_parser = schema_sub.add_parser(
        "migrate",
        help="Legacy alias: preview/write a starter topic guide, dry-run by default.",
    )
    schema_migrate_parser.add_argument("path", nargs="?", help="Wiki root path.")
    schema_migrate_parser.add_argument("--local", action="store_true", default=argparse.SUPPRESS, help="Target .wiki/ in the current directory.")
    schema_migrate_parser.add_argument("--wiki", default=argparse.SUPPRESS, help="Named wiki from the hub registry.")
    schema_migrate_parser.add_argument("--hub", default=argparse.SUPPRESS, help="Override hub path for --wiki resolution.")
    schema_migrate_parser.add_argument(
        "--include-archived",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Allow schema operations on an explicitly targeted archived wiki.",
    )
    schema_migrate_parser.add_argument("--apply", action="store_true", help="Write schema.md.")
    schema_migrate_parser.add_argument(
        "--state",
        choices=["advisory", "strict"],
        default="advisory",
        help="Initial schema_state for the generated schema.md.",
    )
    schema_migrate_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Print JSON.")

    schema_adopt_parser = schema_sub.add_parser(
        "adopt",
        help="Create a starter topic guide if schema.md is missing.",
    )
    schema_adopt_parser.add_argument("path", nargs="?", help="Wiki root path.")
    schema_adopt_parser.add_argument("--local", action="store_true", default=argparse.SUPPRESS, help="Target .wiki/ in the current directory.")
    schema_adopt_parser.add_argument("--wiki", default=argparse.SUPPRESS, help="Named wiki from the hub registry.")
    schema_adopt_parser.add_argument("--hub", default=argparse.SUPPRESS, help="Override hub path for --wiki resolution.")
    schema_adopt_parser.add_argument(
        "--include-archived",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Allow schema operations on an explicitly targeted archived wiki.",
    )
    schema_adopt_parser.add_argument("--dry-run", action="store_true", help="Preview without writing schema.md.")
    schema_adopt_parser.add_argument(
        "--state",
        choices=["advisory", "strict"],
        default="advisory",
        help="Initial schema_state for the generated schema.md.",
    )
    schema_adopt_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="Print JSON.")

    schema.set_defaults(func=run_schema)
    return schema


def register_identity_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Register ``identity`` on a parent ``llm-wiki`` parser; dispatch through ``identity.run_identity``."""
    identity = subparsers.add_parser(
        "identity",
        help="Read or assign a wiki's stable identity.",
        description=(
            "A wiki's identity is declared as wiki_id in its config.md and is "
            "independent of where the wiki currently lives. Registry entries that "
            "carry the same id can be verified on resolution instead of trusted."
        ),
    )
    # The path is accepted after the subcommand only: a parent positional would be
    # consumed as the subcommand name.
    add_scope_arguments(
        identity,
        local_help="Target .wiki/ in the current directory.",
        public_help=f"Resolve --wiki <slug>{PRIVATE_SUFFIX} to the public sibling <slug>.",
        include_archived_help="Allow identity operations on an explicitly targeted archived wiki.",
    )
    identity_sub = identity.add_subparsers(dest="identity_command", required=True)
    identity_status = identity_sub.add_parser("status", help="Print the declared wiki id.")
    identity_status.add_argument("--json", action="store_true", help="Print a JSON report.")
    identity_assign = identity_sub.add_parser(
        "assign", help="Declare a wiki id in config.md if the wiki has none."
    )
    # Accept the resolution flags on either side of the subcommand, as `schema` does.
    for identity_parser in (identity_status, identity_assign):
        identity_parser.add_argument("path", nargs="?", default=argparse.SUPPRESS, help="Wiki root path.")
        identity_parser.add_argument("--local", action="store_true", default=argparse.SUPPRESS, help="Target .wiki/ in the current directory.")
        identity_parser.add_argument("--wiki", default=argparse.SUPPRESS, help="Named wiki from the hub registry.")
        identity_parser.add_argument("--hub", default=argparse.SUPPRESS, help="Override hub path for --wiki resolution.")
        identity_parser.add_argument("--private", action="store_true", default=argparse.SUPPRESS, help=f"Resolve --wiki <slug> to <slug>{PRIVATE_SUFFIX}.")
        identity_parser.add_argument("--public", action="store_true", default=argparse.SUPPRESS, help=f"Resolve --wiki <slug>{PRIVATE_SUFFIX} to <slug>.")
        identity_parser.add_argument("--include-archived", action="store_true", default=argparse.SUPPRESS, help="Allow targeting an archived wiki.")
    identity_assign.add_argument("--id", help="Use this id instead of generating one.")
    identity_assign.add_argument(
        "--dry-run", action="store_true", help="Report what would happen without writing."
    )
    identity.set_defaults(func=_identity.run_identity)
    return identity


def run(args: argparse.Namespace) -> int:
    """Dispatch a parsed ``archive``, ``schema`` or ``identity`` namespace."""
    handlers = {
        "archive": run_archive,
        "schema": run_schema,
        "identity": _identity.run_identity,
    }
    return handlers[args.command](args)
