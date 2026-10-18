"""Frontmatter parsing and Markdown document rendering helpers.

These are the leaf primitives shared by the deterministic control plane: the
minimal YAML-ish frontmatter reader every lint and schema check relies on, the
quoting used when writing frontmatter back, the dated starter topic guide, and
the ``log.md`` appender. They depend only on the ``fsio`` leaf, so every
domain can import them without an import cycle.

This is the one frontmatter parser: :func:`frontmatter_bounds` finds the block
(tolerating a UTF-8 BOM and CRLF), :func:`parse_frontmatter_block` reads it.
``frontmatter.py`` builds the read/edit/write API on top of both.
``scripts/sync-session-vendor.py`` stamps :func:`frontmatter_bounds` into the
single-file session script, so its digests use the same exact-fence rule.

Newline policy (emercons/knowmade#28): an existing file keeps its newline
style (CRLF when its first line ends in CRLF, else LF) and its BOM when it is
rewritten or appended to; a new file is LF without a BOM. The helpers
:func:`sniff_style`, :func:`write_text_preserving` and
:func:`append_text_preserving` implement that for whole-file and append writes.
"""

from __future__ import annotations

import datetime as dt
import os
import re
from pathlib import Path
from typing import Any

from . import fsio as _fsio

BOM = "﻿"
_BOM_BYTES = b"\xef\xbb\xbf"
_STYLE_PROBE_BYTES = 64 * 1024


def today_date() -> dt.date:
    """Return today's date, with an env override for deterministic fixtures."""
    override = os.environ.get("LLM_WIKI_TODAY")
    if override:
        try:
            return dt.date.fromisoformat(override[:10])
        except ValueError:
            raise SystemExit(f"invalid LLM_WIKI_TODAY date: {override!r}")
    return dt.date.today()


def parse_scalar(value: str) -> Any:
    value = value.strip()
    if value == "":
        return ""
    if value in {"null", "Null", "NULL", "~"}:
        return None
    if value in {"true", "True", "TRUE"}:
        return True
    if value in {"false", "False", "FALSE"}:
        return False
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [parse_scalar(part.strip()) for part in split_inline_list(inner)]
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return _unescape_double_quoted(value[1:-1])
    if len(value) >= 2 and value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    return value


_DOUBLE_QUOTED_ESCAPES = {"\\": "\\", '"': '"', "n": "\n", "t": "\t"}
_DOUBLE_QUOTED_ESCAPE_RE = re.compile(r"\\(.)", re.DOTALL)


def _unescape_double_quoted(inner: str) -> str:
    r"""Undo ``yaml_quote``: ``\\``, ``\"``, ``\n`` and ``\t``.

    Any other backslash sequence stays literal, so a hand-written
    ``"C:\Users"`` keeps reading as the path it was written as.
    """
    return _DOUBLE_QUOTED_ESCAPE_RE.sub(
        lambda match: _DOUBLE_QUOTED_ESCAPES.get(match.group(1), match.group(0)), inner
    )


def split_inline_list(value: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    for char in value:
        if escaped:
            escaped = False
            current.append(char)
        elif char == "\\" and quote == '"':
            escaped = True
            current.append(char)
        elif char in {"'", '"'}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            current.append(char)
        elif char == "," and quote is None:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    parts.append("".join(current).strip())
    return parts


def parse_frontmatter_block(text: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    current_key: str | None = None
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if raw_line.startswith("  - ") and current_key:
            current_value = data.setdefault(current_key, [])
            if not isinstance(current_value, list):
                current_value = []
                data[current_key] = current_value
            current_value.append(parse_scalar(raw_line[4:]))
            continue
        if raw_line.startswith(" ") or ":" not in raw_line:
            continue
        key, value = raw_line.split(":", 1)
        key = key.strip()
        if not key:
            continue
        current_key = key
        value = value.strip()
        if value == "":
            data[key] = []
        else:
            data[key] = parse_scalar(value)
    return data


_CLOSING_FENCE_RE = re.compile(r"^---[ \t]*\r?$", re.MULTILINE)


def frontmatter_bounds(text: str) -> tuple[int, int, int] | None:
    """Locate the frontmatter block in ``text``.

    Returns ``(start, end, body_start)`` offsets into ``text``: the block is
    ``text[start:end]`` (without the fence lines; with CRLF it still carries
    the final ``\\r``) and ``text[body_start:]`` is everything after the
    closing ``---``. ``None`` when the text has no complete block. A leading
    UTF-8 BOM and CRLF line endings are accepted. The closing fence is the first
    line that is exactly ``---`` (trailing spaces and tabs allowed): ``----`` and
    ``---x`` do not close the block, and ``---`` straight after the opening
    fence is a valid empty block (emercons/knowmade#103).
    """
    offset = 1 if text.startswith(BOM) else 0
    if text.startswith("---\n", offset):
        start = offset + 4
    elif text.startswith("---\r\n", offset):
        start = offset + 5
    else:
        return None
    fence = _CLOSING_FENCE_RE.search(text, start)
    if fence is None:
        return None
    # ``end`` is the newline that ends the block's last line; an empty block has none.
    return start, max(fence.start() - 1, start), fence.start() + 3


def lf(text: str) -> str:
    """``text`` with CRLF folded to LF."""
    return text.replace("\r\n", "\n")


def split_markdown_frontmatter(text: str) -> tuple[str, str] | None:
    bounds = frontmatter_bounds(text)
    if bounds is None:
        return None
    start, end, body_start = bounds
    block = text[start:end]
    if block.endswith("\r"):
        block = block[:-1]
    return lf(block), lf(text[body_start:])


def yaml_quote(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def topic_title(path: Path, slug: str) -> str:
    """The ``title`` of ``path/config.md`` read through ``frontmatter``, else a title-cased slug."""
    # frontmatter imports this module, so the import cannot be at module level.
    from . import frontmatter as _fm

    config = path / "config.md"
    if config.is_file():
        try:
            title = _fm.load(config).fields.get("title")
        except (OSError, ValueError):  # FrontmatterError and UnicodeDecodeError are ValueErrors
            title = None
        if isinstance(title, str) and title.strip():
            return title.strip()
    return slug.replace("-", " ").title()


def default_schema_text(root: Path, state: str = "advisory") -> str:
    today = today_date().isoformat()
    title = topic_title(root, root.name)
    return "\n".join(
        [
            "---",
            f"title: {yaml_quote(title + ' Topic Guide')}",
            f"schema_state: {state}",
            f"created: {today}",
            f"updated: {today}",
            'summary: "Human-owned topic guide for local vocabulary, relationships, source boundaries, and conventions."',
            "---",
            "",
            f"# {title} Topic Guide",
            "",
            "> This is not a database schema and it does not make existing wiki content invalid.",
            "> It is a human-owned guide for local vocabulary, relationships, source boundaries, and conventions.",
            "> The librarian may propose improvements, but agents should not rewrite this file without explicit approval.",
            "",
            "## State",
            "",
            f"- `schema_state`: `{state}`",
            "- `advisory` means suggestions only.",
            "- Keep this advisory until the librarian's topic-guide suggestions are consistently low-noise.",
            "- `strict` is an advanced, explicit opt-in; it still never permits automatic content rewrites.",
            "",
            "## Entity Types",
            "",
            "| Type | Meaning |",
            "|------|---------|",
            "| `concept` | Bounded idea or mechanism compiled under `wiki/concepts/`. |",
            "| `topic` | Broad theme or playbook compiled under `wiki/topics/`. |",
            "| `reference` | Curated list, map, standard, or lookup page under `wiki/references/`. |",
            "| `source` | Raw evidence under `raw/`; factual claims should trace back here. |",
            "| `artifact` | Generated output, project file, inventory record, or dataset manifest. |",
            "",
            "## Relationship Verbs",
            "",
            "- `cites`: article or output cites a raw source.",
            "- `supports`: source or article supports a claim, plan, or decision.",
            "- `contradicts`: source or article conflicts with another claim.",
            "- `supersedes`: newer article/output replaces an older one.",
            "- `depends-on`: artifact or workflow relies on another artifact.",
            "- `implements`: code, output, or project implements a plan.",
            "- `relates-to`: weak relationship used when a stronger verb is not yet justified.",
            "",
            "## Source Conventions",
            "",
            "- Keep raw sources immutable under `raw/`.",
            "- Compile durable synthesis under `wiki/` with explicit `sources:` frontmatter.",
            "- Keep generated deliverables under `output/`.",
            "- Use inventory for durable tracking state, not factual evidence.",
            "- Use dataset manifests for large, mutable, binary, remote, or query-oriented data.",
            "",
            "## Compile Guidance",
            "",
            "<!-- Record only topic-specific rules that improve article planning. -->",
            "<!-- Examples: one article per mechanism; keep X separate from Y; refer Z claims to another topic. -->",
            "- Shape article cardinality, boundaries, and scope here when this topic needs rules beyond the global defaults.",
            "- This guidance does not redefine global categories or frontmatter and does not exempt raw sources from C6 coverage.",
            "- In advisory mode, compilation explains deliberate deviations; in strict mode, it requests confirmation before writing them.",
            "",
            "## Adoption Notes",
            "",
            "- Existing articles do not need immediate rewrites to adopt this guide.",
            "- Librarian topic-guide scans should propose changes in `output/schema-proposal-*.md`.",
            "- Promote only the small conventions that fit this topic; delete unused starter rows.",
            "- Prefer staying advisory unless the human explicitly wants stricter convention warnings.",
            "",
        ]
    )


def detect_newline(text: str) -> str:
    """``"\\r\\n"`` when the first line of ``text`` ends in CRLF, else ``"\\n"``."""
    first = text.find("\n")
    return "\r\n" if first > 0 and text[first - 1] == "\r" else "\n"


def sniff_style(path: Path) -> tuple[str, bool]:
    """``(newline, bom)`` of an existing file; LF without BOM when absent or empty."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(_STYLE_PROBE_BYTES)
    except OSError:
        return "\n", False
    bom = head.startswith(_BOM_BYTES)
    if bom:
        head = head[len(_BOM_BYTES) :]
    first = head.find(b"\n")
    return ("\r\n" if first > 0 and head[first - 1 : first] == b"\r" else "\n"), bom


def write_target(path: Path) -> Path:
    """Where an atomic rewrite of ``path`` must land (through a symlink)."""
    return Path(os.path.realpath(path)) if path.is_symlink() else path


def write_text_preserving(path: Path, text: str) -> None:
    """Atomically write ``text`` (LF) to ``path``, keeping an existing file's style.

    An existing file keeps its newline style and BOM; a new file is LF without
    a BOM. ``text`` is plain ``\\n`` text.
    """
    newline, bom = sniff_style(path)
    if newline != "\n":
        text = text.replace("\n", newline)
    data = text.encode("utf-8")
    if bom:
        data = _BOM_BYTES + data
    _fsio.atomic_write_bytes(write_target(path), data)


def append_text_preserving(path: Path, text: str) -> None:
    """Append ``text`` (LF) to ``path`` in the file's own newline style.

    A missing file is created LF without a BOM; an existing BOM is untouched.
    """
    newline, _ = sniff_style(path)
    if newline != "\n":
        text = text.replace("\n", newline)
    with open(path, "ab") as handle:
        handle.write(text.encode("utf-8"))


def append_log(path: Path, operation: str, message: str) -> None:
    log = path / "log.md"
    if not log.exists():
        return
    today = today_date().isoformat()
    append_text_preserving(log, f"\n## [{today}] {operation} | {message}\n")
