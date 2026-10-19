"""Stable wiki and hub identity, independent of physical path.

`DESIGN.md` section 8 records the cost of treating a path as an identity:
two hubs can hold the same slug without being the same wiki, and moving a
directory changes its path while its semantic identity is unchanged. Federation
is the eventual answer; this module is the part of it that is useful now.

A wiki declares ``wiki_id`` in its ``config.md`` frontmatter. A registry entry
may carry the same ``id``. When both are present, resolution can be *verified*
rather than trusted: a registry entry that resolves to a different wiki is a
detectable error instead of a silent write to the wrong copy. A hub declares
``hub_id`` as a top-level key of its ``wikis.json``, so the id travels with the
registry.

Every id is optional. Nothing regresses for a wiki or registry that has none.
``assign`` never replaces a declared id; ``fork`` is the one explicit way to
give an independent copy a new identity and records the old one
(``wiki_forked_from`` / ``hub_forked_from``). See ``DESIGN.md`` section 4.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from . import common as _common
from . import frontmatter as _frontmatter
from . import fsio as _fsio
from . import roots as _roots

WIKI_ID_KEY = "wiki_id"
WIKI_FORKED_FROM_KEY = "wiki_forked_from"
HUB_ID_KEY = "hub_id"
HUB_FORKED_FROM_KEY = "hub_forked_from"
_ID = re.compile(r"^wk-[0-9a-f]{16}$")
_HUB_ID = re.compile(r"^hb-[0-9a-f]{16}$")


def new_wiki_id() -> str:
    return f"wk-{uuid.uuid4().hex[:16]}"


def is_wiki_id(value: str) -> bool:
    return bool(_ID.match(value))


def new_hub_id() -> str:
    return f"hb-{uuid.uuid4().hex[:16]}"


def is_hub_id(value: str) -> bool:
    return bool(_HUB_ID.match(value))


def read_wiki_id(wiki_root: Path) -> str | None:
    """Return the identity declared by a wiki root, if it declares one."""
    try:
        fields, _, _, _ = _frontmatter.read(wiki_root / "config.md")
    except (OSError, UnicodeDecodeError, _frontmatter.FrontmatterError):
        return None
    value = fields.get(WIKI_ID_KEY)
    return value if isinstance(value, str) and is_wiki_id(value) else None


def read_wiki_forked_from(wiki_root: Path) -> str | None:
    """Return the id this wiki was forked from, if ``config.md`` records one."""
    try:
        fields, _, _, _ = _frontmatter.read(wiki_root / "config.md")
    except (OSError, UnicodeDecodeError, _frontmatter.FrontmatterError):
        return None
    value = fields.get(WIKI_FORKED_FROM_KEY)
    return value if isinstance(value, str) and is_wiki_id(value) else None


def plan_assignment(wiki_root: Path, *, wiki_id: str | None = None) -> str | None:
    """Validate an ``identity assign`` request; return the id already declared.

    Raises ``SystemExit`` with one clear message when the request cannot be
    honoured, so neither the real assignment nor ``--dry-run`` ends in a
    traceback or silently drops the requested id:

    * ``wiki_root`` has no ``config.md`` (a hub root, an uninitialised path);
    * ``--id`` is not a well-formed ``wk-`` id;
    * ``--id`` differs from the id the wiki already declares. A declared id is
      the wiki's stable identity; ``assign`` never replaces it (``fork`` does,
      explicitly), and an explicit request must not be reported as satisfied.
    """
    config = wiki_root / "config.md"
    if not config.is_file():
        raise SystemExit(
            f"no config.md at {wiki_root}; identity assign needs an initialized wiki "
            "(is this a hub root or an uninitialized path? use --hub for a hub)"
        )
    if wiki_id is not None and not is_wiki_id(wiki_id):
        raise SystemExit(f"not a valid wiki id: {wiki_id}")
    existing = read_wiki_id(wiki_root)
    if existing and wiki_id is not None and wiki_id != existing:
        raise SystemExit(
            f"{config} already declares {WIKI_ID_KEY} {existing}; refusing to replace it with {wiki_id}"
        )
    return existing


def _frontmatter_exit(config: Path, exc: Exception) -> SystemExit:
    if isinstance(exc, _frontmatter.FrontmatterError):
        if exc.kind == "unterminated":
            return SystemExit(f"{config} has an unterminated frontmatter block")
        return SystemExit(f"{config} has no frontmatter block to hold {WIKI_ID_KEY}")
    return SystemExit(f"cannot read {config}: {exc}")


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
    except (OSError, UnicodeDecodeError, _frontmatter.FrontmatterError) as exc:
        raise _frontmatter_exit(config, exc) from exc
    return value, True


def plan_wiki_fork(wiki_root: Path, *, wiki_id: str | None = None) -> str:
    """Validate an ``identity fork`` of a wiki; return the id it will replace.

    Raises ``SystemExit`` when the wiki has no ``config.md``, declares no id
    (there is nothing to fork from), or ``--id`` is malformed or equals the
    declared id.
    """
    config = wiki_root / "config.md"
    if not config.is_file():
        raise SystemExit(
            f"no config.md at {wiki_root}; identity fork needs an initialized wiki "
            "(is this a hub root or an uninitialized path? use --hub for a hub)"
        )
    existing = read_wiki_id(wiki_root)
    if existing is None:
        raise SystemExit(
            f"{config} declares no {WIKI_ID_KEY}: nothing to fork from; use identity assign"
        )
    if wiki_id is not None:
        if not is_wiki_id(wiki_id):
            raise SystemExit(f"not a valid wiki id: {wiki_id}")
        if wiki_id == existing:
            raise SystemExit(
                f"{config} already declares {WIKI_ID_KEY} {existing}; --id must be a different id"
            )
    return existing


def fork_wiki_id(wiki_root: Path, *, wiki_id: str | None = None) -> tuple[str, str]:
    """Give a wiki a new identity and record the old one; return ``(old, new)``.

    ``config.md`` keeps one ``wiki_forked_from``: a second fork overwrites it,
    the chain is not kept. Both fields change in one write.
    """
    old = plan_wiki_fork(wiki_root, wiki_id=wiki_id)
    new = wiki_id or new_wiki_id()
    config = wiki_root / "config.md"

    def transform(block: str) -> str:
        block = _frontmatter.set_field_in_block(block, WIKI_ID_KEY, new)
        return _frontmatter.set_field_in_block(
            block, WIKI_FORKED_FROM_KEY, old, after=WIKI_ID_KEY
        )

    try:
        _frontmatter.update(config, transform)
    except (OSError, UnicodeDecodeError, _frontmatter.FrontmatterError) as exc:
        raise _frontmatter_exit(config, exc) from exc
    return old, new


def registry_entries_of(hub_root: Path, wiki_root: Path, wiki_id: str) -> list[str]:
    """Slugs of the ``wikis`` entries that carry ``id: wiki_id`` and resolve to ``wiki_root``.

    Hub lint verifies each such entry against the wiki it resolves to, so a fork
    that changes the wiki's id must change these ids in the same operation.
    """
    data = _common.read_registry(hub_root)
    wikis = data.get("wikis")
    found: list[str] = []
    for slug, entry in (wikis.items() if isinstance(wikis, dict) else ()):
        if not isinstance(entry, dict) or entry.get("id") != wiki_id:
            continue
        if _roots.same_path(_roots.registry_entry_path(hub_root, slug, entry), wiki_root):
            found.append(slug)
    return found


def retarget_registry_entries(hub_root: Path, slugs: list[str], new_id: str) -> None:
    """Set ``id`` of the named ``wikis`` entries to ``new_id`` (layout preserved)."""
    data = _common.read_registry(hub_root)
    for slug in slugs:
        data["wikis"][slug]["id"] = new_id
    _common.write_registry(hub_root, data)


def read_hub_id(hub_root: Path) -> str | None:
    """Return the identity a hub declares in ``wikis.json``, if it declares one."""
    try:
        data = _fsio.read_json_object(hub_root / "wikis.json", "wiki registry")
    except _fsio.JSONReadError:
        return None
    value = data.get(HUB_ID_KEY)
    return value if isinstance(value, str) and is_hub_id(value) else None


def read_hub_forked_from(hub_root: Path) -> str | None:
    """Return the id this hub was forked from, if ``wikis.json`` records one."""
    try:
        data = _fsio.read_json_object(hub_root / "wikis.json", "wiki registry")
    except _fsio.JSONReadError:
        return None
    value = data.get(HUB_FORKED_FROM_KEY)
    return value if isinstance(value, str) and is_hub_id(value) else None


def _require_registry(hub_root: Path, verb: str) -> dict[str, Any]:
    """The hub's registry, or one line saying why ``identity <verb> --hub`` cannot run."""
    if not (hub_root / "wikis.json").is_file():
        hint = (
            "; this is a wiki root: pass it without --hub"
            if (hub_root / "config.md").is_file()
            else ""
        )
        raise SystemExit(f"no wikis.json at {hub_root}; identity {verb} --hub needs a hub root{hint}")
    return _common.read_registry(hub_root)


def plan_hub_assignment(hub_root: Path, *, hub_id: str | None = None) -> str | None:
    """Validate an ``identity assign --hub`` request; return the id already declared.

    The same refuse-to-replace rule as :func:`plan_assignment`.
    """
    _require_registry(hub_root, "assign")
    if hub_id is not None and not is_hub_id(hub_id):
        raise SystemExit(f"not a valid hub id: {hub_id}")
    existing = read_hub_id(hub_root)
    if existing and hub_id is not None and hub_id != existing:
        raise SystemExit(
            f"{hub_root / 'wikis.json'} already declares {HUB_ID_KEY} {existing}; "
            f"refusing to replace it with {hub_id}"
        )
    return existing


def _registry_with_hub_keys(
    data: dict[str, Any], hub_id: str, forked_from: str | None
) -> dict[str, Any]:
    """``data`` with ``hub_id`` (and ``hub_forked_from``) declared, in key order.

    An existing ``hub_id`` keeps its place and a new one goes first. The fork
    record sits directly after it and replaces any earlier one.
    """
    head: dict[str, Any] = {HUB_ID_KEY: hub_id}
    if forked_from is not None:
        head[HUB_FORKED_FROM_KEY] = forked_from
    if HUB_ID_KEY not in data:
        rest = {k: v for k, v in data.items() if k != HUB_FORKED_FROM_KEY or forked_from is None}
        return {**head, **rest}
    out: dict[str, Any] = {}
    for key, value in data.items():
        if key == HUB_ID_KEY:
            out.update(head)
        elif key == HUB_FORKED_FROM_KEY and forked_from is not None:
            continue
        else:
            out[key] = value
    return out


def assign_hub_id(hub_root: Path, *, hub_id: str | None = None) -> tuple[str, bool]:
    """Ensure the hub declares an identity; return it and whether it was created."""
    existing = plan_hub_assignment(hub_root, hub_id=hub_id)
    if existing:
        return existing, False
    value = hub_id or new_hub_id()
    data = _common.read_registry(hub_root)
    _common.write_registry(hub_root, _registry_with_hub_keys(data, value, None))
    return value, True


def plan_hub_fork(hub_root: Path, *, hub_id: str | None = None) -> str:
    """Validate an ``identity fork --hub``; return the id it will replace."""
    _require_registry(hub_root, "fork")
    registry = hub_root / "wikis.json"
    existing = read_hub_id(hub_root)
    if existing is None:
        raise SystemExit(
            f"{registry} declares no {HUB_ID_KEY}: nothing to fork from; use identity assign --hub"
        )
    if hub_id is not None:
        if not is_hub_id(hub_id):
            raise SystemExit(f"not a valid hub id: {hub_id}")
        if hub_id == existing:
            raise SystemExit(
                f"{registry} already declares {HUB_ID_KEY} {existing}; --id must be a different id"
            )
    return existing


def fork_hub_id(hub_root: Path, *, hub_id: str | None = None) -> tuple[str, str]:
    """Give a hub a new identity and record the old one as ``hub_forked_from``.

    The topics' ``wiki_id`` values are not touched: a copy of a hub keeps the
    ids of its wikis, so it remains a replica of those wikis until each one is
    forked.
    """
    old = plan_hub_fork(hub_root, hub_id=hub_id)
    new = hub_id or new_hub_id()
    data = _common.read_registry(hub_root)
    _common.write_registry(hub_root, _registry_with_hub_keys(data, new, old))
    return old, new


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


def status_payload(wiki_root: Path, *, hub_root: Path | None = None) -> dict[str, Any]:
    """The status of a wiki root; ``hub_id`` appears when the wiki sits in a hub."""
    wiki_id = read_wiki_id(wiki_root)
    payload: dict[str, Any] = {
        "root": str(wiki_root),
        "kind": "wiki",
        "wiki_id": wiki_id,
        "declared": wiki_id is not None,
    }
    forked_from = read_wiki_forked_from(wiki_root)
    if forked_from is not None:
        payload["wiki_forked_from"] = forked_from
    if hub_root is not None:
        payload["hub_id"] = read_hub_id(hub_root)
    return payload


def hub_status_payload(hub_root: Path) -> dict[str, Any]:
    """The status of a hub root: its ``hub_id`` and each registry topic's ``wiki_id``."""
    data = _require_registry(hub_root, "status")
    hub_id = read_hub_id(hub_root)
    topics: list[dict[str, Any]] = []
    wikis = data.get("wikis")
    for slug, entry in (wikis.items() if isinstance(wikis, dict) else ()):
        if not isinstance(entry, dict):
            continue
        root = _roots.registry_entry_path(hub_root, slug, entry)
        if _roots.same_path(root, hub_root):
            continue  # the hub's own registry row is the hub, not a topic
        topics.append(
            {
                "slug": slug,
                "path": entry.get("path"),
                "wiki_id": read_wiki_id(root),
                "mismatch": verify_entry(entry.get("id"), root),
            }
        )
    payload: dict[str, Any] = {
        "root": str(hub_root),
        "kind": "hub",
        "hub_id": hub_id,
        "declared": hub_id is not None,
        "topics": topics,
    }
    forked_from = read_hub_forked_from(hub_root)
    if forked_from is not None:
        payload["hub_forked_from"] = forked_from
    return payload


def _print_hub_status(payload: dict[str, Any]) -> None:
    print(f"Knowmade identity: {payload['root']} (hub)")
    print(f"hub_id: {payload['hub_id'] or 'none'}")
    if "hub_forked_from" in payload:
        print(f"hub_forked_from: {payload['hub_forked_from']}")
    if not payload["declared"]:
        print("Next: llm-wiki identity assign --hub")
    for topic in payload["topics"]:
        line = f"  {topic['slug']}: {topic['wiki_id'] or 'none'}"
        if topic["mismatch"]:
            line += f" (mismatch: {topic['mismatch']})"
        print(line)


def _is_hub_target(args: Any) -> bool:
    """``--hub`` with no wiki scope names the hub itself rather than overriding it."""
    if getattr(args, "hub", None) is None:
        return False
    return not (
        getattr(args, "path", None) or getattr(args, "local", False) or getattr(args, "wiki", None)
    )


def _run_hub(args: Any, hub_root: Path, command: str) -> int:
    as_json = bool(getattr(args, "json", False))
    requested = getattr(args, "id", None)
    dry_run = bool(getattr(args, "dry_run", False))

    if command == "status":
        payload = hub_status_payload(hub_root)
        if as_json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            _print_hub_status(payload)
        return 0

    if command == "assign":
        if dry_run:
            existing = plan_hub_assignment(hub_root, hub_id=requested)
            print(existing or requested or "would assign a new hub_id")
            return 0
        value, created = assign_hub_id(hub_root, hub_id=requested)
        print(f"{'assigned' if created else 'already declared'}: {value}")
        return 0

    if command == "fork":
        if dry_run:
            old = plan_hub_fork(hub_root, hub_id=requested)
            print(f"would fork: {old} -> {requested or 'a new hub_id'}")
            return 0
        old, new = fork_hub_id(hub_root, hub_id=requested)
        print(f"forked: {old} -> {new}")
        return 0

    raise SystemExit(f"unknown identity command: {command}")


def run_identity(args: Any) -> int:
    """`llm-wiki identity status|assign|fork` — read, create or re-issue a stable id."""
    command = getattr(args, "identity_command", "status")

    if _is_hub_target(args):
        selected = _roots.resolve_hub_selection(getattr(args, "hub", None) or None)
        return _run_hub(args, selected.path, command)

    identity = _roots.resolve_wiki_identity_args(args)
    root = identity.path

    if command == "status":
        # A hub root (resolved, or a path with a registry and no wiki config) reports as a hub.
        if identity.kind == "hub" or (
            identity.kind == "path"
            and (root / "wikis.json").is_file()
            and not (root / "config.md").is_file()
        ):
            return _run_hub(args, root, command)
        hub_root = identity.hub if identity.kind == "topic" else None
        if hub_root is None:
            hub_root = _roots.enclosing_hub(root)
        payload = status_payload(root, hub_root=hub_root)
        if getattr(args, "json", False):
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"Knowmade identity: {root}")
            print(f"wiki_id: {payload['wiki_id'] or 'none'}")
            if "wiki_forked_from" in payload:
                print(f"wiki_forked_from: {payload['wiki_forked_from']}")
            if "hub_id" in payload:
                print(f"hub_id: {payload['hub_id'] or 'none'}")
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

    if command == "fork":
        requested = getattr(args, "id", None)
        old = plan_wiki_fork(root, wiki_id=requested)
        # A registry row that carries the old id would turn hub lint critical.
        hub_root = identity.hub if identity.kind == "topic" else _roots.enclosing_hub(root)
        slugs: list[str] = []
        if hub_root is not None and (hub_root / "wikis.json").is_file():
            try:
                slugs = registry_entries_of(hub_root, root, old)
            except _fsio.JSONReadError:
                slugs = []
        if getattr(args, "dry_run", False):
            print(f"would fork: {old} -> {requested or 'a new wiki_id'}")
            for slug in slugs:
                print(f"would also update registry entry {slug}")
            return 0
        old, new = fork_wiki_id(root, wiki_id=requested)
        print(f"forked: {old} -> {new}")
        if slugs and hub_root is not None:
            retarget_registry_entries(hub_root, slugs, new)
            for slug in slugs:
                print(f"updated registry entry {slug}")
        return 0

    raise SystemExit(f"unknown identity command: {command}")
