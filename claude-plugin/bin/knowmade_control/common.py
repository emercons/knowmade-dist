"""Leaf helpers shared by more than one deterministic CLI domain.

``legacy_cli`` used to hold every namespace in one module, so the hub
registry primitives and a handful of Markdown/table/JSON/hash helpers were
reachable by every domain without an import. (The lint data model, ``Issue``,
``Document`` and ``LintContext``, lives in ``lint_report``.) Now that the namespaces live in bounded modules
(``lint``, ``hub``, ``retract``, ``specialists``, ``adapters``,
``checkpoints``), the pieces two or more of them need live here, so no domain
module imports another one in a cycle. This module imports only the leaf
layers (``markdown``, ``roots``, ``fsio``) and nothing above it.

Everything here moved verbatim from ``legacy_cli`` (emercons/knowmade#67
R1.4). Import names from this module, not from the ``legacy_cli`` shim.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from . import fsio as _fsio
from .markdown import write_text_preserving
from .roots import (
    is_archived_registry_entry,
    is_permission_denied,
    permission_denied_message,
    resolve_registry_path,
)


PRIVATE_SUFFIX = "-PRIVATE"


SPECIALIST_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def first_body_summary(body: str) -> str:
    summary_match = re.search(r"(?m)^\*\*Summary\*\*:\s*(.+)$", body)
    if summary_match:
        return clean_summary(summary_match.group(1))

    paragraphs: list[str] = []
    current: list[str] = []
    in_code = False
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if not line:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        if (
            line.startswith("#")
            or line.startswith("|")
            or line.startswith("- ")
            or re.match(r"^\d+\.\s", line)
            or line == "---"
        ):
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        current.append(line)
    if current:
        paragraphs.append(" ".join(current))

    for paragraph in paragraphs:
        cleaned = clean_summary(paragraph)
        if len(cleaned) >= 40:
            return cleaned
    return "Compiled wiki article."


def clean_summary(value: str) -> str:
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"\[\[[^|\]]+\|([^\]]+)\]\]", r"\1", value)
    value = re.sub(r"\[\[([^\]]+)\]\]", r"\1", value)
    value = re.sub(r"[*_`]", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:260].rstrip()


def markdown_link_destination(path: str) -> str:
    if any(char.isspace() for char in path) or ")" in path:
        return f"<{path}>"
    return path


def extract_markdown_links(text: str) -> list[str]:
    links: list[str] = []
    for match in re.finditer(r"\]\((<([^>\n]+)>|([^)\n]+))\)", text):
        links.append(match.group(2) or match.group(3))
    return links


def strip_matching_quotes(value: str) -> str:
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    return value


def is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def table_cell(value: str) -> str:
    return re.sub(r"\s+", " ", value).replace("|", "\\|").strip()


def as_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def parse_date(value: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def read_registry(hub: Path) -> dict[str, Any]:
    """Read ``hub/wikis.json``; a registry that is not an object is one clear error.

    The root and ``wikis`` must both be JSON objects, so no caller has to
    guard ``.get("wikis", {})`` against a list or a string. Reading goes
    through ``fsio.read_json_object``, as ``roots._read_registry`` does, so a
    BOM is accepted and bad bytes are one message, never a traceback.
    """
    registry = hub / "wikis.json"
    data = _fsio.read_json_object(registry, "wiki registry")
    if "wikis" in data and not isinstance(data["wikis"], dict):
        raise SystemExit(f"invalid wiki registry {registry}: wikis must be an object")
    return data


def registry_indent(registry: Path) -> int | str:
    """The indent unit of an existing ``wikis.json``: a count of spaces, or a tab.

    The first indented line decides. A missing, unreadable or unindented file
    (or one mixing tabs and spaces) gets the default two spaces, so a tracked
    registry keeps its layout and a one-field change stays a one-line diff
    (knowmade#100).
    """
    try:
        text = _fsio.read_text(registry)
    except (OSError, UnicodeDecodeError):
        return 2
    for line in text.splitlines():
        stripped = line.lstrip(" \t")
        if not stripped or len(stripped) == len(line):
            continue
        lead = line[: len(line) - len(stripped)]
        if set(lead) == {"\t"}:
            return "\t"
        if set(lead) == {" "}:
            return len(lead)
        return 2
    return 2


def write_registry(hub: Path, data: dict[str, Any]) -> None:
    registry = hub / "wikis.json"
    # Atomic so a failed write leaves the previous registry intact; archive and
    # restore rely on that to roll their directory move back (knowmade#48).
    # An existing registry keeps its newline style and BOM (knowmade#28), its
    # indent unit, its key order (the order it was read in), and its non-ASCII
    # text as UTF-8 rather than \uXXXX escapes (knowmade#100).
    text = json.dumps(data, indent=registry_indent(registry), ensure_ascii=False, sort_keys=False)
    write_text_preserving(registry, text + "\n")


def topic_entry(data: dict[str, Any], slug: str) -> dict[str, Any] | None:
    entry = data.get("wikis", {}).get(slug)
    return entry if isinstance(entry, dict) else None


def active_topic_path(hub: Path, slug: str, entry: dict[str, Any] | None = None) -> Path:
    if entry and entry.get("path") and not is_archived_registry_entry(entry):
        return resolve_registry_path(str(entry["path"]), hub)
    return hub / "topics" / slug


def archived_topic_path(hub: Path, slug: str, entry: dict[str, Any] | None = None) -> Path:
    if entry and entry.get("path") and is_archived_registry_entry(entry):
        return resolve_registry_path(str(entry["path"]), hub)
    return hub / "topics" / ".archive" / slug


def is_initialized_topic(path: Path) -> bool:
    return (path / "_index.md").exists() and (path / "config.md").exists()


def write_private_json(path: Path, data: dict[str, Any]) -> None:
    """Atomically write a machine-local receipt without broadening permissions."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _fsio.write_json_atomic(path, data, mode=0o600, sort_keys=True)
    path.chmod(0o600)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    return _fsio.read_json_object(path, label)


def add_scope_arguments(
    parser: argparse.ArgumentParser,
    *,
    local_help: str,
    public_help: str,
    include_archived_help: str,
) -> None:
    """Register the shared wiki-root scope options on a namespace parser.

    ``lint``, ``schema`` and ``identity`` resolve a wiki root through the same
    hub rules and register ``--local``, ``--wiki``, ``--hub``, the mutually
    exclusive ``--private``/``--public`` pair and ``--include-archived`` in
    this order. Only the verb-specific help texts differ, so they are passed
    in; the ``--help`` output is byte-identical to the former inline blocks.
    """
    parser.add_argument("--local", action="store_true", help=local_help)
    parser.add_argument("--wiki", help="Named wiki from the hub registry.")
    parser.add_argument("--hub", help="Override hub path for --wiki resolution.")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--private",
        action="store_true",
        help=f"Resolve --wiki <slug> to the private sibling <slug>{PRIVATE_SUFFIX}.",
    )
    scope.add_argument("--public", action="store_true", help=public_help)
    parser.add_argument("--include-archived", action="store_true", help=include_archived_help)
