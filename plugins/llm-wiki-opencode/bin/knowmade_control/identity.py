"""Stable wiki identity, independent of physical path.

`DESIGN.md` section 8 records the cost of treating a path as an identity:
two hubs can hold the same slug without being the same wiki, and moving a
directory changes its path while its semantic identity is unchanged. Federation
is the eventual answer; this module is the part of it that is useful now.

A wiki declares ``wiki_id`` in its ``config.md`` frontmatter. A registry entry
may carry the same ``id``. When both are present, resolution can be *verified*
rather than trusted: a registry entry that resolves to a different wiki is a
detectable error instead of a silent write to the wrong copy.

Both fields are optional. Nothing regresses for a wiki or registry that has
neither.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from . import frontmatter as _frontmatter
from . import roots as _roots

WIKI_ID_KEY = "wiki_id"
_ID = re.compile(r"^wk-[0-9a-f]{16}$")


def new_wiki_id() -> str:
    return f"wk-{uuid.uuid4().hex[:16]}"


def is_wiki_id(value: str) -> bool:
    return bool(_ID.match(value))


def read_wiki_id(wiki_root: Path) -> str | None:
    """Return the identity declared by a wiki root, if it declares one."""
    try:
        fields, _, _, _ = _frontmatter.read(wiki_root / "config.md")
    except (OSError, UnicodeDecodeError, _frontmatter.FrontmatterError):
        return None
    value = fields.get(WIKI_ID_KEY)
    return value if isinstance(value, str) and is_wiki_id(value) else None


def plan_assignment(wiki_root: Path, *, wiki_id: str | None = None) -> str | None:
    """Validate an ``identity assign`` request; return the id already declared.

    Raises ``SystemExit`` with one clear message when the request cannot be
    honoured, so neither the real assignment nor ``--dry-run`` ends in a
    traceback or silently drops the requested id:

    * ``wiki_root`` has no ``config.md`` (a hub root, an uninitialised path);
    * ``--id`` is not a well-formed ``wk-`` id;
    * ``--id`` differs from the id the wiki already declares. A declared id is
      the wiki's stable identity; replacing it is deliberately not offered here,
      and an explicit request must not be reported as satisfied.
    """
    config = wiki_root / "config.md"
    if not config.is_file():
        raise SystemExit(
            f"no config.md at {wiki_root}; identity assign needs an initialized wiki "
            "(is this a hub root or an uninitialized path?)"
        )
    if wiki_id is not None and not is_wiki_id(wiki_id):
        raise SystemExit(f"not a valid wiki id: {wiki_id}")
    existing = read_wiki_id(wiki_root)
    if existing and wiki_id is not None and wiki_id != existing:
        raise SystemExit(
            f"{config} already declares {WIKI_ID_KEY} {existing}; refusing to replace it with {wiki_id}"
        )
    return existing


def assign_wiki_id(wiki_root: Path, *, wiki_id: str | None = None) -> tuple[str, bool]:
    """Ensure the wiki declares an identity; return it and whether it was created.

    The identity is written into the existing frontmatter block. A wiki whose
    ``config.md`` has no frontmatter is left alone: creating one would rewrite a
    file this module does not own.
    """
    existing = plan_assignment(wiki_root, wiki_id=wiki_id)
    if existing:
        return existing, False

    config = wiki_root / "config.md"
    value = wiki_id or new_wiki_id()
    if not is_wiki_id(value):
        raise SystemExit(f"not a valid wiki id: {value}")
    try:
        _frontmatter.set_field(config, WIKI_ID_KEY, value)
    except (OSError, UnicodeDecodeError) as exc:
        raise SystemExit(f"cannot read {config}: {exc}") from exc
    except _frontmatter.FrontmatterError as exc:
        if exc.kind == "unterminated":
            raise SystemExit(f"{config} has an unterminated frontmatter block") from exc
        raise SystemExit(f"{config} has no frontmatter block to hold {WIKI_ID_KEY}") from exc
    return value, True


def verify_entry(declared_id: Any, wiki_root: Path) -> str | None:
    """Return a human-readable mismatch, or None when the entry is consistent.

    An entry without an id, or a target without one, is not a mismatch: identity
    is opt-in and adoption is gradual.
    """
    if not isinstance(declared_id, str) or not declared_id:
        return None
    actual = read_wiki_id(wiki_root)
    if actual is None or actual == declared_id:
        return None
    return f"declares id {declared_id} but the wiki at that path declares {actual}"


def status_payload(wiki_root: Path) -> dict[str, Any]:
    return {
        "root": str(wiki_root),
        "wiki_id": read_wiki_id(wiki_root),
        "declared": read_wiki_id(wiki_root) is not None,
    }


def run_identity(args: Any) -> int:
    """`llm-wiki identity status|assign` — read or create a wiki's stable id."""
    root = _roots.resolve_wiki_root_args(args)
    command = getattr(args, "identity_command", "status")

    if command == "status":
        payload = status_payload(root)
        if getattr(args, "json", False):
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"Knowmade identity: {root}")
            print(f"wiki_id: {payload['wiki_id'] or 'none'}")
            if not payload["declared"]:
                print("Next: llm-wiki identity assign")
        return 0

    if command == "assign":
        if getattr(args, "dry_run", False):
            requested = getattr(args, "id", None)
            existing = plan_assignment(root, wiki_id=requested)
            print(existing or requested or "would assign a new wiki_id")
            return 0
        value, created = assign_wiki_id(root, wiki_id=getattr(args, "id", None))
        print(f"{'assigned' if created else 'already declared'}: {value}")
        return 0

    raise SystemExit(f"unknown identity command: {command}")
