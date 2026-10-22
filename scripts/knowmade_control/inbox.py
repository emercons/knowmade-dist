"""The ``inbox`` namespace: the deterministic half of hub inbox dispatch (KM-INBOX-005).

``HUB/inbox/`` is the unclassified human intake queue (``references/hub-dispatch.md``).
Choosing which topic an item belongs to is the agent's fuzzy judgement; this
module does everything around that choice in a way that can be audited:

- ``inbox list`` shows the items not yet routed (hidden entries and the
  ``.routed/``, ``.needs-human/`` and ``.skipped/`` holding directories are not
  items), each with the privacy hints the router can decide on;
- ``inbox route <item> --to <slug> [--to <slug> ...]`` copies the item into the
  inbox of every named topic (``<topic root>/inbox/``, the registry's path, so a
  nested ``.wiki`` and an overlay are honoured), adds the provenance the
  protocol asks for, moves the original to ``HUB/inbox/.routed/`` only after
  every copy exists, and appends one ``dispatch`` entry to ``HUB/log.md``;
- ``inbox hold <item> --as needs-human|skipped`` is the protocol's other two
  verdicts: the original moves to ``.needs-human/`` or ``.skipped/`` and the
  log gets the same kind of entry.

Provenance follows the protocol: a markdown capture (``.md``/``.markdown``,
UTF-8) gets ``routed_from`` (and ``route_reason``/``route_confidence`` when
given) in its frontmatter, never overriding a key it already has; any other file
is copied byte for byte with a ``<name>.route.md`` sidecar note next to it.

Everything is checked before the first write, and a refusal changes nothing:

- an unknown, archived or uninitialized topic slug;
- a destination (copy or sidecar) that already holds different bytes; the same
  bytes (the copy as this command would write it, or the original verbatim)
  count as done, so a re-run is idempotent and finishes an interrupted route;
- an original already in ``.routed/`` with different bytes;
- a private item routed to a public topic. Private is what can be decided
  without reading meaning (``private-boundary.md``):
  a ``private`` name hint (``private - x.md``, ``x.private.md``, an
  uppercase ``-PRIVATE`` token), a privacy/visibility/sensitivity/classification
  frontmatter value or ``private: true``, a path into a ``*-PRIVATE`` location or
  a private topic, or a ``-PRIVATE`` path string in the text. A private
  target may take anything; one public target among several refuses the
  whole route;
- a Markdown item that is not strict UTF-8 routed to a public topic: its
  frontmatter cannot be trusted, so only a private topic may take it (the
  frontmatter privacy check itself reads the replacement-decoded text of every
  item whatever its extension);
- an item name with a control, line-separator or bidi-override character
  (the log line and the sidecar embed the name verbatim), or a ``--reason``
  with one, and into a public topic a ``--reason`` the checkpoint scan marks
  critical (a private path, a machine-local path, a secret);
- a topic ``inbox/`` (or a ``.routed/``/``.needs-human/``/``.skipped/``) that is
  a symlink or junction, or whose real path is not its parent's real path plus
  its name; private or public is judged on where the copy really lands, and the
  check is repeated just before the first write;
- a missing ``HUB/log.md``, because a route nobody can audit is not made.

``--dry-run`` runs every check, prints the plan and writes nothing. Exit
codes: 0 done (or planned, or already done), 1 a refusal, 2 an unusable hub
or registry.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import fsio as _fsio
from .checkpoint_scan import scan_checkpoint_text
from .common import (
    PRIVATE_SUFFIX,
    active_topic_path,
    read_registry,
    topic_entry,
)
from .hub import hub_relative
from .markdown import (
    BOM,
    append_log,
    detect_newline,
    frontmatter_bounds,
    parse_frontmatter_block,
    yaml_quote,
)
from .privacy_boundary import (
    BoundaryChecker,
    BoundaryRegistryError,
    private_topic_dirs,
    is_private_path,
    real_directory,
    split_real,
)
from .redaction import pattern_specs
from .roots import (
    initialized_wiki_root,
    is_archived_registry_entry,
    resolve_hub_args as resolve_hub,
    same_path,
)

INBOX_DIR = "inbox"
ROUTED_DIR = ".routed"
HOLD_DIRS = {"needs-human": ".needs-human", "skipped": ".skipped"}
HOLDING_DIRS = (ROUTED_DIR, *HOLD_DIRS.values())
MARKDOWN_SUFFIXES = (".md", ".markdown")
SIDECAR_SUFFIX = ".route.md"
UNVERIFIABLE_HINT = "Markdown is not valid UTF-8, so its privacy markers cannot be verified"
CONFIDENCES = ("high", "medium", "low")
PRIVATE_VALUES = {"private", "personal", "confidential", "secret", "sensitive"}
PRIVACY_KEYS = ("privacy", "visibility", "sensitivity", "classification")
# A leading `private - ` hint or a `.private` infix before the extension (any
# case), or the uppercase `-PRIVATE` token topic names use (case-sensitive, as
# in the redaction table). "private equity.md" is not a hint.
PRIVATE_HINT_RE = re.compile(r"^private\s*[-\u2013\u2014]\s|\.private(?=\.[^.]+$|$)", re.IGNORECASE)
PRIVATE_TOKEN_RE = re.compile(r"(?<![\w.-])[\w.-]+-PRIVATE(?![\w-])")


class Refusal(Exception):
    """The command declines to change anything (exit 1); carries every reason found."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


class Unusable(Exception):
    """The hub or its registry cannot be used at all (exit 2)."""


@dataclass
class Target:
    slug: str
    private: bool
    inbox: Path
    copy_path: Path
    copy_bytes: bytes
    sidecar_path: Path | None
    sidecar_bytes: bytes | None
    action: str  # "copy" | "unchanged"
    sidecar_action: str | None = None


@dataclass
class Plan:
    hub: Path
    name: str
    source: Path
    state: str  # "inbox" | "routed"
    sha256: str
    private_reasons: list[str]
    unverifiable: bool = False
    targets: list[Target] = field(default_factory=list)
    hold: str | None = None
    move_to: Path | None = None
    move_needed: bool = False
    unlink_source_only: bool = False


# ---------------------------------------------------------------------------
# hub, items, privacy hints


def _hub_and_registry(args: argparse.Namespace) -> tuple[Path, dict[str, Any]]:
    try:
        hub = resolve_hub(args)
        if not (hub / "wikis.json").is_file() or not (hub / "topics").is_dir():
            raise Unusable(f"initialized hub not found: {hub}")
        return hub, read_registry(hub)
    except SystemExit as exc:
        raise Unusable(str(exc.code)) from None


def _has_unsafe_char(text: str) -> bool:
    """A control, line/paragraph separator or bidi-override character (never printable text)."""
    return any(
        unicodedata.category(ch) in {"Cc", "Zl", "Zp"} or "\u202a" <= ch <= "\u202e" or "\u2066" <= ch <= "\u2069"
        for ch in text
    )


def _is_item_name(name: str) -> bool:
    """A plain, visible, single-line file name: the log line and the sidecar embed it verbatim."""
    return (
        bool(name)
        and name not in {".", ".."}
        and not name.startswith(".")
        and not any(sep in name for sep in ("/", "\\"))
        and not _has_unsafe_char(name)
    )


def _display(name: str) -> str:
    """A name for the terminal: control characters shown as escapes, never executed."""
    return "".join(f"\\u{ord(ch):04x}" if _has_unsafe_char(ch) else ch for ch in name)


def _resolve_item_name(hub: Path, raw: str) -> str:
    """The item's file name from ``name``, ``inbox/name`` or a path directly inside the inbox."""
    inbox = hub / INBOX_DIR
    path = Path(raw)
    parts = path.parts
    if not path.is_absolute() and len(parts) == 1:
        name = raw
    elif not path.is_absolute() and len(parts) == 2 and parts[0] == INBOX_DIR:
        name = parts[1]
    elif len(parts) > 1 and same_path(path.parent, inbox):
        name = path.name
    else:
        raise Refusal([f"{raw!r} is not an item directly inside {INBOX_DIR}/ of {hub}"])
    if not _is_item_name(name):
        raise Refusal([f"{raw!r} is not an inbox item name (hidden, empty, or containing a separator or control character)"])
    return name


def _kind(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if path.is_dir():
        return "directory"
    if path.is_file():
        return "file"
    return "other"


def _frontmatter_text(data: bytes, name: str) -> tuple[str | None, str | None]:
    """``(text, None)`` for a UTF-8 markdown capture, else ``(None, decoded-with-replacement)``."""
    if name.lower().endswith(MARKDOWN_SUFFIXES):
        try:
            return data.decode("utf-8"), None
        except UnicodeDecodeError:
            pass
    return None, data.decode("utf-8", errors="replace")


def is_unverifiable(data: bytes, name: str) -> bool:
    """A Markdown name whose bytes are not strict UTF-8: its privacy markers cannot be trusted."""
    return name.lower().endswith(MARKDOWN_SUFFIXES) and _frontmatter_text(data, name)[0] is None


def privacy_hints(hub: Path, path: Path, name: str, data: bytes) -> list[str]:
    """Every deterministic reason the item counts as private (empty: nothing found).

    The frontmatter is read from the replacement-decoded text whatever the
    extension or the decode result, so a stray byte never skips the check; a
    Markdown item that is not strict UTF-8 is additionally unverifiable.
    """
    reasons: list[str] = []
    if PRIVATE_HINT_RE.search(name) or PRIVATE_TOKEN_RE.search(name):
        reasons.append("name carries a private marker")
    if is_unverifiable(data, name):
        reasons.append(UNVERIFIABLE_HINT)
    searchable = data.decode("utf-8", errors="replace")
    bounds = frontmatter_bounds(searchable)
    if bounds is not None:
        block = parse_frontmatter_block(searchable[bounds[0] : bounds[1]])
        for key in PRIVACY_KEYS:
            if str(block.get(key, "")).strip().lower() in PRIVATE_VALUES:
                reasons.append(f"frontmatter {key}: {block[key]}")
        if block.get("private") is True:
            reasons.append("frontmatter private: true")
    for category, _severity, _message, pattern in pattern_specs("checkpoint"):
        if category == "private-topic-path" and pattern.search(searchable):
            reasons.append("text names a -PRIVATE path")
            break
    reasons.extend(_location_reasons(hub, path))
    return reasons


def _location_reasons(hub: Path, path: Path) -> list[str]:
    """A ``*-PRIVATE`` component below the hub, or a real path inside a private topic."""
    try:
        topics = [real_directory(topic) for topic in private_topic_dirs(hub)]
    except BoundaryRegistryError as exc:
        raise Unusable(str(exc)) from None
    real = split_real(path)
    hub_parts = real_directory(hub)
    inside = len(real) >= len(hub_parts) and all(
        os.path.normcase(a) == os.path.normcase(b) for a, b in zip(hub_parts, real)
    )
    below = real[len(hub_parts) :] if inside else real
    in_topic = any(
        len(real) >= len(t) and all(os.path.normcase(a) == os.path.normcase(b) for a, b in zip(t, real))
        for t in topics
    )
    return ["item lies in a private location"] if is_private_path(below) or in_topic else []


# ---------------------------------------------------------------------------
# provenance


def _provenance_fields(name: str, reason: str | None, confidence: str | None) -> list[tuple[str, str]]:
    fields = [("routed_from", yaml_quote(f"HUB/{INBOX_DIR}/{name}"))]
    if reason:
        fields.append(("route_reason", yaml_quote(reason)))
    if confidence:
        fields.append(("route_confidence", yaml_quote(confidence)))
    return fields


def _reason_leaks(reason: str | None) -> list[str]:
    """Critical scan categories in ``reason``; a public copy never carries one as ``route_reason``."""
    if not reason:
        return []
    return sorted(
        {f.category for f in scan_checkpoint_text(reason, "route_reason") if f.severity == "critical"}
    )


def copy_with_provenance(
    name: str, data: bytes, reason: str | None, confidence: str | None
) -> tuple[bytes, bytes | None]:
    """``(copy bytes, sidecar bytes)``: frontmatter for markdown, a sidecar note otherwise."""
    fields = _provenance_fields(name, reason, confidence)
    text, _ = _frontmatter_text(data, name)
    if text is None:
        lines = "".join(f"{key}: {value}\n" for key, value in fields)
        note = (
            f"---\n{lines}---\n\nRouting note for `{name}`, copied from the hub inbox "
            "byte for byte by `llm-wiki inbox route`. The ingest worker reads these "
            "fields as the item's provenance.\n"
        )
        return data, note.encode("utf-8")
    newline = detect_newline(text)
    bounds = frontmatter_bounds(text)
    if bounds is None:
        head = BOM if text.startswith(BOM) else ""
        block = f"---{newline}" + "".join(f"{k}: {v}{newline}" for k, v in fields) + f"---{newline}{newline}"
        return (head + block + text[len(head) :]).encode("utf-8"), None
    start, end, body_start = bounds
    present = parse_frontmatter_block(text[start:end])
    added = "".join(f"{k}: {v}{newline}" for k, v in fields if k not in present)
    fence = body_start - 3
    prefix = text[:fence]
    if added and prefix and not prefix.endswith("\n"):
        prefix += newline
    return (prefix + added + text[fence:]).encode("utf-8"), None


# ---------------------------------------------------------------------------
# planning


def _same_bytes(path: Path, data: bytes) -> bool:
    try:
        return path.is_file() and not path.is_symlink() and path.read_bytes() == data
    except OSError:
        return False


def _known_slugs(hub: Path, registry: dict[str, Any]) -> list[str]:
    wikis = registry.get("wikis", {})
    return sorted(
        str(slug)
        for slug, entry in wikis.items()
        if slug != "hub" and isinstance(entry, dict) and not is_archived_registry_entry(entry)
    )


def _is_junction(path: Path) -> bool:
    check = getattr(os.path, "isjunction", None)
    return bool(check and check(path))


def _verify_real_child(parent: Path, child: Path) -> None:
    """Refuse a ``child`` that is a link or whose real path is not ``realpath(parent)/name``."""
    if not os.path.lexists(child):
        return
    if child.is_symlink() or _is_junction(child):
        raise Refusal([f"{child} is a symbolic link or junction; refusing to write through it"])
    expected = os.path.join(os.path.realpath(parent), child.name)
    if os.path.normcase(os.path.realpath(child)) != os.path.normcase(expected):
        raise Refusal([f"{child} resolves to {os.path.realpath(child)}, outside {os.path.realpath(parent)}"])


def _topic_inbox(hub: Path, registry: dict[str, Any], slug: str) -> tuple[Path, Path]:
    """``(topic root, topic inbox)`` of an active registered topic, or a Refusal."""
    if slug == "hub" or topic_entry(registry, slug) is None:
        known = ", ".join(_known_slugs(hub, registry)) or "none"
        raise Refusal([f"unknown topic slug: {slug} (registered topics: {known})"])
    entry = topic_entry(registry, slug)
    if is_archived_registry_entry(entry):
        raise Refusal([f"topic {slug} is archived; restore it first (llm-wiki archive restore {slug})"])
    root = active_topic_path(hub, slug, entry)
    if not initialized_wiki_root(root):
        raise Refusal([f"topic {slug} is not an initialized wiki at {root}"])
    inbox = root / INBOX_DIR
    _verify_real_child(root, inbox)
    if inbox.exists() and not inbox.is_dir():
        raise Refusal([f"{inbox} exists and is not a directory"])
    return root, inbox


def build_plan(
    hub: Path,
    registry: dict[str, Any],
    raw_item: str,
    slugs: list[str],
    *,
    hold: str | None,
    reason: str | None,
    confidence: str | None,
) -> Plan:
    refusals: list[str] = []
    name = _resolve_item_name(hub, raw_item)
    inbox = hub / INBOX_DIR
    routed = inbox / ROUTED_DIR / name
    if os.path.lexists(inbox / name):
        source, state = inbox / name, "inbox"
    elif hold is None and routed.is_file():
        source, state = routed, "routed"
    else:
        raise Refusal([f"no item named {name!r} in {inbox} (llm-wiki inbox list shows what is waiting)"])
    kind = _kind(source)
    if kind != "file":
        raise Refusal([f"{source} is a {kind}; only regular files are routed (move it by hand)"])
    if not (hub / "log.md").is_file():
        refusals.append(f"{hub / 'log.md'} is missing; a dispatch without a log entry is not auditable")
    data = source.read_bytes()
    plan = Plan(
        hub=hub,
        name=name,
        source=source,
        state=state,
        sha256=hashlib.sha256(data).hexdigest(),
        private_reasons=privacy_hints(hub, source, name, data),
        unverifiable=is_unverifiable(data, name),
        hold=hold,
    )

    try:
        for holding in HOLDING_DIRS:
            _verify_real_child(inbox, inbox / holding)
    except Refusal as exc:
        refusals.extend(exc.reasons)
    if hold is not None:
        dest = inbox / HOLD_DIRS[hold] / name
        plan.move_to = dest
        if os.path.lexists(dest):
            if _same_bytes(dest, data):
                plan.unlink_source_only = True
            else:
                refusals.append(f"{dest} already holds a different file named {name}; rename the item first")
        plan.move_needed = True
    else:
        _plan_targets(plan, hub, registry, data, slugs, reason, confidence, refusals)
        if state == "inbox":
            plan.move_to = routed
            plan.move_needed = True
            if os.path.lexists(routed):
                if _same_bytes(routed, data):
                    plan.unlink_source_only = True
                else:
                    refusals.append(f"{routed} already holds a different file named {name}; rename the item first")
    if refusals:
        raise Refusal(refusals)
    return plan


def _plan_targets(
    plan: Plan,
    hub: Path,
    registry: dict[str, Any],
    data: bytes,
    slugs: list[str],
    reason: str | None,
    confidence: str | None,
    refusals: list[str],
) -> None:
    try:
        checker = BoundaryChecker(hub, private_dirs=private_topic_dirs(hub))
    except BoundaryRegistryError as exc:
        raise Unusable(str(exc)) from None
    copy_bytes, sidecar_bytes = copy_with_provenance(plan.name, data, reason, confidence)
    seen: set[str] = set()
    for slug in slugs:
        if slug in seen:
            continue
        seen.add(slug)
        try:
            root, topic_inbox = _topic_inbox(hub, registry, slug)
        except Refusal as exc:
            refusals.extend(exc.reasons)
            continue
        # Judged on where the copy and the sidecar really land, not on the spelling.
        private = checker.is_private_location(real_directory(topic_inbox))
        if plan.unverifiable and not private:
            refusals.append(
                f"{plan.name} is a Markdown file that is not valid UTF-8, so its privacy markers "
                f"cannot be verified and {slug} is a public topic; route it to {slug}{PRIVATE_SUFFIX} "
                "or re-encode the file as UTF-8"
            )
            continue
        if plan.private_reasons and not private:
            refusals.append(
                f"{plan.name} is private ({'; '.join(plan.private_reasons)}) and {slug} is a public "
                f"topic; route it to {slug}{PRIVATE_SUFFIX} or rename the item if the hint is wrong"
            )
            continue
        leaked = _reason_leaks(reason) if not private else []
        if leaked:
            refusals.append(
                f"--reason carries private or machine-local material ({', '.join(leaked)}) and {slug} "
                f"is a public topic; reword it or route to {slug}{PRIVATE_SUFFIX}"
            )
            continue
        target = Target(
            slug=slug,
            private=private,
            inbox=topic_inbox,
            copy_path=topic_inbox / plan.name,
            copy_bytes=copy_bytes,
            sidecar_path=topic_inbox / (plan.name + SIDECAR_SUFFIX) if sidecar_bytes is not None else None,
            sidecar_bytes=sidecar_bytes,
            action="copy",
        )
        _check_destination(target, data, refusals)
        plan.targets.append(target)


def _check_destination(target: Target, original: bytes, refusals: list[str]) -> None:
    path = target.copy_path
    if os.path.lexists(path):
        if _same_bytes(path, target.copy_bytes) or _same_bytes(path, original):
            target.action = "unchanged"
        else:
            refusals.append(
                f"{path} already holds a different file named {path.name}; nothing was overwritten"
            )
    if target.sidecar_path is not None and target.sidecar_bytes is not None:
        side = target.sidecar_path
        if os.path.lexists(side):
            if _same_bytes(side, target.sidecar_bytes):
                target.sidecar_action = "unchanged"
            else:
                refusals.append(f"{side} already holds a different file; nothing was overwritten")
        else:
            target.sidecar_action = "copy"


# ---------------------------------------------------------------------------
# applying


def log_message(plan: Plan) -> str:
    """The audit line body: slug-only for topics, the hash to tie the entry to the bytes."""
    where = f".{plan.hold}" if plan.hold else ", ".join(t.slug for t in plan.targets)
    line = f"{plan.name} → {where} (sha256 {plan.sha256[:12]})"
    if _has_unsafe_char(line):  # defence in depth: names are validated, the log stays one line
        line = "".join(f"\\u{ord(ch):04x}" if _has_unsafe_char(ch) else ch for ch in line)
    return line


def apply_plan(plan: Plan) -> dict[str, Any]:
    """Write the copies, move the original, log. Returns what was done."""
    done: dict[str, Any] = {"copied": [], "moved": False, "logged": False}
    # Nothing is written until every destination still checks out (the plan may be stale).
    for target in plan.targets:
        _verify_real_child(target.inbox.parent, target.inbox)
    holding = plan.hub / INBOX_DIR
    if plan.move_needed and plan.move_to is not None and plan.move_to.parent != holding:
        _verify_real_child(holding, plan.move_to.parent)
    for target in plan.targets:
        if target.action == "copy" or target.sidecar_action == "copy":
            target.inbox.mkdir(parents=True, exist_ok=True)
        if target.action == "copy":
            _fsio.atomic_write_bytes(target.copy_path, target.copy_bytes)
            done["copied"].append(target.slug)
        if target.sidecar_action == "copy" and target.sidecar_path is not None and target.sidecar_bytes is not None:
            _fsio.atomic_write_bytes(target.sidecar_path, target.sidecar_bytes)
    if plan.move_needed and plan.move_to is not None:
        plan.move_to.parent.mkdir(parents=True, exist_ok=True)
        if plan.unlink_source_only:
            plan.source.unlink()
        else:
            _fsio._replace(plan.source, plan.move_to)  # noqa: SLF001 - fsio's retrying rename
        done["moved"] = True
    if done["copied"] or done["moved"]:
        append_log(plan.hub, "dispatch", log_message(plan))
        done["logged"] = True
    return done


# ---------------------------------------------------------------------------
# output


def _rel(plan: Plan, path: Path | None) -> str | None:
    return None if path is None else hub_relative(plan.hub, path)


def plan_payload(plan: Plan, *, dry_run: bool, done: dict[str, Any] | None) -> dict[str, Any]:
    already = (
        plan.hold is None
        and plan.state == "routed"
        and all(t.action == "unchanged" and t.sidecar_action in (None, "unchanged") for t in plan.targets)
    )
    return {
        "ok": True,
        "dry_run": dry_run,
        "hub": str(plan.hub),
        "item": plan.name,
        "sha256": plan.sha256,
        "state": plan.state,
        "private": bool(plan.private_reasons),
        "private_reasons": plan.private_reasons,
        "hold": plan.hold,
        "targets": [
            {
                "slug": t.slug,
                "private": t.private,
                "action": t.action,
                "copy": _rel(plan, t.copy_path),
                "sidecar": _rel(plan, t.sidecar_path),
                "sidecar_action": t.sidecar_action,
            }
            for t in plan.targets
        ],
        "move_to": _rel(plan, plan.move_to) if plan.move_needed else None,
        "already_routed": already,
        "log_entry": f"dispatch | {log_message(plan)}" if not already else None,
        "done": done,
    }


def _print_plan(payload: dict[str, Any]) -> None:
    prefix = "Plan (dry run)" if payload["dry_run"] else "Done"
    if payload["already_routed"]:
        prefix = "Already routed" + (" (dry run)" if payload["dry_run"] else "")
    print(f"{prefix}: {payload['item']} (sha256 {payload['sha256'][:12]})")
    if payload["private"]:
        print(f"  private item: {'; '.join(payload['private_reasons'])}")
    for target in payload["targets"]:
        verb = "copy to" if target["action"] == "copy" else "already in"
        suffix = " (private topic)" if target["private"] else ""
        print(f"  {verb} {target['copy']}{suffix}")
        if target["sidecar"]:
            side = "write" if target["sidecar_action"] == "copy" else "already has"
            print(f"  {side} sidecar {target['sidecar']}")
    if payload["move_to"]:
        print(f"  move original to {payload['move_to']}")
    if payload["log_entry"]:
        print(f"  log: ## [date] {payload['log_entry']}")


def _refusal(args: argparse.Namespace, reasons: list[str]) -> int:
    if getattr(args, "json", False):
        print(json.dumps({"ok": False, "refusals": reasons}, indent=2, ensure_ascii=False))
    for reason in reasons:
        print(f"llm-wiki inbox: refused: {reason}", file=sys.stderr)
    return 1


# ---------------------------------------------------------------------------
# commands


def run_list(args: argparse.Namespace) -> int:
    hub, registry = _hub_and_registry(args)
    inbox = hub / INBOX_DIR
    items: list[dict[str, Any]] = []
    held = {name: 0 for name in HOLDING_DIRS}
    if inbox.is_dir():
        for entry in sorted(inbox.iterdir(), key=lambda p: p.name.lower()):
            if entry.name in HOLDING_DIRS:
                held[entry.name] = sum(1 for child in entry.iterdir() if not child.name.startswith("."))
                continue
            if entry.name.startswith("."):
                continue
            kind = _kind(entry)
            record: dict[str, Any] = {
                "name": entry.name,
                "kind": kind,
                "routable": kind == "file" and _is_item_name(entry.name),
            }
            if kind == "file":
                data = entry.read_bytes()
                record["size"] = len(data)
                record["sha256"] = hashlib.sha256(data).hexdigest()
                record["private_hints"] = privacy_hints(hub, entry, entry.name, data)
            items.append(record)
    payload = {
        "hub": str(hub),
        "inbox": hub_relative(hub, inbox),
        "items": items,
        "held": held,
        "topics": _known_slugs(hub, registry),
    }
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if not items:
        print(f"No unrouted items in {inbox}.")
    for item in items:
        flags = []
        if not item["routable"]:
            flags.append(f"not routable: {item['kind']}")
        if item.get("private_hints"):
            flags.append("private: " + "; ".join(item["private_hints"]))
        size = f"  {item['size']} bytes" if "size" in item else ""
        print(f"{_display(item['name'])}{size}" + (f"  [{' | '.join(flags)}]" if flags else ""))
    counts = ", ".join(f"{name}: {count}" for name, count in held.items() if count)
    if counts:
        print(f"Held ({counts}).")
    return 0


def run_route(args: argparse.Namespace) -> int:
    hub, registry = _hub_and_registry(args)
    if args.reason and _has_unsafe_char(" ".join(args.reason.split())):
        return _refusal(args, ["--reason must be plain text without control characters"])
    try:
        plan = build_plan(
            hub,
            registry,
            args.item,
            args.to,
            hold=None,
            reason=args.reason,
            confidence=args.confidence,
        )
    except Refusal as exc:
        return _refusal(args, exc.reasons)
    return _finish(args, plan)


def run_hold(args: argparse.Namespace) -> int:
    hub, registry = _hub_and_registry(args)
    try:
        plan = build_plan(
            hub, registry, args.item, [], hold=args.verdict, reason=None, confidence=None
        )
    except Refusal as exc:
        return _refusal(args, exc.reasons)
    return _finish(args, plan)


def _finish(args: argparse.Namespace, plan: Plan) -> int:
    try:
        done = None if args.dry_run else apply_plan(plan)
    except Refusal as exc:
        return _refusal(args, exc.reasons)
    payload = plan_payload(plan, dry_run=args.dry_run, done=done)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _print_plan(payload)
    return 0


def run(args: argparse.Namespace) -> int:
    try:
        return args.inbox_func(args)
    except Unusable as exc:
        print(f"llm-wiki inbox: {exc}", file=sys.stderr)
        return 2


def register_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Register ``inbox`` on a parent ``llm-wiki`` parser; dispatch through :func:`run`."""
    inbox = subparsers.add_parser(
        "inbox",
        help="List, route or hold the items of the hub inbox (HUB/inbox/).",
        description=(
            "The deterministic half of hub inbox dispatch (references/hub-dispatch.md). "
            "The agent chooses the topic; this namespace copies the item into each named "
            "topic inbox with provenance, moves the original to HUB/inbox/.routed/ after "
            "every copy exists, and logs one dispatch entry. Nothing is overwritten, a "
            "private item is never routed to a public topic, and --dry-run writes nothing. "
            "Exit 0 done, 1 refused, 2 unusable hub."
        ),
    )
    sub = inbox.add_subparsers(dest="inbox_command", required=True)

    def common(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--hub", help="Override hub path (default: the configured hub).")
        parser.add_argument("--json", action="store_true", help="Print JSON.")

    def writer(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("item", help="File name of the item in HUB/inbox/ (a path inside it also works).")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Run every check and print the plan; write nothing, not even the log.",
        )
        parser.add_argument(
            "--require-cwd-hub",
            action="store_true",
            help="Refuse unless the selected hub is the hub enclosing the current directory.",
        )

    listing = sub.add_parser(
        "list",
        help="List the items in HUB/inbox/ that are not routed yet.",
        description=(
            "Read-only. Lists non-hidden entries of HUB/inbox/ (the .routed/, .needs-human/ "
            "and .skipped/ directories are counted, not listed) with size, sha256 and the "
            "privacy hints a router needs. Exit 0."
        ),
    )
    common(listing)
    listing.set_defaults(inbox_func=run_list)

    route = sub.add_parser(
        "route",
        help="Copy an inbox item into one or more topic inboxes, then move it to .routed/.",
        description=(
            "Copies the item into <topic>/inbox/ for every --to slug (markdown captures get "
            "routed_from/route_reason/route_confidence frontmatter, other files a "
            "<name>.route.md sidecar), moves the original to HUB/inbox/.routed/ once all "
            "copies exist and appends a dispatch entry to HUB/log.md. A re-run with the "
            "same arguments is a no-op. Refuses an unknown or archived slug, a different "
            "file already at a destination, a private item routed to a public topic, and "
            "a --reason naming a private or machine-local path into a public topic. "
            "Exit 0 done, 1 refused, 2 unusable hub."
        ),
    )
    common(route)
    writer(route)
    route.add_argument(
        "--to",
        action="append",
        required=True,
        metavar="SLUG",
        help="Registered topic slug to receive a copy; repeat for copy-to-multiple.",
    )
    route.add_argument("--reason", help="Why the item belongs there (written as route_reason).")
    route.add_argument(
        "--confidence",
        choices=CONFIDENCES,
        help="The router's confidence (written as route_confidence).",
    )
    route.set_defaults(inbox_func=run_route)

    hold = sub.add_parser(
        "hold",
        help="Set an item aside as needs-human or skipped (moves it, logs it).",
        description=(
            "The protocol's other verdicts: moves the item to HUB/inbox/.needs-human/ or "
            "HUB/inbox/.skipped/ and appends a dispatch entry to HUB/log.md. Nothing is "
            "overwritten. Exit 0 done, 1 refused, 2 unusable hub."
        ),
    )
    common(hold)
    writer(hold)
    hold.add_argument(
        "--as",
        dest="verdict",
        required=True,
        choices=sorted(HOLD_DIRS),
        help="Holding directory to move the item to.",
    )
    hold.set_defaults(inbox_func=run_hold)

    inbox.set_defaults(func=run)
    return inbox
