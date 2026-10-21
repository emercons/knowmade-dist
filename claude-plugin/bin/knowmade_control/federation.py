"""The ``registry`` namespace: the machine-local federation registry (KM-HUB-004, KM-HUB-007).

``~/.config/llm-wiki/registry.json`` lists the hubs and standalone wikis this
machine knows about, keyed on their stable ids (``hb-`` / ``wk-``), so a root
that moved or went offline can still be named and found again. It is machine
state: it never lives inside a corpus, is never tracked, and is never a second
source of truth. A root's own ``hub_id`` / ``wiki_id`` stays authoritative;
every status below is computed from the root on each call.

It does not touch resolution: ``hub_path``, ``LLM_WIKI_HUB``, ``--hub`` and
current-directory discovery behave exactly as before. The only write into a
root is ``registry add --assign``, which calls ``identity assign``.

Writers (``add``, ``refresh``, ``remove``) hold an ``O_EXCL`` lock file next to
``registry.json`` around their read-modify-write, so parallel callers never
lose an entry; a stale lock (dead holder, or older than 30 s) is reclaimed and a
lock held past the wait is one line and exit 2.

``inventory`` and ``peek`` (KM-HUB-011, KM-HUB-010) live in ``federation_view``,
which imports this module and ``xref``; they are registered and dispatched from
here and change nothing.

Verbs and exit codes (``DESIGN.md`` sections 4, 9 and 12;
``references/federation.md``): 0 success, 1 a refusal (undeclared id, duplicate
alias, ambiguity, nothing available), 2 an unusable input (a corrupt registry
file, a path that is not a root, an unknown id or alias, a lock that stayed
held). One line on stderr, never a traceback.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import random
import re
import socket
import sys
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import frontmatter as _frontmatter
from . import fsio as _fsio
from . import identity as _identity
from . import markdown as _markdown
from . import roots as _roots

SCHEMA_VERSION = 1
REGISTRY_LABEL = "federation registry"

AVAILABLE = "available"
UNAVAILABLE = "unavailable"
MISMATCH = "mismatch"
UNDECLARED = "undeclared"

_ID = re.compile(r"^(?:hb|wk)-[0-9a-f]{16}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ENTRY_KEYS = ("id", "kind", "path", "alias", "added", "last_seen")


class RegistryError(Exception):
    """A user-facing failure: one message, the exit code to use."""

    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


def registry_path(home: Path | None = None) -> Path:
    return _roots.federation_registry_path(home)


@dataclass
class Registry:
    """The parsed file: the entries, and every key this version does not know.

    Unknown top-level and per-entry keys are read back and written out again, so
    a hand-annotated file, or one written by a later minor addition, survives.
    """

    entries: list[dict[str, Any]] = field(default_factory=list)
    extras: dict[str, Any] = field(default_factory=dict)


# --- the lock -----------------------------------------------------------------
# A plain O_EXCL file (works on Windows and POSIX alike) holding pid, host and a
# token, as scripts/llm-wiki-session does for .sessions/. That script ships
# alone and cannot import this package, so the pattern is repeated, not shared.

LOCK_TIMEOUT_ENV = "LLM_WIKI_REGISTRY_LOCK_TIMEOUT"
DEFAULT_LOCK_TIMEOUT_SECONDS = 10.0
LOCK_STALE_SECONDS = 30.0  # a healthy transaction takes milliseconds
LOCK_RECLAIM_MIN_AGE_SECONDS = 1.0  # never reclaim a lock this fresh
_LOCK_POLL_SECONDS = 0.01


def _pid_alive(pid: int) -> bool | None:
    """Whether ``pid`` runs on this host; ``None`` when that cannot be told."""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        except (ImportError, AttributeError):
            return None
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return kernel32.GetLastError() == 5  # access denied: it exists
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return None
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def _lock_payload(lock: Path) -> dict[str, Any]:
    try:
        value = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _stale_lock_identity(lock: Path) -> tuple[Any, int] | None:
    """``(token, mtime_ns)`` of a lock that may be reclaimed, else ``None``."""
    try:
        info = lock.stat()
    except OSError:
        return None
    age = time.time() - info.st_mtime
    if age < LOCK_RECLAIM_MIN_AGE_SECONDS:
        return None
    payload = _lock_payload(lock)
    pid = payload.get("pid")
    dead = (
        isinstance(pid, int)
        and not isinstance(pid, bool)
        and payload.get("host") == socket.gethostname()
        and _pid_alive(pid) is False
    )
    if dead or age > LOCK_STALE_SECONDS:
        return payload.get("token"), info.st_mtime_ns
    return None


def _reclaim_lock(lock: Path, identity: tuple[Any, int]) -> None:
    """Rename the lock judged stale away; hand it back if it was replaced meanwhile."""
    reclaimed = lock.with_name(f"{lock.name}.reclaimed-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    try:
        os.replace(lock, reclaimed)
    except OSError:
        return
    try:
        taken = (_lock_payload(reclaimed).get("token"), reclaimed.stat().st_mtime_ns)
    except OSError:
        taken = None
    if taken != identity:
        with contextlib.suppress(OSError):
            os.link(reclaimed, lock)
    with contextlib.suppress(OSError):
        reclaimed.unlink()


def _release_lock(lock: Path, token: str) -> None:
    if _lock_payload(lock).get("token") not in {token, None}:
        return
    deadline = time.monotonic() + 1.0
    while True:
        try:
            lock.unlink()
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if os.name != "nt" or time.monotonic() >= deadline:
                return
            time.sleep(_LOCK_POLL_SECONDS)
        except OSError:
            return


def _lock_timeout() -> float:
    raw = os.environ.get(LOCK_TIMEOUT_ENV)
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return DEFAULT_LOCK_TIMEOUT_SECONDS


@contextlib.contextmanager
def locked(path: Path | None = None) -> Iterator[None]:
    """Hold the registry's lock file (``registry.json.lock``) for a read-modify-write.

    Creating it also creates the config directory, so a registry that cannot be
    written fails here, before anything else is changed.
    """
    path = path or registry_path()
    lock = path.with_name(path.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    wait = _lock_timeout()
    deadline = time.monotonic() + wait
    token = uuid.uuid4().hex
    payload = json.dumps({"pid": os.getpid(), "host": socket.gethostname(), "token": token})
    while True:
        try:
            fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            break
        except FileExistsError:
            identity = _stale_lock_identity(lock)
            if identity is not None:
                _reclaim_lock(lock, identity)
                continue
        except PermissionError:
            if os.name != "nt":
                raise
        if time.monotonic() >= deadline:
            raise RegistryError(
                f"{REGISTRY_LABEL} is locked by another process ({lock}); waited {wait:g}s", 2
            )
        time.sleep(_LOCK_POLL_SECONDS + random.random() * _LOCK_POLL_SECONDS)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload + "\n")
        yield
    finally:
        _release_lock(lock, token)


# --- the file ---------------------------------------------------------------


def _invalid(path: Path, why: str) -> RegistryError:
    return RegistryError(f"invalid {REGISTRY_LABEL} {path}: {why}", 2)


def _validated_entry(path: Path, index: int, entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise _invalid(path, f"roots[{index}] must be an object")
    for key in ("id", "kind", "path"):
        if not isinstance(entry.get(key), str) or not entry[key]:
            raise _invalid(path, f"roots[{index}].{key} must be a non-empty string")
    if not _ID.match(entry["id"]):
        raise _invalid(path, f"roots[{index}].id is not a hub or wiki id: {entry['id']}")
    if entry["kind"] not in ("hub", "wiki") or entry["id"][:2] != ("hb" if entry["kind"] == "hub" else "wk"):
        raise _invalid(path, f"roots[{index}].kind {entry['kind']!r} does not match id {entry['id']}")
    alias = entry.get("alias")
    if alias is not None and (not isinstance(alias, str) or not alias):
        raise _invalid(path, f"roots[{index}].alias must be a non-empty string or absent")
    for key in ("added", "last_seen"):
        value = entry.get(key)
        if value is not None and not (isinstance(value, str) and _DATE.match(value)):
            raise _invalid(path, f"roots[{index}].{key} must be YYYY-MM-DD or null")
    known = {
        "id": entry["id"],
        "kind": entry["kind"],
        "path": entry["path"],
        "alias": alias,
        "added": entry.get("added"),
        "last_seen": entry.get("last_seen"),
    }
    return {**known, **{key: value for key, value in entry.items() if key not in _ENTRY_KEYS}}


def load(path: Path | None = None) -> Registry:
    """The registry, entries in file order; a missing file is an empty registry."""
    path = path or registry_path()
    if not path.exists():
        return Registry()
    try:
        data = _fsio.read_json_object(path, REGISTRY_LABEL)
    except _fsio.JSONReadError as exc:
        raise RegistryError(str(exc), 2) from exc
    if data.get("schema_version") != SCHEMA_VERSION:
        raise _invalid(path, f"schema_version must be {SCHEMA_VERSION}, found {data.get('schema_version')!r}")
    roots = data.get("roots")
    if not isinstance(roots, list):
        raise _invalid(path, "roots must be an array")
    extras = {key: value for key, value in data.items() if key not in ("schema_version", "roots")}
    return Registry([_validated_entry(path, index, entry) for index, entry in enumerate(roots)], extras)


def save(registry: Registry, path: Path | None = None) -> None:
    path = path or registry_path()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "roots": [
            {
                **{key: entry.get(key) for key in _ENTRY_KEYS},
                **{key: value for key, value in entry.items() if key not in _ENTRY_KEYS},
            }
            for entry in registry.entries
        ],
        **registry.extras,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    _fsio.write_json_atomic(path, payload)


# --- paths and statuses -------------------------------------------------------


def expanded(raw: str) -> Path:
    return _roots.expand_leading_tilde(raw)


def display(path: Path) -> str:
    return path.as_posix()


def _stored_path(argument: str) -> str:
    """The path as stored: ``~`` forms kept as typed, anything else made absolute."""
    if argument == "~" or argument.startswith(("~/", "~\\")):
        return argument.replace("\\", "/")
    return display(Path(os.path.abspath(expanded(argument))))


def _same_root(left: str, right: str) -> bool:
    return left == right or _roots.same_path(expanded(left), expanded(right))


def _declared(root: Path, kind: str) -> tuple[str | None, str | None, str | None]:
    """``(declared id, forked_from, unreadable reason)`` of a root that exists.

    A marker file that exists but cannot be read or parsed gives a reason, not a
    missing id: such a root is unavailable and is never offered for
    ``identity assign``.
    """
    if kind == "hub":
        marker = root / "wikis.json"
        if not marker.exists():
            return None, None, None
        try:
            _fsio.read_json_object(marker, "wiki registry")
        except _fsio.JSONReadError:
            return None, None, "wikis.json is unreadable or not a JSON object"
        return _identity.read_hub_id(root), _identity.read_hub_forked_from(root), None
    marker = root / "config.md"
    if not marker.exists():
        return None, None, None
    try:
        _frontmatter.read(marker)
    except _frontmatter.FrontmatterError as exc:
        if exc.kind == "unterminated":
            return None, None, "config.md has an unterminated frontmatter block"
        return None, None, None
    except (OSError, UnicodeDecodeError):
        return None, None, "config.md is unreadable"
    return _identity.read_wiki_id(root), _identity.read_wiki_forked_from(root), None


def status_of(entry: dict[str, Any]) -> dict[str, Any]:
    """Compute ``status`` (and ``declared_id`` / ``forked_to`` / ``reason``) of one entry, read-only."""
    root = expanded(entry["path"])
    result: dict[str, Any] = {"status": UNAVAILABLE}
    try:
        if not root.is_dir():
            return result
        os.listdir(root)
    except OSError:
        return result
    declared, forked_from, reason = _declared(root, entry["kind"])
    if reason is not None:
        result["reason"] = reason
    elif declared is None:
        result["status"] = UNDECLARED
    elif declared == entry["id"]:
        result["status"] = AVAILABLE
    else:
        result["status"] = MISMATCH
        result["declared_id"] = declared
        if forked_from == entry["id"]:
            result["forked_to"] = declared
    return result


def status_detail(status: dict[str, Any]) -> str:
    """The short reason of a computed status (``forked -> ...``, ``declares ...``), or ``""``."""
    if "forked_to" in status:
        return f"forked -> {status['forked_to']}"
    if "declared_id" in status:
        return f"declares {status['declared_id']}"
    return status.get("reason", "")


def _with_status(entry: dict[str, Any]) -> dict[str, Any]:
    return {**entry, **status_of(entry)}


def _entry_line(entry: dict[str, Any]) -> str:
    detail = status_detail(entry)
    parts = [f"{entry['status']:<11}", f"{entry['kind']:<4}", entry["id"], entry["alias"] or "-", entry["path"]]
    if detail:
        parts.append(detail)
    return "  ".join(parts)


def _describe(entry: dict[str, Any]) -> str:
    """``<status> <path>`` plus the last-seen date and any detail, for messages."""
    text = f"{entry['status']}  {display(expanded(entry['path']))}"
    extras = [status_detail(entry), f"last seen {entry['last_seen']}" if entry.get("last_seen") else ""]
    extras = [extra for extra in extras if extra]
    return f"{text} ({', '.join(extras)})" if extras else text


# --- verbs ----------------------------------------------------------------------


def _detect_root(argument: str) -> tuple[Path, str]:
    root = expanded(argument)
    if not root.is_dir():
        raise RegistryError(f"not a directory: {argument}", 2)
    if (root / "wikis.json").is_file():
        return root, "hub"
    if (root / "config.md").is_file():
        return root, "wiki"
    raise RegistryError(
        f"{argument} is neither a hub (no wikis.json) nor a wiki (no config.md); "
        "for a wiki nested in a directory, pass its .wiki path",
        2,
    )


def _check_alias(alias: str, entries: list[dict[str, Any]]) -> None:
    if _ID.match(alias):
        raise RegistryError(f"alias {alias} looks like an id; choose another name", 1)
    if any(entry["alias"] == alias for entry in entries):
        raise RegistryError(f"alias already registered: {alias}", 1)


def cmd_add(args: argparse.Namespace) -> int:
    root, kind = _detect_root(args.path)
    alias = args.alias.strip() if args.alias is not None else None
    if alias is not None and not alias:
        raise RegistryError("alias must not be empty", 1)
    if kind == "hub":
        try:
            _fsio.read_json_object(root / "wikis.json", "wiki registry")
        except _fsio.JSONReadError as exc:
            raise RegistryError(str(exc), 2) from exc

    # The lock (which also creates the config directory) is held before the
    # registry is read, and the file is validated before --assign writes the
    # root, so an assignment is never made into a registry that cannot take it.
    with locked():
        registry = load()
        entries = registry.entries
        found = _identity.read_hub_id(root) if kind == "hub" else _identity.read_wiki_id(root)
        assigned = False
        if found is None:
            assign_hint = "llm-wiki identity assign --hub" if kind == "hub" else "llm-wiki identity assign"
            if not args.assign:
                raise RegistryError(
                    f"{display(root)} declares no id: run {assign_hint} first (or pass --assign)", 1
                )
            if alias is not None:
                _check_alias(alias, entries)
            found, assigned = (
                _identity.assign_hub_id(root) if kind == "hub" else _identity.assign_wiki_id(root)
            )

        stored = _stored_path(args.path)
        for entry in entries:
            if entry["id"] == found and _same_root(entry["path"], stored):
                if alias is not None and alias != entry["alias"]:
                    raise RegistryError(
                        f"{found} is already registered at {entry['path']} "
                        f"as {entry['alias'] or 'no alias'}; remove it first to change the alias",
                        1,
                    )
                print(f"already registered: {kind} {found} {entry['path']}")
                return 0
        if alias is not None:
            _check_alias(alias, entries)

        others = [entry for entry in entries if entry["id"] == found]
        stale = [entry for entry in entries if entry["id"] != found and _same_root(entry["path"], stored)]
        today = _markdown.today_date().isoformat()
        entries.append(
            {"id": found, "kind": kind, "path": stored, "alias": alias, "added": today, "last_seen": today}
        )
        try:
            save(registry)
        except OSError as exc:
            if assigned:
                raise RegistryError(
                    f"assigned {found} to {display(root)} but could not write the registry: {exc}", 2
                ) from exc
            raise
    if assigned:
        print(f"assigned: {found}")
    print(f"added: {kind} {found} {stored}")
    if others:
        where = ", ".join(entry["path"] for entry in others)
        print(f"notice: {found} is already registered at {where}; this path is a second replica", file=sys.stderr)
    for entry in stale:
        print(
            f"notice: {stored} was registered as {entry['id']}, which it no longer declares; "
            f"llm-wiki registry remove {entry['id']} drops the stale entry",
            file=sys.stderr,
        )
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    entries = [_with_status(entry) for entry in load().entries]
    if args.json:
        print(json.dumps({"roots": entries}, indent=2))
        return 0
    if not entries:
        print("no roots registered")
        return 0
    for entry in entries:
        print(_entry_line(entry))
    return 0


def cmd_refresh(args: argparse.Namespace) -> int:
    with locked():
        registry = load()
        today = _markdown.today_date().isoformat()
        available = changed = 0
        for entry in registry.entries:
            if status_of(entry)["status"] == AVAILABLE:
                available += 1
                if entry["last_seen"] != today:
                    entry["last_seen"] = today
                    changed += 1
        if changed:
            save(registry)
    print(f"refreshed {changed} of {available} available, {len(registry.entries) - available} not available")
    return 0


def _select(query: str, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Entries named by an id, else an alias, else a path; empty when nothing matches."""
    by_id = [entry for entry in entries if entry["id"] == query]
    if by_id:
        return by_id
    by_alias = [entry for entry in entries if entry["alias"] == query]
    if by_alias:
        return by_alias
    return [entry for entry in entries if _same_root(entry["path"], query)] if query else []


def cmd_remove(args: argparse.Namespace) -> int:
    with locked():
        registry = load()
        chosen = _select(args.target, registry.entries)
        if not chosen:
            raise RegistryError(f"not registered: {args.target}", 2)
        if len(chosen) > 1:
            lines = "\n".join(f"  {entry['path']}" for entry in chosen)
            raise RegistryError(
                f"ambiguous: {len(chosen)} entries match {args.target}; remove one by path:\n{lines}", 1
            )
        entry = chosen[0]
        registry.entries = [other for other in registry.entries if other is not entry]
        save(registry)
    print(f"removed: {entry['kind']} {entry['id']} {entry['path']}")
    return 0


def _replica(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": display(expanded(entry["path"])),
        "status": entry["status"],
        "alias": entry["alias"],
        "last_seen": entry["last_seen"],
    }


def cmd_locate(args: argparse.Namespace) -> int:
    entries = load().entries
    by_id = [entry for entry in entries if entry["id"] == args.target]
    if by_id:
        replicas = by_id
    else:
        # An alias names one registered entry, so it selects that replica.
        replicas = [entry for entry in entries if entry["alias"] == args.target]
    if not replicas:
        raise RegistryError(f"unknown id or alias: {args.target}", 2)
    replicas = [_with_status(entry) for entry in replicas]
    identity_id, kind = replicas[0]["id"], replicas[0]["kind"]
    available = [entry for entry in replicas if entry["status"] == AVAILABLE]

    if len(available) == 1:
        state = AVAILABLE
    elif available:
        state = "ambiguous"
    else:
        state = UNAVAILABLE
    found = display(expanded(available[0]["path"])) if len(available) == 1 else None
    code = 1 if state == UNAVAILABLE or (state == "ambiguous" and not args.all) else 0

    if args.json:
        print(
            json.dumps(
                {
                    "id": identity_id,
                    "kind": kind,
                    "status": state,
                    "path": found,
                    "replicas": [_replica(entry) for entry in replicas],
                },
                indent=2,
            )
        )
        return code
    if state == AVAILABLE:
        print(found)
    elif state == UNAVAILABLE:
        print(f"no available replica of {identity_id}; last known:", file=sys.stderr)
        for entry in replicas:
            print(f"  {_describe(entry)}", file=sys.stderr)
    elif args.all:
        for entry in available:
            print(display(expanded(entry["path"])))
    else:
        print(f"ambiguous: {len(available)} replicas of {identity_id}", file=sys.stderr)
        for entry in available:
            print(f"  {_describe(entry)}", file=sys.stderr)
        print("pass --all to list them, or locate by alias to pick one", file=sys.stderr)
    return code


# --- parser -----------------------------------------------------------------------

REGISTRY_HELP = (
    "Machine-local registry of hubs and wikis keyed on their stable ids "
    "(~/.config/llm-wiki/registry.json)."
)


def register_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Register ``registry`` on a parent ``llm-wiki`` parser; dispatch through :func:`run`."""
    registry = subparsers.add_parser(
        "registry",
        help=REGISTRY_HELP,
        description=(
            f"{REGISTRY_HELP} It lists the roots this machine knows, keeps unavailable ones "
            "registered, and finds the current path of an id; it never replaces hub_path, "
            "LLM_WIKI_HUB, --hub or cwd discovery, and a root's own id stays authoritative. "
            "Exit 0 success, 1 a refusal (undeclared id, duplicate alias, ambiguity, nothing "
            "available), 2 unusable input (corrupt registry file, not a root, unknown id, "
            "lock held)."
        ),
    )
    registry.set_defaults(func=run)
    sub = registry.add_subparsers(dest="registry_command", required=True)

    add = sub.add_parser(
        "add",
        help="Register a hub or wiki root under the id it declares.",
        description=(
            "<path> is a hub (wikis.json) or a wiki (config.md; for a wiki nested in a "
            "directory, pass its .wiki path). The root must declare an id "
            "(llm-wiki identity assign [--hub]); --assign assigns one, the only write into a "
            "root this namespace does. The same id at another path is a second replica (added, "
            "with a notice); the same id at the same path is a no-op."
        ),
    )
    add.add_argument("path", help="Hub or wiki root to register.")
    add.add_argument("--alias", help="A unique name for this entry (surrounding whitespace is stripped).")
    add.add_argument("--assign", action="store_true", help="Assign an id to a root that declares none.")

    list_parser = sub.add_parser(
        "list",
        help="List registered roots with a status computed from each root.",
        description=(
            "Read-only. Status: available (path exists and declares the registered id), "
            "unavailable (path missing or unreadable, or its wikis.json / config.md cannot "
            "be read; the entry stays), mismatch (the path declares another id; "
            "'forked -> <id>' when it was forked from the registered one), "
            "undeclared (path exists, no id)."
        ),
    )
    list_parser.add_argument("--json", action="store_true", help="Print JSON.")

    sub.add_parser(
        "refresh",
        help="Set last_seen to today on every available entry.",
        description="Writes only last_seen, only on available entries, and only when it changes.",
    )

    remove = sub.add_parser(
        "remove",
        help="Remove one entry by id, alias or path.",
        description=(
            "Removes exactly one entry. An id with several replicas, or a path shared by "
            "several entries, exits 1 and lists them; name the path to pick one. Nothing "
            "inside the root is touched."
        ),
    )
    remove.add_argument("target", help="An id, an alias or a registered path.")

    locate = sub.add_parser(
        "locate",
        help="Print the current local path of an id or alias.",
        description=(
            "Read-only. Exactly one available replica: its path, exit 0. None available: exit 1 "
            "with the last known paths and statuses. Several available: exit 1 'ambiguous: N "
            "replicas of <id>'; --all lists them and exits 0. An alias selects its own entry. "
            "Unknown id or alias: exit 2."
        ),
    )
    locate.add_argument("target", help="An id (hb-... / wk-...) or an alias.")
    locate.add_argument("--all", action="store_true", help="List every available replica instead of failing on several.")
    locate.add_argument("--json", action="store_true", help="Print JSON.")

    # inventory and peek read across roots and need the reference resolver, which
    # imports this module: imported here, after both are loaded.
    from . import federation_view

    federation_view.register_verbs(sub)
    return registry


_VERBS = {
    "add": cmd_add,
    "list": cmd_list,
    "refresh": cmd_refresh,
    "remove": cmd_remove,
    "locate": cmd_locate,
}


def run(args: argparse.Namespace) -> int:
    """`llm-wiki registry add|list|refresh|remove|locate|inventory|peek`."""
    try:
        verb = _VERBS.get(args.registry_command)
        if verb is None:
            from . import federation_view

            verb = federation_view.VERBS[args.registry_command]
        return verb(args)
    except RegistryError as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
    except SystemExit as exc:
        # JSONReadError is a SystemExit: an unreadable JSON file that reaches here
        # (for instance through the identity layer) is an unusable input, exit 2.
        if isinstance(exc.code, str):
            print(exc.code, file=sys.stderr)
            return 2 if isinstance(exc, _fsio.JSONReadError) else 1
        raise
    except OSError as exc:
        print(f"registry: {exc}", file=sys.stderr)
        return 2
