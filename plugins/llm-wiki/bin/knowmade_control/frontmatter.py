"""Read, edit and write the YAML-ish frontmatter of Markdown corpus files.

One module for every place the control plane touches a frontmatter block
(emercons/knowmade#67 R1.2, emercons/knowmade#28). Before it, five parsers and
writers each split on ``---`` their own way (lint, schema, identity, rules,
source lifecycle), read with plain ``utf-8`` (so a BOM made a file look
frontmatter-less) and wrote back through a text round trip (so a CRLF file was
silently rewritten as LF). The parser itself stays in ``markdown`` (one
parser: ``frontmatter_bounds`` and ``parse_frontmatter_block``); this module
adds the file-level API on top of it.

Policy, implemented once here:

- Reading accepts a UTF-8 BOM and CRLF everywhere. Fields parse exactly as
  ``parse_frontmatter_block`` always has.
- Rewriting an existing file keeps its newline style (CRLF when its first line
  ends in CRLF, else LF) and its BOM. Only the frontmatter block is rebuilt;
  everything after the closing fence is carried over byte for byte, and inside
  the block a change touches only the lines it names.
- A new file is LF without a BOM (:func:`write_document` defaults).
- Writes are atomic and keep the file's permissions (``fsio``); a symlinked
  file is rewritten through the link.

API: :func:`read` returns ``(fields, bounds, newline, bom)``; :func:`load`
returns the same facts plus the text as a :class:`FrontmatterFile`;
:func:`update` applies a block transform; :func:`set_field` replaces or adds
one key; :func:`write_document` writes a whole document.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

try:
    from . import fsio as _fsio
    from .markdown import (
        BOM,
        detect_newline,
        frontmatter_bounds,
        parse_frontmatter_block,
        write_target,
    )
except ImportError:  # executed directly as a script, not imported as a package
    import fsio as _fsio
    from markdown import (
        BOM,
        detect_newline,
        frontmatter_bounds,
        parse_frontmatter_block,
        write_target,
    )

__all__ = [
    "Bounds",
    "FrontmatterError",
    "FrontmatterFile",
    "append_lines",
    "field_value",
    "has_value",
    "insert_after_title",
    "load",
    "read",
    "read_block",
    "set_field",
    "set_field_in_block",
    "set_list",
    "update",
    "write_document",
]

_BOM_BYTES = BOM.encode("utf-8")


class FrontmatterError(ValueError):
    """A file has no usable frontmatter block; ``kind`` says why.

    ``kind`` is ``"missing"`` (no opening ``---`` line) or ``"unterminated"``
    (opened but never closed).
    """

    def __init__(self, message: str, kind: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class Bounds:
    """Offsets of the block inside :attr:`FrontmatterFile.text`.

    ``text[start:end]`` is the block (fence lines excluded) and
    ``text[body_start:]`` is everything after the closing ``---``.
    """

    start: int
    end: int
    body_start: int


@dataclass(frozen=True)
class FrontmatterFile:
    """A Markdown file with a frontmatter block, as found on disk.

    ``text`` is the decoded content with the BOM removed and line endings left
    exactly as stored; ``newline`` and ``bom`` record the style to write back.
    """

    path: Path
    text: str
    newline: str
    bom: bool
    bounds: Bounds

    @property
    def block(self) -> str:
        """The frontmatter block with LF line endings and no final newline."""
        raw = self.text[self.bounds.start : self.bounds.end]
        if raw.endswith("\r"):
            raw = raw[:-1]
        return _universal(raw)

    @property
    def body(self) -> str:
        """Everything after the closing ``---``, with LF line endings."""
        return _universal(self.text[self.bounds.body_start :])

    @property
    def fields(self) -> dict[str, Any]:
        return parse_frontmatter_block(self.block)

    def rewrite(self, new_block: str) -> bool:
        """Write ``new_block`` (LF text) in place of the block; True if it changed.

        The file keeps its newline style and BOM, and everything after the
        closing fence is carried over byte for byte. Nothing is written when
        the block is unchanged.
        """
        if new_block == self.block:
            return False
        opening = self.text[: self.bounds.start]
        rest = self.text[self.bounds.body_start :]
        data = _encode(opening, new_block, rest, self.newline, self.bom)
        _fsio.atomic_write_bytes(write_target(self.path), data)
        return True


def _universal(text: str) -> str:
    """Fold CRLF and lone CR to LF, as text-mode reading always did."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def load(path: Path) -> FrontmatterFile:
    """Read ``path`` and locate its frontmatter block.

    Raises ``OSError`` / ``UnicodeDecodeError`` as reading does, and
    :class:`FrontmatterError` when there is no complete block.
    """
    data = Path(path).read_bytes()
    bom = data.startswith(_BOM_BYTES)
    text = data[len(_BOM_BYTES) :].decode("utf-8") if bom else data.decode("utf-8")
    found = frontmatter_bounds(text)
    if found is None:
        if text.startswith("---\n") or text.startswith("---\r\n"):
            raise FrontmatterError(f"{path} has an unterminated frontmatter block", "unterminated")
        raise FrontmatterError(f"{path} has no frontmatter block", "missing")
    return FrontmatterFile(Path(path), text, detect_newline(text), bom, Bounds(*found))


def read(path: Path) -> tuple[dict[str, Any], Bounds, str, bool]:
    """``(fields, bounds, newline, bom)`` for ``path``; see :func:`load`."""
    loaded = load(path)
    return loaded.fields, loaded.bounds, loaded.newline, loaded.bom


def read_block(path: Path) -> str:
    """The LF-normalised frontmatter block text of ``path``."""
    return load(path).block


def _encode(prefix: str, block: str, rest: str, newline: str, bom: bool) -> bytes:
    """``prefix`` (the opening fence) + block + closing fence + ``rest``."""
    block = block.replace("\n", newline)
    text = f"{prefix}{block}{newline}---{rest}"
    data = text.encode("utf-8")
    return _BOM_BYTES + data if bom else data


def write_document(
    path: Path,
    block: str,
    body: str,
    *,
    newline: str = "\n",
    bom: bool = False,
) -> None:
    """Write a whole document: fence, ``block``, fence, ``body``.

    ``block`` and ``body`` are LF text; ``body`` is what follows the closing
    ``---`` (so it normally starts with a newline). Defaults give a new file:
    LF, no BOM. To rewrite an existing file pass its ``newline`` and ``bom``
    (from :func:`read`), or use :func:`update` / :func:`set_field`, which do.
    """
    rest = body.replace("\n", newline)
    data = _encode(f"---{newline}", block, rest, newline, bom)
    _fsio.atomic_write_bytes(write_target(Path(path)), data)


def update(path: Path, transform: Callable[[str], str]) -> bool:
    """Rewrite the frontmatter block of ``path`` with ``transform(block)``.

    ``block`` is LF text; the result is written back with the file's own
    newline style and BOM and everything after the closing fence untouched.
    Returns whether the file changed (the file is not written otherwise).
    """
    loaded = load(path)
    return loaded.rewrite(transform(loaded.block))


def set_field(
    path: Path,
    key: str,
    value: str | int | bool | list[str],
    *,
    after: str | None = None,
) -> bool:
    """Set top-level ``key`` in the frontmatter of ``path``; True if it changed.

    A string, int or bool is written as ``key: value`` (strings verbatim, so
    quote them first if they need it); a list becomes a block list. An existing
    ``key`` is replaced where it stands, together with its ``  - `` items; a
    new one goes after the ``after`` key when given, else at the end of the
    block.
    """
    return update(path, lambda block: set_field_in_block(block, key, value, after=after))


# --- pure block transforms ---------------------------------------------------
# These work on LF block text and are what ``update`` callers compose.


def _render(key: str, value: str | int | bool | list[str]) -> list[str]:
    if isinstance(value, list):
        return [f"{key}:", *(f"  - {item}" for item in value)]
    if isinstance(value, bool):
        value = "true" if value else "false"
    return [f"{key}: {value}"]


def _key_re(key: str) -> re.Pattern[str]:
    return re.compile(rf"^{re.escape(key)}\s*:")


def set_field_in_block(
    block: str,
    key: str,
    value: str | int | bool | list[str],
    *,
    after: str | None = None,
) -> str:
    """:func:`set_field` on block text."""
    lines = block.splitlines()
    wanted = _key_re(key)
    new_lines = _render(key, value)
    output: list[str] = []
    index = 0
    replaced = False
    while index < len(lines):
        line = lines[index]
        if wanted.match(line):
            output.extend(new_lines)
            index += 1
            while index < len(lines) and lines[index].startswith("  - "):
                index += 1
            replaced = True
            continue
        output.append(line)
        index += 1
    if not replaced:
        anchor = _key_re(after) if after else None
        position = len(output)
        if anchor is not None:
            for number, line in enumerate(output):
                if anchor.match(line):
                    position = number + 1
                    break
        output[position:position] = new_lines
    return "\n".join(output)


def set_list(block: str, key: str, values: list[str]) -> str:
    """Replace (or append) ``key`` as a block list of ``values``."""
    return set_field_in_block(block, key, list(values))


def field_value(block: str, field: str) -> str | None:
    """The raw text after ``field:`` on its line, or ``None`` when empty/absent."""
    match = re.search(rf"(?m)^{re.escape(field)}\s*:\s*(.*)$", block)
    if not match:
        return None
    value = match.group(1).strip()
    return value if value else None


def has_value(block: str, field: str) -> bool:
    """Whether ``field`` carries a non-empty scalar or a block list."""
    value = field_value(block, field)
    if value not in {None, "", "[]"}:
        return True
    return bool(
        re.search(
            rf"(?ms)^{re.escape(field)}\s*:\s*\n(?:  - .+\n?)+",
            block,
        )
    )


def insert_after_title(block: str, lines: list[str]) -> str:
    """Insert ``lines`` after the ``title:`` line (or the first line)."""
    parts = block.splitlines()
    insert_at = 1 if parts else 0
    for index, line in enumerate(parts):
        if line.startswith("title:"):
            insert_at = index + 1
            break
    return "\n".join(parts[:insert_at] + lines + parts[insert_at:])


def append_lines(block: str, lines: list[str]) -> str:
    """Append ``lines`` at the end of the block."""
    return block.rstrip() + "\n" + "\n".join(lines)
