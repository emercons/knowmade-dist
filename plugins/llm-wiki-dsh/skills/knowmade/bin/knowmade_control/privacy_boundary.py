"""Cross-boundary references: a public file that points into a ``-PRIVATE`` sibling.

The content scan (``redaction.py``'s ``private-topic-path`` pattern) finds a
literal ``topics/x-PRIVATE/`` in text. It cannot see a reference that only
*resolves* into a private directory: a symlink, a registry path that does not
spell ``-PRIVATE``, a wiki-link by slug. :class:`BoundaryChecker` resolves the
references of one Markdown file and reports each one whose target lies in a
private location that the file itself does not live in. A private location is
a ``*-PRIVATE`` directory or the directory of a private topic of the hub
registry (a ``<name>-PRIVATE`` slug, wherever its path points).

The references it reads:

- inline links, reference definitions (``[id]: target``), autolinks
  (``<target>``) and ``href=``/``src=`` attributes, relative or ``file:``,
- wiki-links, by path (``[[../p-PRIVATE/a]]``) or by a slug that only a
  private file answers to,
- ``source:``, ``sources:`` and ``origin:`` frontmatter values (the ``raw/``
  provenance pointers and inventory record sources),
- path cells of inventory table rows,
- and, from a file under ``output/``, any reference to a ``goals.md``.

Targets are judged after ``os.path.realpath``, so a symlink into a private
directory counts, and whether or not the target exists. A file that is inside
the private location itself may refer to it: only references that cross the
boundary are findings. ``privacy scan`` (``--check cross-boundary``, and the
default check set) and ``checkpoint seal`` share this resolver; both give a
finding the id ``checkpoint_scan.checkpoint_finding`` derives from
(``cross-boundary``, path, line, the reference as written).
"""

from __future__ import annotations

import functools
import os
import re
import urllib.parse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .common import read_registry
from .indexing import clean_link_destination, extract_link_destinations, link_path_variants
from .lint_links import WIKILINK_RE, link_key
from .lint_report import is_private_dir_name
from .markdown import frontmatter_bounds, parse_frontmatter_block
from .roots import (
    enclosing_hub,
    registry_entry_path,
    topic_directory_of,
    windows_absolute_path,
)

CROSS_BOUNDARY_KIND = "cross-boundary"
PRIVATE_MESSAGE = "A public file links, cites or sources into a -PRIVATE sibling."
GOALS_MESSAGE = "A public output references a goals file."
SOURCE_KEYS = ("source", "sources", "origin")
GOALS_NAME = "goals.md"
MAX_VALUE_LENGTH = 1024

LINK_DEFINITION_RE = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*<?([^\s>]+)")
AUTOLINK_RE = re.compile(r"<([^<>\s]+)>")
HTML_REFERENCE_RE = re.compile(
    r"""\b(?:href|src)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>"']+))""", re.IGNORECASE
)

Parts = tuple[str, ...]


class BoundaryRegistryError(Exception):
    """The hub registry could not be read: callers fail closed with exit 2."""


@dataclass(frozen=True)
class BoundaryHit:
    """One reference that crosses the boundary: where, and as written."""

    line: int
    written: str
    goals: bool = False

    @property
    def message(self) -> str:
        return GOALS_MESSAGE if self.goals else PRIVATE_MESSAGE


def _split(real: str) -> Parts:
    drive, tail = os.path.splitdrive(real)
    names = [name for name in tail.replace("\\", "/").split("/") if name]
    return tuple(([drive] if drive else []) + names)


@functools.lru_cache(maxsize=65536)
def _real_directory(path: str) -> Parts:
    return _split(os.path.realpath(path))


def real_directory(path: str | os.PathLike[str]) -> Parts:
    """``realpath`` components of a directory, cached per directory."""
    return _real_directory(os.fspath(path))


def split_real(path: str | os.PathLike[str]) -> Parts:
    """The components of ``realpath(path)``, drive first, symlinks resolved.

    The directory part is resolved once per directory (cached); a final
    component that is a link or a directory is resolved in full, so a symlink
    and the on-disk case of a directory are never taken from the spelling.
    """
    text = os.fspath(path)
    head, tail = os.path.split(text)
    if not head or not tail or tail in {".", ".."} or os.path.islink(text) or os.path.isdir(text):
        return _split(os.path.realpath(text))
    return _real_directory(head) + (tail,)


def is_private_path(parts: Parts) -> bool:
    """True when some component of ``parts`` is a ``<slug>-PRIVATE`` name."""
    return any(is_private_dir_name(part) for part in parts)


def _is_prefix(prefix: Parts, parts: Parts) -> bool:
    return len(prefix) <= len(parts) and all(
        os.path.normcase(left) == os.path.normcase(right) for left, right in zip(prefix, parts)
    )


def crosses_boundary(source: Parts, target: Parts, private_topics: Sequence[Parts] = ()) -> bool:
    """Whether ``target`` is in a private location that ``source`` is not in.

    ``source`` is the directory of the referring file. A private directory
    that is an ancestor of the source (or the source itself) is the file's own
    home, so a reference into it does not cross anything.
    """
    for index, part in enumerate(target):
        if is_private_dir_name(part) and not _is_prefix(target[: index + 1], source):
            return True
    return any(_is_prefix(topic, target) and not _is_prefix(topic, source) for topic in private_topics)


def private_topic_dirs(start: Path) -> list[Path]:
    """Real directories of the private topics of the hub enclosing ``start``.

    A private topic is a registry entry whose slug is ``<name>-PRIVATE``; its
    directory is where the registry (and the per-machine overlay) puts it,
    which need not be named like its slug. No enclosing hub gives ``[]``. An
    unreadable registry raises :class:`BoundaryRegistryError` (fail closed).
    """
    try:
        hub = enclosing_hub(start)
        if hub is None:
            return []
        wikis = read_registry(hub).get("wikis", {})
        found: list[Path] = []
        for slug, entry in wikis.items():
            if is_private_dir_name(str(slug)) and (entry is None or isinstance(entry, dict)):
                topic = topic_directory_of(registry_entry_path(hub, str(slug), entry))
                found.append(Path(os.path.realpath(topic)))
        return found
    except SystemExit as exc:
        raise BoundaryRegistryError(str(exc.code)) from None


def _stems(directory: Path) -> set[str]:
    keys: set[str] = set()
    for _current, dirs, names in os.walk(directory):
        dirs[:] = [name for name in dirs if name != ".git"]
        keys.update(link_key(Path(name).stem) for name in names if name.lower().endswith(".md"))
    keys.discard("")
    return keys


class BoundaryChecker:
    """Resolve the references of Markdown files against the private boundary."""

    def __init__(
        self,
        root: Path,
        private_dirs: Sequence[Path] = (),
        private_slugs: set[str] | None = None,
        public_slugs: set[str] | None = None,
    ) -> None:
        self.root = root
        self.private_dirs = list(private_dirs)
        self.private_topics = [real_directory(directory) for directory in self.private_dirs]
        self.private_slugs = private_slugs or set()
        self.public_slugs = public_slugs or set()

    def is_private_location(self, parts: Parts) -> bool:
        """A ``*-PRIVATE`` component, or inside a private topic of the registry."""
        return is_private_path(parts) or any(_is_prefix(topic, parts) for topic in self.private_topics)

    def root_is_private(self) -> bool:
        return self.is_private_location(real_directory(self.root))

    def applies(self, rel: str) -> bool:
        """A Markdown file outside every private location of the tree."""
        if not rel.lower().endswith(".md"):
            return False
        parts = tuple(rel.replace("\\", "/").split("/")[:-1])
        return not is_private_path(parts) and not self.is_private_location(
            real_directory((self.root / rel).parent)
        )

    def index_slugs(self, files: Sequence[str]) -> None:
        """Fill the slug sets: private from the tree and the registry, public from ``files``.

        A slug wiki-link crosses the boundary only when a private file answers
        to it and no checked public file does.
        """
        private: set[str] = set()
        for current, dirs, names in os.walk(self.root):
            dirs[:] = [name for name in dirs if name != ".git"]
            if self.is_private_location(real_directory(current)):
                private.update(
                    link_key(Path(name).stem) for name in names if name.lower().endswith(".md")
                )
        for directory in self.private_dirs:
            private |= _stems(directory)
        public = {link_key(Path(rel).stem) for rel in files if self.applies(rel)}
        self.private_slugs = private - {""}
        self.public_slugs = public - {""}

    def check_symlink(self, path: Path, rel: str) -> list[BoundaryHit]:
        """A public file that is itself a link into a private location."""
        if self._crosses(real_directory(path.parent), [path]):
            return [BoundaryHit(0, rel)]
        return []

    def check(self, path: Path, rel: str, text: str) -> list[BoundaryHit]:
        """Every crossing reference of ``text``, the content of ``path``."""
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        source = real_directory(path.parent)
        rel_dirs = rel.replace("\\", "/").split("/")[:-1]
        in_output = "output" in rel_dirs
        in_inventory = "inventory" in rel_dirs
        hits: dict[tuple[int, str], BoundaryHit] = {}

        def note(line: int, written: str, candidates: Sequence[Path], slug: str | None = None) -> None:
            crossing = self._crosses(source, candidates) or (
                slug is not None and slug in self.private_slugs and slug not in self.public_slugs
            )
            if crossing:
                hits.setdefault((line, written), BoundaryHit(line, written))
            elif in_output and (
                slug == "goals" or any(candidate.name.lower() == GOALS_NAME for candidate in candidates)
            ):
                hits.setdefault((line, written), BoundaryHit(line, written, goals=True))

        for number, line in enumerate(text.split("\n"), start=1):
            for destination in _destinations(line):
                candidates = self._link_candidates(path.parent, destination)
                if candidates:
                    note(number, destination, candidates)
            if "[[" in line:
                for match in WIKILINK_RE.finditer(line):
                    written = match.group(1).strip()
                    target = written.replace("\\", "/")
                    if "/" in target:
                        note(number, written, [path.parent / target, self.root / target])
                    else:
                        stem = target[:-3] if target.lower().endswith(".md") else target
                        note(number, written, [], slug=link_key(stem))
            if in_inventory and line.lstrip().startswith("|"):
                for cell in line.split("|"):
                    value = _path_value(cell)
                    if value and ("/" in value or "\\" in value):
                        note(number, value, self._value_candidates(path.parent, value))

        bounds = frontmatter_bounds(text)
        if bounds is not None:
            start, end, _ = bounds
            first_line = text.count("\n", 0, start) + 1
            block_lines = text[start:end].split("\n")
            fields: dict[str, Any] = parse_frontmatter_block(text[start:end])
            for key in SOURCE_KEYS:
                raw = fields.get(key)
                values = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
                for item in values:
                    value = _path_value(item) if isinstance(item, str) else None
                    if not value or value.startswith("[["):
                        continue
                    number = first_line + _line_of(block_lines, value, key)
                    note(number, value, self._value_candidates(path.parent, value))
        return sorted(hits.values(), key=lambda hit: (hit.line, hit.written))

    def _crosses(self, source: Parts, candidates: Sequence[Path]) -> bool:
        return any(
            crosses_boundary(source, split_real(candidate), self.private_topics)
            for candidate in candidates
        )

    def _link_candidates(self, base: Path, destination: str) -> list[Path]:
        """The places a link destination can name; none for a web link or a fragment."""
        target = clean_link_destination(destination)
        if not target or target.startswith("#") or target.lower().startswith("mailto:"):
            return []
        if target.lower().startswith("file:"):
            local = urllib.parse.unquote(urllib.parse.urlparse(target).path)
            if len(local) > 2 and local[0] == "/" and local[2] == ":":
                local = local[1:]
            return [Path(local)] if local else []
        if "://" in target:
            return []
        windows = windows_absolute_path(target.split("#", 1)[0])
        if windows is not None:
            return [windows]
        candidates: list[Path] = []
        for variant in link_path_variants(destination):
            if variant.startswith("/"):
                candidates.extend([Path(variant), self.root / variant.lstrip("/")])
            else:
                candidates.append(base / variant)
        return candidates

    def _value_candidates(self, base: Path, value: str) -> list[Path]:
        """A frontmatter or table value as a path: beside the file and from the root."""
        spellings = {value, value.replace("\\", "/")}
        return [anchor / spelling for spelling in sorted(spellings) for anchor in (base, self.root)]


def bundle_checker(root: Path) -> BoundaryChecker:
    """The resolver for a checkpoint bundle directory.

    Private topics come from the hub registry enclosing the bundle (a bundle
    outside every hub has none); a slug wiki-link crosses when only a private
    topic file answers to it and no file of the bundle itself does.
    """
    checker = BoundaryChecker(root, private_topic_dirs(root))
    private: set[str] = set()
    for directory in checker.private_dirs:
        private |= _stems(directory)
    checker.private_slugs = private
    checker.public_slugs = {link_key(path.stem) for path in root.glob("*.md")} - {""}
    return checker


def _destinations(line: str) -> list[str]:
    """Every link target of one line: inline, definition, autolink, ``href``/``src``."""
    found: list[str] = []
    if "](" in line:
        found.extend(extract_link_destinations(line))
    definition = LINK_DEFINITION_RE.match(line)
    if definition:
        found.append(definition.group(1))
    if "<" in line:
        found.extend(match.group(1) for match in AUTOLINK_RE.finditer(line))
        for match in HTML_REFERENCE_RE.finditer(line):
            found.append(next(group for group in match.groups() if group is not None))
    return list(dict.fromkeys(item.strip() for item in found if item.strip()))


def _path_value(value: str) -> str | None:
    """A cell or frontmatter scalar as path text, or ``None`` when it cannot be one."""
    text = value.strip().strip("`\"'<>").split("#", 1)[0].strip()
    lowered = text.lower()
    if not text or len(text) > MAX_VALUE_LENGTH or "\n" in text:
        return None
    if "://" in text or lowered.startswith(("mailto:", "[")):
        return None
    return text


def _line_of(block_lines: Sequence[str], value: str, key: str) -> int:
    """Zero-based line of ``value`` in the block, else of ``key``, else the first."""
    for index, line in enumerate(block_lines):
        if value in line:
            return index
    for index, line in enumerate(block_lines):
        if line.startswith(f"{key}:"):
            return index
    return 0
