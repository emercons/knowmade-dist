#!/usr/bin/env python3
"""Automated session capture helpers for llm-wiki.

This helper is intentionally deterministic and local-only. Harness hooks call the
`hook` subcommand with one JSON object on stdin. The hook path records a small,
redacted event, updates per-session state under HUB/.sessions, and writes a
markdown digest at configured checkpoints. It never calls an LLM or copies raw
transcripts by default.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
import random
import re
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.parse
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

UTC = dt.timezone.utc
SCHEMA_VERSION = 1
DEFAULT_TOOL_THRESHOLD = 50
MAX_LAST_EVENTS = 20
MAX_EVENT_PREVIEW_CHARS = 1200
MAX_FEEDBACK_PREVIEW_CHARS = 360
# Filesystem-safe id characters. `:` is deliberately absent (NTFS treats `a:b` as an
# alternate data stream) and so is `@`; see safe_id() for how ids stay distinct.
SESSION_ID_SAFE = re.compile(r"[^A-Za-z0-9_.+-]+")
LEGACY_SESSION_ID_SAFE = re.compile(r"[^A-Za-z0-9_.@:+-]+")
WINDOWS_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"] + [f"COM{n}" for n in range(1, 10)] + [f"LPT{n}" for n in range(1, 10)]
)
MAX_SAFE_ID_CHARS = 120
SAFE_ID_HASH_CHARS = 8
SECRET_KEY_RE = re.compile(
    r"(?i)(api[_-]?key|authorization|bearer|token|secret|password|passwd|private[_-]?key|access[_-]?key|session[_-]?cookie|cookie)"
)
CONTENT_KEY_RE = re.compile(
    r"(?i)^(prompt|user_prompt|message|messages|content|transcript|tool_response|tool_output|response|result|stdout|stderr)$"
)
# Input-shaped keys carry shell commands and Write/Edit payloads. They are replaced
# wholesale by a hash summary; only the tool-identity keys below survive in clear.
INPUT_KEY_RE = re.compile(r"(?i)^(tool_input|arguments|input|args|command|old_string|new_string)$")
INPUT_CLEAR_KEYS = ("file_path", "file_paths")
PRIVATE_TOPIC_SUFFIX = "-PRIVATE"
MACHINE_IDENTITY_ROWS = {"CWD", "Git remote", "Transcript pointer"}
# scrub_machine_identity leaves values this short alone (a filesystem root).
MIN_SCRUB_VALUE_CHARS = 3

DEFAULT_CONFIG: dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "enabled": True,
    "mode": "balanced",
    "auto_capture": {
        "tool_events": DEFAULT_TOOL_THRESHOLD,
        "pre_compact": True,
        "post_compact": True,
        "session_end": True,
        "stop": True,
    },
    "rehydrate": {
        "session_start": True,
        "user_prompt": True,
        "strict": False,
    },
    "raw_transcripts": False,
    "privacy": "redacted",
    "feedback": {
        "enabled": True,
        "capture_approvals": True,
        "min_confidence": "medium",
    },
}

BALANCED_CONFIG = {
    "enabled": True,
    "mode": "balanced",
    "auto_capture": {
        "tool_events": DEFAULT_TOOL_THRESHOLD,
        "pre_compact": True,
        "post_compact": True,
        "session_end": True,
        "stop": True,
    },
    "rehydrate": {
        "session_start": True,
        "user_prompt": True,
        "strict": False,
    },
    "raw_transcripts": False,
    "privacy": "redacted",
}

CAPTURE_ONLY_CONFIG = {
    "enabled": True,
    "mode": "capture-only",
    "auto_capture": {
        "tool_events": DEFAULT_TOOL_THRESHOLD,
        "pre_compact": True,
        "post_compact": True,
        "session_end": True,
        "stop": True,
    },
    "rehydrate": {
        "session_start": False,
        "user_prompt": False,
        "strict": False,
    },
    "raw_transcripts": False,
    "privacy": "redacted",
}

AGGRESSIVE_CONFIG = {
    "enabled": True,
    "mode": "aggressive",
    "auto_capture": {
        "tool_events": 25,
        "pre_compact": True,
        "post_compact": True,
        "session_end": True,
        "stop": True,
    },
    "rehydrate": {
        "session_start": True,
        "user_prompt": True,
        "strict": False,
    },
    "raw_transcripts": False,
    "privacy": "redacted",
}

MODE_CONFIGS = {
    "off": {
        **DEFAULT_CONFIG,
        "enabled": False,
        "mode": "off",
        "rehydrate": {"session_start": False, "user_prompt": False, "strict": False},
    },
    "capture-only": {**DEFAULT_CONFIG, **CAPTURE_ONLY_CONFIG},
    "balanced": {**DEFAULT_CONFIG, **BALANCED_CONFIG},
    "aggressive": {**DEFAULT_CONFIG, **AGGRESSIVE_CONFIG},
}


class HookSkip(Exception):
    """Raised when a hook should silently do nothing."""


def utc_now() -> str:
    """Current UTC timestamp, with the same env override legacy_cli.py honours.

    LLM_WIKI_NOW pins every timestamp this helper writes (state, queue events,
    digest paths), so a test can assert exact `digests/YYYY/MM/` locations.
    """
    override = os.environ.get("LLM_WIKI_NOW")
    if override:
        return override
    return dt.datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def today() -> str:
    """Today's ISO date, with the LLM_WIKI_TODAY override legacy_cli.py honours."""
    override = os.environ.get("LLM_WIKI_TODAY")
    if override:
        try:
            return dt.date.fromisoformat(override[:10]).isoformat()
        except ValueError:
            raise SystemExit(f"invalid LLM_WIKI_TODAY date: {override!r}")
    return dt.date.today().isoformat()


# This file ships verbatim as a plugin hook and cannot import the control plane:
# it vendors, never imports. scripts/sync-session-vendor.py is the only writer of
# the blocks between `vendored from knowmade_control.<module>` markers; change the
# module and re-run it (both plugin sync scripts do). A drifted hand copy is what
# made the hook silently create ~/wiki (emercons/knowmade#43).

# --- vendored from knowmade_control.roots: start ---
# Generated from scripts/knowmade_control/roots.py by scripts/sync-session-vendor.py; do not edit.

DEFAULT_PRIVATE_SUFFIX = "-PRIVATE"
HUB_ENV = "LLM_WIKI_HUB"
# A drive path as written on Windows: ``C:\x``, ``C:/x``, ``/C:/x``, or the Git
# Bash spelling ``/c/x`` (also ``/c`` alone). Whether it applies is decided by
# the platform, never by the text: see ``normalize_windows_absolute_path``.
WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"^(?:/?[A-Za-z]:[\\/]|/[A-Za-z](?:[\\/]|$))")
GIT_BASH_DRIVE_RE = re.compile(r"^/([A-Za-z])(?:[\\/]|$)")


def normalize_windows_absolute_path(value: str, platform: str | None = None) -> str | None:
    """Return a canonical drive path only when Windows semantics apply.

    ``C:\\x``, ``C:/x`` and ``/C:/x`` become ``C:/x`` (the drive keeps its
    case); the Git Bash spelling ``/c/x`` becomes ``C:/x`` and ``/c`` becomes
    ``C:/``. On POSIX every one of these is an ordinary path and ``None`` comes
    back, so a literal ``/c/x`` stays what the caller wrote.
    """
    if (platform or os.name) != "nt" or not WINDOWS_ABSOLUTE_PATH_RE.match(value):
        return None
    git_bash = GIT_BASH_DRIVE_RE.match(value)
    if git_bash:
        value = f"{git_bash.group(1).upper()}:/{value[git_bash.end():]}"
    elif value.startswith("/"):
        value = value[1:]
    return value.replace("\\", "/")


def windows_absolute_path(value: str, platform: str | None = None) -> Path | None:
    normalized = normalize_windows_absolute_path(value, platform)
    return Path(normalized) if normalized is not None else None


def home_directory() -> Path:
    """Return the home directory, honouring HOME on every platform.

    ``Path.home()`` consults USERPROFILE on Windows and ignores HOME entirely, so
    a hub reached through a POSIX-style shell — or any caller that sets HOME to
    point the tool at a different profile, which is what the test suite does —
    silently resolved against the wrong profile and reported the wiki as missing
    from the registry. Git itself honours HOME on Windows; match that.
    """
    home = os.environ.get("HOME")
    return Path(home) if home else Path.home()


def expand_leading_tilde(value: str, platform: str | None = None) -> Path:
    windows_path = windows_absolute_path(value, platform)
    if windows_path is not None:
        return windows_path
    if value == "~":
        return home_directory()
    if value.startswith("~/"):
        return home_directory() / value[2:]
    if (platform or os.name) == "nt" and value.startswith("~\\"):
        return home_directory() / value[2:].replace("\\", "/")
    return Path(value)


def initialized_wiki_root(path: Path) -> bool:
    return (path / "_index.md").exists()


def resolve_registry_path(raw_path: str, hub: Path, platform: str | None = None) -> Path:
    # On Windows either separator spells the same path, so ``<HUB>\topics\x`` is
    # ``<HUB>/topics/x``. On POSIX a backslash is part of a file name: untouched.
    text = raw_path.replace("\\", "/") if (platform or os.name) == "nt" else raw_path
    if text in {".", "<HUB>", "HUB"}:
        return hub
    if text.startswith("<HUB>/"):
        return hub / text[len("<HUB>/") :]
    if text.startswith("HUB/"):
        return hub / text[len("HUB/") :]
    path = expand_leading_tilde(raw_path, platform)
    if path.is_absolute():
        return path
    return hub / text


def _read_hub_config(config: Path) -> dict[str, Any]:
    return read_json_object(config, "hub config")


def hub_env_value(environ: dict[str, str] | None = None) -> str | None:
    """The ``LLM_WIKI_HUB`` value, stripped; ``None`` when unset or blank."""
    environ = os.environ if environ is None else environ
    return (environ.get(HUB_ENV) or "").strip() or None


def resolve_hub_path(
    explicit_hub: str | Path | None = None,
    *,
    home: Path | None = None,
    config_path: Path | None = None,
    private_suffix: str = DEFAULT_PRIVATE_SUFFIX,
) -> Path:
    if explicit_hub:
        return expand_leading_tilde(str(explicit_hub))
    home = home or home_directory()
    config = config_path or (home / ".config" / "llm-wiki" / "config.json")
    if config.exists():
        data = _read_hub_config(config)
        hub_path = data.get("hub_path")
        hub_candidate = expand_leading_tilde(str(hub_path)) if hub_path else None
        resolved_path = data.get("resolved_path")
        resolved_candidate = expand_leading_tilde(str(resolved_path)) if resolved_path else None
        if not hub_candidate and (
            data.get("private_hub_path")
            or data.get("public_hub_path")
            or (isinstance(data.get("hubs"), dict) and data.get("hubs"))
        ):
            raise SystemExit(
                "Knowmade uses one hub_path. Put public and private topic wikis "
                f"in the same hub, naming private topics <slug>{private_suffix}."
            )
        if hub_candidate:
            if hub_candidate.exists() or not (
                resolved_candidate and initialized_wiki_root(resolved_candidate)
            ):
                return hub_candidate
        if resolved_candidate:
            return resolved_candidate
    return home / "wiki"
# --- vendored from knowmade_control.roots: end ---


def resolve_hub(args: argparse.Namespace) -> Path:
    """Where the hub is: ``--local``, ``--hub``, ``LLM_WIKI_HUB``, then the configured hub.

    The control plane's order (``roots.resolve_hub_selection``) without cwd
    discovery. Pure resolution: it never creates anything. The caller decides
    whether a missing directory is an error (explicit commands) or a silent
    no-op (hook).
    """
    if getattr(args, "local", False):
        return Path.cwd() / ".wiki"
    return resolve_hub_path(getattr(args, "hub", None) or hub_env_value())


def hub_is_explicit(args: argparse.Namespace) -> bool:
    """True when the caller named the hub (--hub/--local) rather than inheriting a default."""
    return bool(getattr(args, "local", False) or getattr(args, "hub", None))


def sessions_dir(hub: Path) -> Path:
    return hub / ".sessions"


def safe_id(value: str) -> str:
    """A filesystem-safe form of ``value`` that stays unambiguous.

    An id that is already safe is returned unchanged, so existing state files keep
    their names. Anything that has to be altered (a ``:``, an ``@``, a reserved
    Windows device name, over-length input) gets ``-<sha256[:8]>`` of the original
    appended, so ``a:b`` and ``a-b`` can never share a state file. The full,
    unmodified id is always kept in the JSON (``native_session_id``).
    """
    cleaned = SESSION_ID_SAFE.sub("-", value).strip(".-")
    if not cleaned:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    changed = (
        cleaned != value
        or len(cleaned) > MAX_SAFE_ID_CHARS
        or cleaned.split(".")[0].upper() in WINDOWS_RESERVED_NAMES
    )
    if not changed:
        return cleaned
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:SAFE_ID_HASH_CHARS]
    stem = cleaned[: MAX_SAFE_ID_CHARS - SAFE_ID_HASH_CHARS - 1].rstrip(".-")
    return f"{stem}-{digest}" if stem else digest


def legacy_safe_id(value: str) -> str:
    """The pre-#43 id form, kept only to find state files written by older versions."""
    cleaned = LEGACY_SESSION_ID_SAFE.sub("-", value).strip(".-")
    if not cleaned:
        cleaned = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return cleaned[:120]


def state_path(root: Path, harness: str, native_session_id: str) -> Path:
    return root / "state" / safe_id(harness) / f"{safe_id(native_session_id)}.json"


def legacy_state_path(root: Path, harness: str, native_session_id: str) -> Path | None:
    """Where an older version stored this session, when that differs from ``state_path``."""
    legacy = root / "state" / legacy_safe_id(harness) / f"{legacy_safe_id(native_session_id)}.json"
    return None if legacy == state_path(root, harness, native_session_id) else legacy


def digest_path(root: Path, harness: str, native_session_id: str, now: str | None = None) -> Path:
    when = parse_timestamp(now or utc_now())
    return (
        root
        / "digests"
        / f"{when.year:04d}"
        / f"{when.month:02d}"
        / f"{safe_id(harness)}-{safe_id(native_session_id)}.md"
    )


def parse_timestamp(value: str) -> dt.datetime:
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(normalized)
    except ValueError:
        return dt.datetime.now(UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


# One temp file per writer (mkstemp), so concurrent hook processes writing the
# same session state file never pull a shared temp out from under each other.
# --- vendored from knowmade_control.fsio: start ---
# Generated from scripts/knowmade_control/fsio.py by scripts/sync-session-vendor.py; do not edit.

PERMISSION_DENIED_ERRNOS = {1, 13}
# Windows refuses to replace a file that another process currently has open,
# which concurrent hooks and readers of the same state file do constantly. The
# window is short, so the rename is retried briefly before the error is raised.
_REPLACE_RETRY_SECONDS = 2.0
_REPLACE_RETRY_INTERVAL = 0.01


def _default_mode(path: Path) -> int:
    """The permissions a plain ``open(path, "w")`` would leave behind."""
    try:
        return os.stat(path).st_mode & 0o7777
    except OSError:
        pass
    current = os.umask(0)
    os.umask(current)
    return 0o666 & ~current


def _replace(tmp: Path, path: Path) -> None:
    deadline = time.monotonic() + _REPLACE_RETRY_SECONDS
    while True:
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if os.name != "nt" or time.monotonic() >= deadline:
                raise
            time.sleep(_REPLACE_RETRY_INTERVAL)


def _fsync_directory(directory: Path) -> None:
    """Persist the directory entry after a rename; a no-op where unsupported."""
    if os.name == "nt":
        return
    try:
        fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write_bytes(path: Path, data: bytes, mode: int | None = None) -> None:
    """Replace ``path`` with ``data`` atomically; see the module docstring."""
    path = Path(path)
    if mode is None:
        mode = _default_mode(path)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        _replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    _fsync_directory(path.parent)


def atomic_write_text(
    path: Path,
    text: str,
    encoding: str = "utf-8",
    newline: str | None = "\n",
    mode: int | None = None,
) -> None:
    """Atomically write ``text``; ``\\n`` in ``text`` becomes ``newline``.

    ``newline=None`` means the platform convention, exactly as text-mode
    ``open`` would translate it. The default keeps corpus files LF-only.
    """
    if newline is None:
        newline = os.linesep
    if newline != "\n":
        text = text.replace("\n", newline)
    atomic_write_bytes(path, text.encode(encoding), mode=mode)


def read_text(path: Path) -> str:
    """Read ``path`` as UTF-8 text, dropping a leading BOM if one is present.

    Line endings are translated like ``Path.read_text`` does; only the BOM
    handling differs, and nothing here ever writes a BOM back.
    """
    with open(path, "r", encoding="utf-8-sig") as handle:
        return handle.read()


def is_permission_denied(exc: BaseException) -> bool:
    return isinstance(exc, PermissionError) or getattr(exc, "errno", None) in PERMISSION_DENIED_ERRNOS


def permission_denied_message(path: Path | None, operation: str, exc: BaseException) -> str:
    target = str(path or getattr(exc, "filename", None) or "wiki path")
    return (
        f"permission denied while trying to {operation}: {target}\n"
        f"{type(exc).__name__}: {exc}\n\n"
        "The path exists, but this process cannot read or list its contents. "
        "On macOS this usually means the app launching Codex/the terminal lacks "
        "Full Disk Access or iCloud Drive access. Grant access to the exact launcher, "
        "restart Codex, and try again. The configured hub_path is probably correct; "
        "do not switch to a ~/wiki fallback or machine-local resolved_path for this error."
    )


class JSONReadError(SystemExit):
    """A JSON object file that cannot be used; the message names the file.

    A ``SystemExit`` so an uncaught one ends a command with its message and
    exit status 1, exactly as the per-module readers it replaces did. Callers
    that report errors another way (a JSON result, a lifecycle error) catch
    this class rather than ``SystemExit`` as a whole.
    """


def read_json_object(path: Path, label: str) -> dict[str, Any]:
    """Read ``path`` as a JSON object; ``label`` names it in every message.

    A leading UTF-8 BOM is accepted. Unreadable, permission-denied, non-UTF-8,
    malformed and non-object files each raise :class:`JSONReadError`.
    """
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        if is_permission_denied(exc):
            raise JSONReadError(permission_denied_message(path, f"read {label}", exc)) from exc
        raise JSONReadError(f"could not read {label} {path}: {exc}") from exc
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise JSONReadError(f"{label} is not valid UTF-8: {path} ({exc.reason} at byte {exc.start})") from exc
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise JSONReadError(f"invalid {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise JSONReadError(f"invalid {label} {path}: root must be an object")
    return value
# --- vendored from knowmade_control.fsio: end ---


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, text)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    # Windows refuses to open a file while it is being replaced, so a reader that
    # meets a writer sees a sharing violation rather than either version of the
    # file. The window is very short; retry briefly before giving up.
    deadline = time.monotonic() + 1.0
    while True:
        try:
            text = read_text(path)
            break
        except FileNotFoundError:
            return default
        except PermissionError:
            if time.monotonic() >= deadline:
                return default
            time.sleep(0.005)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Defense in depth for any already-torn state files: keep the first
        # valid object and drop trailing bytes. If the file starts with garbage,
        # is truncated, or is empty, fall back to a fresh default instead of
        # crashing a hook.
        try:
            obj, _ = json.JSONDecoder().raw_decode(text.lstrip())
            return obj
        except json.JSONDecodeError:
            return default


def write_json(path: Path, data: Any) -> None:
    atomic_write(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def append_jsonl(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(data, sort_keys=True, ensure_ascii=False) + "\n")


def merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(base))
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_dict(result[key], value)
        else:
            result[key] = value
    return result


class ConfigError(Exception):
    """`.sessions/config.json` exists but cannot be used."""


def read_config_strict(path: Path) -> dict[str, Any] | None:
    """The parsed config object, ``None`` when absent, ``ConfigError`` when unusable."""
    try:
        text = read_text(path)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ConfigError(f"{path} must contain a JSON object")
    return loaded


def load_config(root: Path) -> dict[str, Any]:
    """The effective config. Capture is opt-in; a corrupt `config.json` fails closed.

    With no `config.json` the result is `enabled: False`: capture starts only after
    `llm-wiki-session enable` has written a config (`DEFAULT_CONFIG` is what that
    writes, with `enabled: True`). An existing config keeps its own `enabled`.

    A config we cannot read might be the file that says `enabled: false`, so it is
    treated as opt-out rather than as "no config": the result is `enabled: False`,
    `mode: "off"` and carries `config_error` for callers to report. The hook warns
    on stderr and records nothing; `llm-wiki-session enable` repairs it.
    """
    data = merge_dict(DEFAULT_CONFIG, {})
    path = root / "config.json"
    try:
        loaded = read_config_strict(path)
    except ConfigError as exc:
        data = merge_dict(data, MODE_CONFIGS["off"])
        data["config_error"] = str(exc)
        return data
    if loaded is None:
        data["enabled"] = False
    else:
        data = merge_dict(data, loaded)
    return data


def quarantine_config(root: Path) -> Path | None:
    """Move an unusable config aside so `enable`/`disable` can rewrite it; returns the new path."""
    path = root / "config.json"
    target = root / "config.json.corrupt"
    try:
        os.replace(path, target)
    except OSError:
        return None
    return target


# --- session store -----------------------------------------------------------
# Every mutation of .sessions/ goes through one SessionStore transaction: take the
# store lock, load what is needed, apply the mutation to in-memory objects, and
# commit once. Harnesses fire hooks per tool call and run tools in parallel, so
# two hook processes routinely read event_count=N and used to both write N+1.
# The lock is a plain O_EXCL file (works on Windows and POSIX alike) holding
# pid/host/timestamp so a crashed holder can be recognised and reclaimed.

LOCK_NAME = ".lock"
DEFAULT_LOCK_TIMEOUT_SECONDS = 4.0  # hooks.json gives the command 5 s
LOCK_STALE_SECONDS = 30.0  # a healthy transaction takes milliseconds
LOCK_RECLAIM_MIN_AGE_SECONDS = 1.0  # never reclaim a lock this fresh, whoever seems to hold it
_LOCK_POLL_SECONDS = 0.01
_WINDOWS_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WINDOWS_STILL_ACTIVE = 259
_WINDOWS_ERROR_ACCESS_DENIED = 5


class SessionLockTimeout(Exception):
    """The store lock stayed held by a live process for the whole wait."""


def pid_alive(pid: int) -> bool | None:
    """Whether ``pid`` runs on this host; ``None`` when that cannot be told."""
    if pid <= 0:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) would terminate the target on Windows; query it instead.
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        except (ImportError, AttributeError):
            return None
        handle = kernel32.OpenProcess(_WINDOWS_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return kernel32.GetLastError() == _WINDOWS_ERROR_ACCESS_DENIED
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return None
            return code.value == _WINDOWS_STILL_ACTIVE
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


def _read_lock_payload(lock: Path) -> dict[str, Any]:
    try:
        value = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _stale_lock_identity(lock: Path) -> tuple[Any, int] | None:
    """``(token, mtime_ns)`` of a lock that may be reclaimed, else ``None``.

    A lock is reclaimable when its holder is a dead process on this host, or when
    it is older than LOCK_STALE_SECONDS. A lock younger than LOCK_RECLAIM_MIN_AGE
    is never touched: the holder may still be writing its own payload, and a
    holder that just released may have been replaced by a new, live lock.
    """
    try:
        info = lock.stat()
    except OSError:
        return None  # vanished: the holder released it
    age = time.time() - info.st_mtime
    if age < LOCK_RECLAIM_MIN_AGE_SECONDS:
        return None
    payload = _read_lock_payload(lock)
    pid = payload.get("pid")
    dead = (
        isinstance(pid, int)
        and not isinstance(pid, bool)
        and payload.get("host") == socket.gethostname()
        and pid_alive(pid) is False
    )
    if dead or age > LOCK_STALE_SECONDS:
        return payload.get("token"), info.st_mtime_ns
    return None


def _reclaim_lock(lock: Path, identity: tuple[Any, int]) -> None:
    """Rename the lock we judged stale away, then delete it.

    Only one of several concurrent reclaimers wins the rename. Between judging
    and renaming, the stale lock may have been removed and a live one created; the
    renamed file is therefore checked against the judged identity and, if it is
    someone else's, handed back.
    """
    reclaimed = lock.with_name(f"{lock.name}.reclaimed-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    try:
        os.replace(lock, reclaimed)
    except OSError:
        return
    try:
        taken = (_read_lock_payload(reclaimed).get("token"), reclaimed.stat().st_mtime_ns)
    except OSError:
        taken = None
    if taken != identity:
        try:
            os.link(reclaimed, lock)  # fails, harmlessly, if the path was taken again
        except OSError:
            pass
    try:
        reclaimed.unlink()
    except OSError:
        pass


def _release_lock(lock: Path, token: str) -> None:
    """Delete our own lock; never one a reclaimer has since re-created for someone else."""
    if _read_lock_payload(lock).get("token") not in {token, None}:
        return
    deadline = time.monotonic() + 1.0
    while True:
        try:
            lock.unlink()
            return
        except FileNotFoundError:
            return
        except PermissionError:
            # Windows refuses to delete a file another process has open for reading.
            if os.name != "nt" or time.monotonic() >= deadline:
                return
            time.sleep(_LOCK_POLL_SECONDS)
        except OSError:
            return


def lock_timeout_seconds() -> float:
    raw = os.environ.get("LLM_WIKI_SESSION_LOCK_TIMEOUT")
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return DEFAULT_LOCK_TIMEOUT_SECONDS


@contextlib.contextmanager
def exclusive_lock(lock: Path, timeout: float | None = None) -> Iterator[None]:
    lock.parent.mkdir(parents=True, exist_ok=True)
    wait = lock_timeout_seconds() if timeout is None else timeout
    deadline = time.monotonic() + wait
    token = uuid.uuid4().hex
    payload = json.dumps(
        {"pid": os.getpid(), "host": socket.gethostname(), "token": token, "created_at": utc_now()},
        sort_keys=True,
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    while True:
        try:
            fd = os.open(lock, flags, 0o600)
            break
        except FileExistsError:
            identity = _stale_lock_identity(lock)
            if identity is not None:
                _reclaim_lock(lock, identity)
                continue
        except PermissionError:
            # Windows reports "access denied" for a lock that is being deleted.
            if os.name != "nt":
                raise
        if time.monotonic() >= deadline:
            raise SessionLockTimeout(f"{lock} is held by another process; waited {wait:g}s")
        time.sleep(_LOCK_POLL_SECONDS + random.random() * _LOCK_POLL_SECONDS)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload + "\n")
        yield
    finally:
        _release_lock(lock, token)


class Transaction:
    """One locked load -> mutate -> commit cycle over `.sessions/`.

    Mutations are staged in memory and applied by `commit()` in a fixed order:
    files, JSONL appends, JSON documents, then one state write per session, then
    derived indexes if any step asked for them. Nothing touches disk before the
    lock is held, and each session state is written exactly once per transaction.
    """

    def __init__(self, store: "SessionStore") -> None:
        self.store = store
        self.root = store.root
        self._states: dict[Path, dict[str, Any]] = {}
        self._loaded_new: dict[Path, bool] = {}
        self._legacy: dict[Path, Path] = {}
        self._files: list[tuple[Path, str]] = []
        self._appends: list[tuple[Path, dict[str, Any]]] = []
        self._documents: list[tuple[Path, Any]] = []
        self._index_requested = False
        self._feedback_index_requested = False

    def load_state(self, harness: str, native_session_id: str) -> tuple[dict[str, Any], bool]:
        """The session state as it is now (under the lock) and whether it is new."""
        path = state_path(self.root, harness, native_session_id)
        if path in self._states:
            return self._states[path], self._loaded_new[path]
        is_new = not path.exists()
        state = read_json(path, {})
        if is_new:
            legacy = legacy_state_path(self.root, harness, native_session_id)
            if legacy is not None and legacy.exists():
                legacy_state = read_json(legacy, {})
                if isinstance(legacy_state, dict) and legacy_state:
                    state, is_new = legacy_state, False
                    self._legacy[path] = legacy
        if not isinstance(state, dict):
            state, is_new = {}, True
        self._states[path] = state
        self._loaded_new[path] = is_new
        return state, is_new

    def stage_state(self, state: dict[str, Any]) -> None:
        path = state_path(self.root, str(state["harness"]), str(state["native_session_id"]))
        self._states[path] = state
        self._loaded_new.setdefault(path, False)

    def write_file(self, path: Path, text: str) -> None:
        self._files.append((path, text))

    def write_document(self, path: Path, data: Any) -> None:
        self._documents.append((path, data))

    def append(self, path: Path, record: dict[str, Any]) -> None:
        self._appends.append((path, record))

    def request_index(self) -> None:
        self._index_requested = True

    def request_feedback_index(self) -> None:
        self._feedback_index_requested = True

    def commit(self) -> None:
        for path, text in self._files:
            atomic_write(path, text)
        # One open/append/close per file, not per record.
        grouped: dict[Path, list[str]] = {}
        for path, record in self._appends:
            grouped.setdefault(path, []).append(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
        for path, lines in grouped.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write("".join(lines))
        for path, data in self._documents:
            write_json(path, data)
        for path, state in self._states.items():
            write_json(path, state)
            legacy = self._legacy.get(path)
            if legacy is not None:
                try:
                    legacy.unlink()
                except OSError:
                    pass
        if self._index_requested:
            rebuild_indexes(self.root)
        elif self._feedback_index_requested:
            rebuild_feedback_index(self.root)


class SessionStore:
    """Entry point for every write to one hub's `.sessions/` directory."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @property
    def lock_path(self) -> Path:
        return self.root / LOCK_NAME

    @contextlib.contextmanager
    def transaction(self, timeout: float | None = None) -> Iterator[Transaction]:
        """Lock the store; commit the staged mutations once if the body succeeds."""
        with exclusive_lock(self.lock_path, timeout):
            tx = Transaction(self)
            yield tx
            tx.commit()


def mode_config(mode: str) -> dict[str, Any]:
    if mode not in MODE_CONFIGS:
        raise SystemExit(f"unknown session mode: {mode}")
    return merge_dict(DEFAULT_CONFIG, MODE_CONFIGS[mode])


def ensure_layout(root: Path) -> None:
    for rel in ["state", "queue", "digests", "indexes"]:
        (root / rel).mkdir(parents=True, exist_ok=True)


def omitted_digest(value: Any) -> tuple[int, str]:
    try:
        text = json.dumps(value, sort_keys=True, ensure_ascii=False)
    except TypeError:
        text = str(value)
    return len(text), hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def omitted_value(value: Any) -> str:
    length, digest = omitted_digest(value)
    return f"[OMITTED len={length} sha256={digest}]"


def omitted_input(value: Any) -> dict[str, Any]:
    """Summarize an input-shaped value as a hash. Nested command/old_string/new_string/content never survive."""
    length, digest = omitted_digest(value)
    summary: dict[str, Any] = {"sha256": digest, "length": length}
    if isinstance(value, dict):
        for key in INPUT_CLEAR_KEYS:
            if key in value:
                summary[key] = redact(value[key], key)
    return summary


def redact_scalar(value: Any, key_hint: str = "") -> Any:
    if value is None or isinstance(value, bool) or isinstance(value, (int, float)):
        return value
    if SECRET_KEY_RE.search(key_hint):
        return "[REDACTED]"
    text = str(value)
    # Key material is never partially kept: the whole value goes.
    if first_secret_category(text) == "secret-private-key":
        return "[REDACTED]"
    text = redact_secrets(text)
    if len(text) > MAX_EVENT_PREVIEW_CHARS:
        text = text[:MAX_EVENT_PREVIEW_CHARS] + "…"
    return text


def redact(value: Any, key_hint: str = "") -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, nested in value.items():
            key_text = str(key)
            if SECRET_KEY_RE.search(key_text):
                redacted[key_text] = "[REDACTED]"
            elif CONTENT_KEY_RE.search(key_text):
                redacted[key_text] = omitted_value(nested)
            elif INPUT_KEY_RE.search(key_text):
                redacted[key_text] = omitted_input(nested)
            else:
                redacted[key_text] = redact(nested, key_text)
        return redacted
    if isinstance(value, list):
        return [redact(item, key_hint) for item in value[:20]]
    return redact_scalar(value, key_hint)


def compact_json(value: Any, max_chars: int = MAX_EVENT_PREVIEW_CHARS) -> str:
    text = json.dumps(redact(value), sort_keys=True, ensure_ascii=False)
    if len(text) > max_chars:
        return text[:max_chars] + "…"
    return text


# The secret patterns are the control plane's redaction table (#88, #94). This
# script ships as one file and cannot import knowmade_control, so the plugin
# sync scripts stamp the table in from scripts/knowmade_control/redaction.py
# (scripts/sync-session-vendor.py); tests/test-session-capture.sh checks it.
# --- vendored from knowmade_control.redaction: start ---
# Generated from scripts/knowmade_control/redaction.py by scripts/sync-session-vendor.py; do not edit.

_I = re.IGNORECASE | re.MULTILINE

PATTERN_TABLE: tuple[tuple[str, str, str, str, int, frozenset[str]], ...] = (
    (
        "secret-private-key",
        "critical",
        "Possible private-key material.",
        # The whole block, header to matching footer (or the end of the text
        # when the footer is missing), so a redaction removes the key body too.
        r"-----BEGIN ((?:[A-Z0-9 ]+ )?PRIVATE KEY|PGP PRIVATE KEY BLOCK)-----"
        r"[\s\S]*?(?:-----END \1-----|\Z)",
        _I,
        frozenset({"checkpoint", "redact"}),
    ),
    (
        "secret-known-token",
        "critical",
        "Possible service credential or bearer token.",
        # Provider token shapes: AWS long-term and STS key ids, GitHub classic
        # and fine-grained tokens, Slack, OpenAI/Anthropic-style `sk-` keys
        # (hyphens included: `sk-ant-api03-...`, `sk-proj-...`), Stripe live
        # and test keys, GitLab and npm tokens, Google API keys and JWTs.
        r"(?:\b(?:AKIA|ASIA)[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9]{20,}\b|"
        r"\bgithub_pat_[A-Za-z0-9_]{20,}\b|"
        r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b|\bsk-[A-Za-z0-9_-]{16,}\b|"
        r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}\b|\bglpat-[A-Za-z0-9_-]{20,}\b|"
        r"\bnpm_[A-Za-z0-9]{36,}\b|"
        r"\bAIza[0-9A-Za-z_-]{25,}\b|"
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b)",
        _I,
        frozenset({"checkpoint", "redact"}),
    ),
    (
        "secret-known-token",
        "critical",
        "Possible service credential or bearer token.",
        r"(?P<keep>\bBearer\s+)[A-Za-z0-9._~+/=-]{12,}",
        _I,
        frozenset({"checkpoint", "redact"}),
    ),
    (
        "secret-assignment",
        "critical",
        "Possible credential assigned to a sensitive field.",
        r"(?P<keep>\b(?:api[_-]?key|private[_-]?key|(?:access|auth|api|refresh)?[_-]?token|"
        r"client[_-]?secret|password|passwd|secret|session[_-]?cookie)\b"
        r"\s*[\"']?\s*[:=]\s*[\"']?)"
        r"[A-Za-z0-9_./+=$~-]{8,}",
        _I,
        frozenset({"checkpoint", "redact"}),
    ),
    (
        "secret-url-credentials",
        "critical",
        "Possible username/password embedded in a URL.",
        r"(?P<keep>\b[a-z][a-z0-9+.-]*://[^\s/@:]+:)[^\s/@]+(?=@)",
        _I,
        frozenset({"checkpoint", "redact"}),
    ),
    (
        # Prose credentials ("my password is hunter2", "token: abc") of any
        # length. Redaction only: in a checkpoint, prose such as "the token
        # is stored in the keychain" would block every honest bundle.
        "secret-prose-credential",
        "critical",
        "Possible credential written as prose.",
        r"(?P<keep>\b(?:password|passwd|pwd|secret|token|api[_-]?key)(?:\s+is\s+|\s*[=:]\s*))"
        r"[\"']?[^\s\"']+",
        _I,
        frozenset({"redact"}),
    ),
    (
        # Opaque three-part dotted tokens (signed cookies, JWT-like values
        # without the `eyJ` header). Redaction only: long dotted identifiers
        # are ordinary prose in a checkpoint.
        "secret-opaque-token",
        "critical",
        "Possible opaque signed token.",
        r"\b[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\b",
        0,
        frozenset({"redact"}),
    ),
    (
        "personal-email",
        "warning",
        "Email address may identify a person or private account.",
        r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        0,
        frozenset({"checkpoint"}),
    ),
    (
        "personal-account-field",
        "warning",
        "Account or username field may expose an identity.",
        r"^[ \t]*[\"']?(?:user(?:name)?|login|account(?:[_-]?name)?|handle)"
        r"[\"']?[ \t]*[:=][ \t]*[\"']?@?[A-Za-z0-9_.-]{2,}",
        _I,
        frozenset({"checkpoint"}),
    ),
    (
        "local-home-path",
        "critical",
        "Machine-local home directory path.",
        # The `(?<![:A-Za-z0-9])` lookbehind keeps the POSIX forms anchored
        # at an absolute root so `api/Users/1` or `https://h/Users/x` stay
        # quiet. It also rejects `/c/Users/x` (preceded by `c`) and
        # `C:/Users/x` (preceded by `:`), so Git Bash, forward-slash
        # Windows, WSL and Cygwin drive roots get their own alternatives.
        r"(?:(?<![:A-Za-z0-9])/Users/[A-Za-z0-9._-]+(?:/|\b)|"
        r"(?<![:A-Za-z0-9])/home/[A-Za-z0-9._-]+(?:/|\b)|"
        r"(?<![:A-Za-z0-9])/root(?:/|\b)|[A-Za-z]:\\Users\\[A-Za-z0-9._ -]+(?:\\|\b)|"
        r"(?<![A-Za-z0-9])[A-Za-z]:/Users/[A-Za-z0-9._-]+(?:/|\b)|"
        r"(?<![:/A-Za-z0-9])/[A-Za-z]/Users/[A-Za-z0-9._-]+(?:/|\b)|"
        r"(?<![:/A-Za-z0-9])/(?:mnt|cygdrive)/[A-Za-z]/Users/[A-Za-z0-9._-]+(?:/|\b))",
        _I,
        frozenset({"checkpoint"}),
    ),
    (
        "local-machine-path",
        "critical",
        "Machine-local volume, temporary, network share, or file URL path.",
        # Drive roots: `D:/x`, `D:\x` (not preceded by a word character,
        # so `https://x/y` is not a drive) and Git Bash `/d/x` (not
        # preceded by `:`, `/` or a word character, so `https://x/y` and
        # relative `a/b/c` stay quiet). UNC shares: `\\server\share`, not
        # preceded by another backslash or a word character.
        r"(?:file://|(?:~|\$HOME|\$\{HOME\})/|(?<![:A-Za-z0-9])/Volumes/[^\s/]+(?:/|\b)|"
        r"(?<![:A-Za-z0-9])/private/var/folders/|(?<![:A-Za-z0-9])/(?:tmp|var|etc|opt|srv|mnt|media|workspace|Applications|Library)/[^\s`\"']+|"
        r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s`\"']+|"
        r"(?<![:/A-Za-z0-9])/[A-Za-z]/[^\s`\"']+|"
        r"(?<![\\A-Za-z0-9])\\\\[A-Za-z0-9][A-Za-z0-9._$-]*\\[^\s`\"'\\]+)",
        _I,
        frozenset({"checkpoint"}),
    ),
    (
        "remote-login-identity",
        "warning",
        "Remote-login syntax may expose a username or private host.",
        r"\b(?:ssh|scp|sftp|rsync)\b[^\n]*\b[A-Za-z0-9._-]+@[A-Za-z0-9._-]+",
        _I,
        frozenset({"checkpoint"}),
    ),
    (
        "local-network-address",
        "warning",
        "Private-network address may reveal local infrastructure.",
        r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|"
        r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b",
        0,
        frozenset({"checkpoint"}),
    ),
    (
        "local-hostname",
        "warning",
        "Local hostname may reveal private infrastructure.",
        r"(?:\b[A-Za-z0-9][A-Za-z0-9-]{0,62}\.local\b|"
        r"\b[A-Za-z0-9][A-Za-z0-9.-]{0,126}\.(?:lan|internal)\b|"
        r"^[ \t]*[\"']?hostname[\"']?[ \t]*[:=][ \t]*[\"']?[^\s\"']+)",
        _I,
        frozenset({"checkpoint"}),
    ),
    (
        "session-transcript",
        "critical",
        "Session or transcript metadata must not enter a checkpoint.",
        r"(?:transcript[_-]?path|rollout-[^\s]+\.jsonl|\.sessions/(?:digests|feedback)/|"
        r"\b(?:codex|claude):[0-9a-f]{8}-[0-9a-f-]{27,}\b|UserPromptSubmit|PreCompact|PostCompact)",
        _I,
        frozenset({"checkpoint"}),
    ),
    (
        "prompt-conversation-role",
        "warning",
        "Conversation-role content may be copied prompt or transcript text.",
        r"(?:^[ \t]*(?:system|developer|assistant|user)[ \t]*:[ \t]+\S|"
        r"[\"']role[\"'][ \t]*:[ \t]*[\"'](?:system|developer|assistant|user)[\"'])",
        _I,
        frozenset({"checkpoint"}),
    ),
    (
        "prompt-instruction-marker",
        "warning",
        "Prompt/instruction marker may expose agent instructions.",
        r"(?:<\|(?:system|user|assistant)\|>|\[INST\]|</?system>|"
        r"AGENTS\.md instructions for|(?:system|developer) prompt[ \t]*:)",
        _I,
        frozenset({"checkpoint"}),
    ),
    (
        "private-topic-path",
        "critical",
        "Path into a -PRIVATE topic must not enter a checkpoint.",
        # A whole path segment ending in the canonical uppercase `-PRIVATE`
        # suffix: `topics/x-PRIVATE/...`, `x-PRIVATE/goals.md` after a space,
        # `` `x-PRIVATE` `` in backticks, or `x-PRIVATE` alone. `\w` is
        # Unicode, so `topics/santé-PRIVATE/` is caught too. The lookarounds
        # stop at any non-path character, so `x-PRIVATE-notes/`,
        # `PRIVATE.md`, `<slug>-PRIVATE` and prose such as `non-private`
        # (case-sensitive on purpose) stay quiet.
        r"(?<![\w.-])[\w.-]+-PRIVATE(?![\w-])",
        0,
        frozenset({"checkpoint"}),
    ),
)

_COMPILED: dict[str, list[tuple[str, str, str, re.Pattern[str]]]] = {}


def pattern_specs(context: str) -> list[tuple[str, str, str, re.Pattern[str]]]:
    """``(category, severity, message, compiled pattern)`` for one context, in table order."""
    if context not in _COMPILED:
        _COMPILED[context] = [
            (category, severity, message, re.compile(regex, flags))
            for category, severity, message, regex, flags, contexts in PATTERN_TABLE
            if context in contexts
        ]
    return list(_COMPILED[context])


def first_secret_category(text: str) -> str | None:
    """The category of the first redaction row that matches ``text``, or None."""
    for category, _severity, _message, pattern in pattern_specs("redact"):
        if pattern.search(text):
            return category
    return None


def redact_secrets(text: str, marker: str = "[REDACTED]") -> str:
    """Replace every secret in ``text`` by ``marker``, keeping each row's ``keep`` prefix."""

    def replace(match: re.Match[str]) -> str:
        keep = match.groupdict().get("keep") or ""
        return keep + marker

    for _category, _severity, _message, pattern in pattern_specs("redact"):
        text = pattern.sub(replace, text)
    return text
# --- vendored from knowmade_control.redaction: end ---


CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}
WEAK_FEEDBACK_WORDS = frozenset({"no", "actually", "instead", "correct", "exactly", "perfect"})
MIN_WEAK_FEEDBACK_TOKENS = 3


def confidence_at_least(value: str, minimum: str) -> bool:
    return CONFIDENCE_RANK.get(value, 0) >= CONFIDENCE_RANK.get(minimum, 1)


def flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "content", "message", "prompt", "user_prompt", "feedback_text"):
            if key in value:
                text = flatten_text(value.get(key))
                if text:
                    return text
        return ""
    if isinstance(value, list):
        parts = [flatten_text(item) for item in value[:10]]
        return "\n".join(part for part in parts if part)
    return str(value)


def extract_user_text(payload: dict[str, Any]) -> str:
    for key in ("feedback_text", "prompt", "user_prompt", "message", "input", "content"):
        text = flatten_text(payload.get(key))
        if text:
            return normalize_whitespace(text)
    messages = payload.get("messages")
    if isinstance(messages, list) and messages:
        return normalize_whitespace(flatten_text(messages[-1]))
    return ""


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def redacted_feedback_preview(text: str) -> str:
    """The stored preview of a user prompt, which is untrusted (#88).

    A prompt that matches any secret pattern, prose passwords included, is
    stored only as ``[REDACTED <category>]``: a partial redaction of free text
    is too easy to get wrong, and the candidate's hash still deduplicates it.
    """
    category = first_secret_category(text)
    if category:
        return f"[REDACTED {category}]"
    preview = str(redact_scalar(text))
    if len(preview) > MAX_FEEDBACK_PREVIEW_CHARS:
        preview = preview[:MAX_FEEDBACK_PREVIEW_CHARS] + "…"
    return preview


def feedback_scope_hint(text: str, feedback_type: str) -> str:
    lower = text.lower()
    if re.search(r"\b(always|never|for future|next time|from now on|rule|policy)\b", lower):
        return "global"
    if re.search(r"\b(repo|project|pr|release|docs?|readme|website|tests?|config|hook|environment|env)\b", lower):
        return "repo"
    if re.search(r"\b(topic|wiki|raw/notes|article|source|inventory|dataset)\b", lower):
        return "topic"
    if feedback_type in {"approval", "decision"}:
        return "session"
    return "session"


def feedback_lesson(feedback_type: str, preview: str) -> str:
    if feedback_type == "correction":
        return f"User correction/redirection to remember: {preview}"
    if feedback_type == "preference":
        return f"User preference or design rule candidate: {preview}"
    if feedback_type == "approval":
        return f"User validated the immediately preceding result or action: {preview}"
    if feedback_type == "decision":
        return f"User approved proceeding with the prior plan or decision point: {preview}"
    return f"User feedback candidate: {preview}"


def classify_feedback_text(text: str, force: bool = False) -> dict[str, str] | None:
    normalized = normalize_whitespace(text)
    if not normalized:
        return None
    lower = normalized.lower()
    if not force and re.fullmatch(r"(ok|okay|cool|thanks|thank you|ty|k|👍|👌)[.!]*", lower):
        return None

    rules: list[tuple[str, str, str, list[str], str]] = [
        (
            "correction",
            "high",
            "high",
            [
                r"\b(no|wrong|incorrect|not quite|actually|instead|you missed|you should have|shouldn'?t|don't|do not)\b",
                r"\b(that'?s not|this is not|not what i meant|wrong file|wrong repo|wrong approach)\b",
            ],
            "correction/redirection pattern",
        ),
        (
            "preference",
            "high",
            "high",
            [
                r"\b(i prefer|i want|i'd like|i would like|for me|my environment)\b",
                r"\b(default should|should be|there should|we need|needs to|always|never|from now on|next time|for future)\b",
            ],
            "preference/rule pattern",
        ),
        (
            "approval",
            "medium",
            "medium",
            [
                r"\b(that worked|this worked|that fixed it|fixed it|works for me|looks good|lgtm|correct|exactly|perfect|great job|nice work)\b",
                r"\b(yes|yep|yeah)[, ]+(that'?s|that is|this is|it is)? ?(right|correct|good|perfect)\b",
            ],
            "approval/validation pattern",
        ),
        (
            "decision",
            "medium",
            "medium",
            [
                r"\b(go ahead|do it|let'?s do that|sounds good|approved|ship it|make the pr|release it|proceed)\b",
            ],
            "plan acceptance pattern",
        ),
    ]
    token_count = len(lower.split())
    for feedback_type, strength, confidence, patterns, rationale in rules:
        matches = [m for pattern in patterns for m in re.finditer(pattern, lower)]
        if not matches:
            continue
        # A bare "no" or "correct" is usually the answer to a yes/no question, not
        # a lesson to remember: a one- or two-word reply only counts when it
        # carries a trigger that means something on its own.
        if not force and token_count < MIN_WEAK_FEEDBACK_TOKENS and all(
            m.group(0).strip().lower() in WEAK_FEEDBACK_WORDS for m in matches
        ):
            continue
        if matches:
            preview = redacted_feedback_preview(normalized)
            return {
                "feedback_type": feedback_type,
                "strength": strength,
                "confidence": confidence,
                "scope_hint": feedback_scope_hint(normalized, feedback_type),
                "text_preview": preview,
                "text_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
                "distilled_lesson": feedback_lesson(feedback_type, preview),
                "rationale": rationale,
                "promotion_recommendation": "promote" if feedback_type in {"correction", "preference"} else "review",
            }
    if force:
        preview = redacted_feedback_preview(normalized)
        return {
            "feedback_type": "manual",
            "strength": "medium",
            "confidence": "medium",
            "scope_hint": feedback_scope_hint(normalized, "manual"),
            "text_preview": preview,
            "text_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            "distilled_lesson": feedback_lesson("manual", preview),
            "rationale": "manual feedback capture",
            "promotion_recommendation": "review",
        }
    return None


def detect_harness(args: argparse.Namespace, payload: dict[str, Any]) -> str:
    if getattr(args, "harness", None):
        return str(args.harness)
    env_harness = os.environ.get("LLM_WIKI_HARNESS")
    if env_harness:
        return env_harness
    if os.environ.get("PLUGIN_ROOT") or "model" in payload:
        return "codex"
    if "permission_mode" in payload or "agent_id" in payload:
        return "claude"
    return "unknown"


def fallback_session_id(payload: dict[str, Any]) -> str:
    parts = [
        str(payload.get("transcript_path") or ""),
        str(payload.get("cwd") or ""),
        str(payload.get("model") or ""),
    ]
    basis = "|".join(parts).strip("|") or utc_now()
    return "derived-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def git_value(cwd: str | None, args: list[str]) -> str | None:
    if not cwd:
        return None
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1.5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = result.stdout.strip()
    return value or None


def strip_remote_userinfo(value: str | None) -> str | None:
    """Drop user:password@ from a git remote before it is stored anywhere."""
    if not value:
        return None
    try:
        parts = urllib.parse.urlsplit(value)
    except ValueError:
        return value.rsplit("@", 1)[-1]
    if parts.netloc and "@" in parts.netloc:
        host = parts.netloc.rsplit("@", 1)[1]
        return urllib.parse.urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))
    if not parts.netloc and "@" in value:
        # scp-like form: keep git@host:path, strip user:password@host:path.
        userinfo, rest = value.split("@", 1)
        if ":" in userinfo:
            return rest
    return value


def git_remote(cwd: str | None) -> str | None:
    return strip_remote_userinfo(git_value(cwd, ["config", "--get", "remote.origin.url"]))


def normalize_event(args: argparse.Namespace, payload: dict[str, Any], root: Path) -> dict[str, Any]:
    now = utc_now()
    harness = safe_id(detect_harness(args, payload))
    native_session_id = str(
        getattr(args, "session_id", None)
        or payload.get("session_id")
        or payload.get("sessionId")
        or fallback_session_id(payload)
    )
    event_name = str(
        getattr(args, "event_name", None)
        or payload.get("hook_event_name")
        or payload.get("hookEventName")
        or payload.get("event")
        or "manual"
    )
    cwd = str(getattr(args, "cwd", None) or payload.get("cwd") or os.getcwd())
    transcript_path = payload.get("transcript_path") or payload.get("transcriptPath")
    tool_name = payload.get("tool_name") or payload.get("toolName") or payload.get("tool")
    trigger = str(getattr(args, "trigger", None) or event_name)
    event = {
        "schema_version": SCHEMA_VERSION,
        "ts": now,
        "event": "session_event",
        "harness": harness,
        "hook_event_name": event_name,
        "native_session_id": native_session_id,
        "llm_wiki_session_id": f"{harness}:{native_session_id}",
        "cwd": cwd,
        "transcript_path": str(transcript_path) if transcript_path else None,
        "model": payload.get("model"),
        "turn_id": payload.get("turn_id") or payload.get("turnId"),
        "tool_name": str(tool_name) if tool_name else None,
        "tool_use_id": payload.get("tool_use_id") or payload.get("toolUseId"),
        "trigger": trigger,
        "payload_preview": compact_json(payload, max_chars=int(getattr(args, "max_event_bytes", MAX_EVENT_PREVIEW_CHARS))),
    }
    if getattr(args, "topic", None):
        event["topics"] = [args.topic]
    if getattr(args, "summary", None):
        event["manual_summary"] = str(args.summary)
    if transcript_path:
        event["transcript_path_hash"] = hashlib.sha256(str(transcript_path).encode("utf-8")).hexdigest()
    return event


def event_queue_path(root: Path, event: dict[str, Any]) -> Path:
    return root / "queue" / f"{event['ts'][:10]}.jsonl"


def git_info(cwd: str | None) -> dict[str, str | None]:
    return {"remote": git_remote(cwd), "branch": git_value(cwd, ["branch", "--show-current"])}


def event_git_info(root: Path, event: dict[str, Any]) -> dict[str, str | None] | None:
    """Probe git once for this event, before the store lock is taken.

    Tool events of an already-known session in an unchanged cwd return ``None``
    ("keep what the state records"): PostToolUse fires per tool call and two
    ``git`` subprocesses each time dominated the hook's cost. Prompts, compaction,
    stop and manual events always refresh, so a branch switch is picked up at the
    next prompt.
    """
    if is_tool_event(event):
        peek = read_json(state_path(root, event["harness"], event["native_session_id"]), {})
        if (
            isinstance(peek, dict)
            and peek.get("cwd") == event.get("cwd")
            and "git_remote" in peek
            and "git_branch" in peek
        ):
            return None
    return git_info(event.get("cwd"))


def update_state(
    tx: Transaction,
    event: dict[str, Any],
    config: dict[str, Any],
    git: dict[str, str | None] | None,
) -> tuple[dict[str, Any], bool]:
    """Apply one event to its session state (loaded under the lock, staged for one write)."""
    state, is_new = tx.load_state(event["harness"], event["native_session_id"])
    now = str(event["ts"])
    state.setdefault("schema_version", SCHEMA_VERSION)
    state.setdefault("harness", event["harness"])
    state.setdefault("native_session_id", event["native_session_id"])
    state.setdefault("llm_wiki_session_id", event["llm_wiki_session_id"])
    state.setdefault("started_at", now)
    state["last_seen_at"] = now
    state["cwd"] = event.get("cwd")
    state["transcript_path"] = event.get("transcript_path")
    state["model"] = event.get("model") or state.get("model")
    state["privacy"] = config.get("privacy", "redacted")
    state["raw_transcripts"] = bool(config.get("raw_transcripts", False))
    if git is not None:
        state["git_remote"] = git["remote"]
        state["git_branch"] = git["branch"]
    else:
        state.setdefault("git_remote", None)
        state.setdefault("git_branch", None)
    state["event_count"] = int(state.get("event_count") or 0) + 1
    if is_tool_event(event):
        state["tool_event_count"] = int(state.get("tool_event_count") or 0) + 1
    else:
        state.setdefault("tool_event_count", int(state.get("tool_event_count") or 0))
    topics = set(as_list(state.get("topics")))
    topics.update(str(item) for item in as_list(event.get("topics")))
    if topics:
        state["topics"] = sorted(topics)
    summaries = as_list(state.get("manual_summaries"))
    if event.get("manual_summary"):
        summaries.append(str(event["manual_summary"]))
        state["manual_summaries"] = summaries[-10:]
    if event.get("hook_event_name") == "PostCompact":
        state["last_compact_event"] = event.get("payload_preview")
    last_events = as_list(state.get("last_events"))
    last_events.append(
        {
            "ts": event.get("ts"),
            "hook_event_name": event.get("hook_event_name"),
            "tool_name": event.get("tool_name"),
            "turn_id": event.get("turn_id"),
            "trigger": event.get("trigger"),
        }
    )
    state["last_events"] = last_events[-MAX_LAST_EVENTS:]
    tx.stage_state(state)
    return state, is_new


def session_seen_record(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "ts": event["ts"],
        "event": "session_seen",
        "llm_wiki_session_id": event["llm_wiki_session_id"],
        "harness": event["harness"],
        "native_session_id": event["native_session_id"],
        "cwd": event.get("cwd"),
        "transcript_path": event.get("transcript_path"),
    }


def record_event(
    tx: Transaction,
    event: dict[str, Any],
    config: dict[str, Any],
    git: dict[str, str | None] | None,
) -> tuple[dict[str, Any], bool]:
    """Queue the event, update its state, and note a first sighting in the registry."""
    tx.append(event_queue_path(tx.root, event), event)
    state, is_new = update_state(tx, event, config, git)
    if is_new:
        tx.append(tx.root / "registry.jsonl", session_seen_record(event))
    return state, is_new


def feedback_candidates_path(root: Path) -> Path:
    return root / "feedback" / "candidates.jsonl"


def feedback_status_path(root: Path) -> Path:
    return root / "feedback" / "status.json"


def candidate_exists(root: Path, candidate_id: str) -> bool:
    """Whether a candidate with this id was already recorded.

    Ids are hex digests and the file is written with ``json.dumps(sort_keys=True)``,
    so a substring probe is exact (any quote inside a preview is backslash-escaped)
    and avoids parsing every line of a log that only grows.
    """
    path = feedback_candidates_path(root)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return f'"id": "{candidate_id}"' in text


def build_feedback_candidate(
    event: dict[str, Any],
    payload: dict[str, Any],
    config: dict[str, Any],
    state: dict[str, Any],
    force: bool = False,
) -> dict[str, Any] | None:
    feedback_cfg = config.get("feedback") if isinstance(config.get("feedback"), dict) else {}
    if not force and feedback_cfg.get("enabled", True) is False:
        return None
    event_name = str(event.get("hook_event_name") or "")
    if not force and event_name not in {"UserPromptSubmit", "ManualFeedback"}:
        return None
    text = extract_user_text(payload)
    classification = classify_feedback_text(text, force=force)
    if not classification:
        return None
    if classification["feedback_type"] == "approval" and feedback_cfg.get("capture_approvals", True) is False and not force:
        return None
    minimum = str(feedback_cfg.get("min_confidence") or "medium")
    if not force and not confidence_at_least(classification["confidence"], minimum):
        return None
    basis = "|".join(
        [
            str(event.get("llm_wiki_session_id") or ""),
            str(event.get("turn_id") or ""),
            event_name,
            classification["feedback_type"],
            classification["text_sha256"],
        ]
    )
    candidate_id = "fb-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]
    return {
        "schema_version": SCHEMA_VERSION,
        "id": candidate_id,
        "ts": event.get("ts") or utc_now(),
        "event": "feedback_candidate",
        "llm_wiki_session_id": event.get("llm_wiki_session_id"),
        "harness": event.get("harness"),
        "native_session_id": event.get("native_session_id"),
        "cwd": event.get("cwd"),
        "git_remote": state.get("git_remote"),
        "git_branch": state.get("git_branch"),
        "hook_event_name": event_name,
        "turn_id": event.get("turn_id"),
        **classification,
    }


def maybe_record_feedback_candidate(
    tx: Transaction,
    state: dict[str, Any],
    event: dict[str, Any],
    payload: dict[str, Any],
    config: dict[str, Any],
    force: bool = False,
) -> dict[str, Any] | None:
    candidate = build_feedback_candidate(event, payload, config, state, force=force)
    if not candidate:
        return None
    if candidate_exists(tx.root, candidate["id"]):
        return None
    tx.append(feedback_candidates_path(tx.root), candidate)
    tx.append(
        tx.root / "registry.jsonl",
        {
            "schema_version": SCHEMA_VERSION,
            "ts": candidate["ts"],
            "event": "feedback_candidate",
            "id": candidate["id"],
            "llm_wiki_session_id": candidate.get("llm_wiki_session_id"),
            "feedback_type": candidate.get("feedback_type"),
            "confidence": candidate.get("confidence"),
            "cwd": candidate.get("cwd"),
        },
    )
    last = as_list(state.get("last_feedback_candidates"))
    last.append(
        {
            "id": candidate["id"],
            "ts": candidate["ts"],
            "feedback_type": candidate.get("feedback_type"),
            "confidence": candidate.get("confidence"),
            "distilled_lesson": candidate.get("distilled_lesson"),
        }
    )
    state["last_feedback_candidates"] = last[-10:]
    state["feedback_candidate_count"] = int(state.get("feedback_candidate_count") or 0) + 1
    tx.stage_state(state)
    return candidate


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def is_tool_event(event: dict[str, Any]) -> bool:
    name = str(event.get("hook_event_name") or "")
    return bool(event.get("tool_name")) or name in {"PreToolUse", "PostToolUse", "PermissionRequest", "BeforeTool", "AfterTool"}


def should_write_digest(state: dict[str, Any], event: dict[str, Any], config: dict[str, Any], force: bool = False) -> tuple[bool, str]:
    if force:
        return True, str(event.get("trigger") or "manual")
    auto = config.get("auto_capture") if isinstance(config.get("auto_capture"), dict) else {}
    event_name = str(event.get("hook_event_name") or "")
    lower = event_name.lower().replace("_", "")
    if lower in {"precompact", "precompress"} and auto.get("pre_compact", True):
        return True, "pre-compact"
    if lower in {"postcompact", "postcompress"} and auto.get("post_compact", True):
        return True, "post-compact"
    if lower in {"sessionend", "afteragent"} and auto.get("session_end", True):
        return True, "session-end"
    if lower in {"stop", "stopfailure"} and auto.get("stop", True):
        return True, "stop"
    threshold = int(auto.get("tool_events") or 0)
    tool_count = int(state.get("tool_event_count") or 0)
    if threshold > 0 and is_tool_event(event) and tool_count > 0 and tool_count % threshold == 0:
        return True, f"tool-count-{threshold}"
    return False, ""


def yaml_string(value: Any) -> str:
    text = "" if value is None else str(value)
    if text == "":
        return '""'
    if re.fullmatch(r"[A-Za-z0-9_.:/@+ -]+", text):
        return json.dumps(text)
    return json.dumps(text)


def yaml_list(values: list[Any]) -> str:
    return "[" + ", ".join(yaml_string(item) for item in values) + "]"


def digest_summary(state: dict[str, Any], trigger: str) -> str:
    manual = as_list(state.get("manual_summaries"))
    if manual:
        return str(manual[-1])[:240]
    cwd = state.get("cwd") or "unknown cwd"
    return f"Automated {trigger} checkpoint for {state.get('llm_wiki_session_id')} in {cwd}."


def frontmatter_block(state: dict[str, Any], trigger: str, digest_file: Path) -> str:
    summary = digest_summary(state, trigger)
    topics = as_list(state.get("topics"))
    lines = [
        "---",
        f"title: {yaml_string('Session Digest: ' + str(state.get('llm_wiki_session_id')))}",
        "type: session-digest",
        f"schema_version: {SCHEMA_VERSION}",
        f"harness: {yaml_string(state.get('harness'))}",
        f"native_session_id: {yaml_string(state.get('native_session_id'))}",
        f"llm_wiki_session_id: {yaml_string(state.get('llm_wiki_session_id'))}",
        f"cwd: {yaml_string(state.get('cwd'))}",
        f"git_remote: {yaml_string(state.get('git_remote'))}",
        f"git_branch: {yaml_string(state.get('git_branch'))}",
        f"transcript_path: {yaml_string(state.get('transcript_path'))}",
        f"started_at: {yaml_string(state.get('started_at'))}",
        f"last_seen_at: {yaml_string(state.get('last_seen_at'))}",
        f"tool_event_count: {int(state.get('tool_event_count') or 0)}",
        f"event_count: {int(state.get('event_count') or 0)}",
        f"capture_trigger: {yaml_string(trigger)}",
        f"topics: {yaml_list(topics)}",
        "topic_candidates: []",
        f"privacy: {yaml_string(state.get('privacy') or 'redacted')}",
        f"raw_transcripts: {str(bool(state.get('raw_transcripts'))).lower()}",
        "promoted_to: []",
        f"summary: {yaml_string(summary)}",
        "---",
    ]
    return "\n".join(lines) + "\n"


def markdown_table(rows: list[tuple[str, str]]) -> str:
    out = ["| Field | Value |", "|---|---|"]
    for key, value in rows:
        safe = str(value or "").replace("|", "\\|").replace("\n", " ")
        out.append(f"| {key} | {safe} |")
    return "\n".join(out)


def render_digest(state: dict[str, Any], trigger: str, digest_file: Path) -> str:
    summary = digest_summary(state, trigger)
    last_events = as_list(state.get("last_events"))[-12:]
    event_lines = []
    for event in last_events:
        event_lines.append(
            f"- `{event.get('ts')}` — {event.get('hook_event_name')}"
            + (f" / {event.get('tool_name')}" if event.get("tool_name") else "")
        )
    if not event_lines:
        event_lines.append("- No hook events recorded yet.")

    manual = as_list(state.get("manual_summaries"))
    manual_lines = [f"- {item}" for item in manual[-5:]] or ["- None recorded."]
    feedback = as_list(state.get("last_feedback_candidates"))
    feedback_lines = [
        f"- `{item.get('id')}` — {item.get('feedback_type')} / {item.get('confidence')}: {item.get('distilled_lesson')}"
        for item in feedback[-5:]
        if isinstance(item, dict)
    ] or ["- None detected."]

    body = f"""
# Session Digest: {state.get('llm_wiki_session_id')}

## Summary

{summary}

## Session Identity

{markdown_table([
        ('Harness', state.get('harness')),
        ('Native session ID', state.get('native_session_id')),
        ('llm-wiki session ID', state.get('llm_wiki_session_id')),
        ('CWD', state.get('cwd')),
        ('Git remote', state.get('git_remote')),
        ('Git branch', state.get('git_branch')),
        ('Transcript pointer', state.get('transcript_path')),
        ('Started at', state.get('started_at')),
        ('Last seen at', state.get('last_seen_at')),
        ('Capture trigger', trigger),
        ('Observed tool events', state.get('tool_event_count')),
        ('Observed total events', state.get('event_count')),
    ])}

## Manual Summaries

{chr(10).join(manual_lines)}

## Feedback Candidates

{chr(10).join(feedback_lines)}

## Recent Observed Events

{chr(10).join(event_lines)}

## Distillation Notes

- This digest is an automated checkpoint generated from redacted hook metadata.
- Raw transcript text is not copied by default; use the transcript pointer only if the harness still owns a readable transcript and the user approves.
- Promote useful distilled learnings into a topic wiki with `llm-wiki-session promote`; do not treat this operational digest as canonical topic knowledge until promoted.

## Open Loops

- Review whether this session should be topic-tagged or promoted.
- If the session ended mid-task, use `llm-wiki-session rehydrate --session-id {state.get('llm_wiki_session_id')}` before continuing.
""".strip()
    return frontmatter_block(state, trigger, digest_file) + "\n" + body + "\n"


def write_digest(tx: Transaction, state: dict[str, Any], trigger: str) -> Path:
    """Stage a digest, the state fields that point at it, and the index rebuild it triggers."""
    path = digest_path(tx.root, str(state["harness"]), str(state["native_session_id"]), str(state.get("last_seen_at") or utc_now()))
    text = render_digest(state, trigger, path)
    tx.write_file(path, text)
    state["last_digest_path"] = str(path)
    state["last_digest_at"] = utc_now()
    state["digest_count"] = int(state.get("digest_count") or 0) + 1
    tx.stage_state(state)
    tx.append(
        tx.root / "registry.jsonl",
        {
            "schema_version": SCHEMA_VERSION,
            "ts": utc_now(),
            "event": "digest_written",
            "llm_wiki_session_id": state.get("llm_wiki_session_id"),
            "harness": state.get("harness"),
            "native_session_id": state.get("native_session_id"),
            "digest_path": str(path),
            "capture_trigger": trigger,
            "cwd": state.get("cwd"),
            "topics": as_list(state.get("topics")),
        },
    )
    tx.request_index()
    return path


def iter_feedback_candidates(root: Path) -> list[dict[str, Any]]:
    path = feedback_candidates_path(root)
    status = read_json(feedback_status_path(root), {"promoted": {}}) if feedback_status_path(root).exists() else {"promoted": {}}
    promoted = status.get("promoted") if isinstance(status.get("promoted"), dict) else {}
    candidates: list[dict[str, Any]] = []
    if not path.exists():
        return candidates
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        cid = str(data.get("id") or "")
        if cid in promoted:
            data["promoted"] = promoted[cid]
        candidates.append(data)
    return sorted(candidates, key=lambda item: str(item.get("ts") or ""), reverse=True)


def rebuild_feedback_index(root: Path) -> None:
    candidates = iter_feedback_candidates(root)
    by_session: dict[str, list[str]] = {}
    by_type: dict[str, list[str]] = {}
    unpromoted: list[str] = []
    for candidate in candidates:
        cid = str(candidate.get("id") or "")
        if not cid:
            continue
        session_id = str(candidate.get("llm_wiki_session_id") or "")
        if session_id:
            by_session.setdefault(session_id, []).append(cid)
        feedback_type = str(candidate.get("feedback_type") or "unknown")
        by_type.setdefault(feedback_type, []).append(cid)
        if not candidate.get("promoted"):
            unpromoted.append(cid)
    write_json(
        root / "indexes" / "feedback.json",
        {
            "schema_version": SCHEMA_VERSION,
            "candidate_count": len(candidates),
            "unpromoted_count": len(unpromoted),
            "by_session": by_session,
            "by_type": by_type,
            "unpromoted": unpromoted,
            "candidates": candidates,
        },
    )


def rebuild_indexes(root: Path) -> None:
    by_cwd: dict[str, list[dict[str, Any]]] = {}
    by_topic: dict[str, list[dict[str, Any]]] = {}
    states: list[dict[str, Any]] = []
    for path in sorted((root / "state").glob("*/*.json")):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(state, dict):
            continue
        states.append(state)
        record = {
            "llm_wiki_session_id": state.get("llm_wiki_session_id"),
            "harness": state.get("harness"),
            "native_session_id": state.get("native_session_id"),
            "last_seen_at": state.get("last_seen_at"),
            "last_digest_path": state.get("last_digest_path"),
        }
        cwd = str(state.get("cwd") or "")
        if cwd:
            by_cwd.setdefault(cwd, []).append(record)
        for topic in as_list(state.get("topics")):
            by_topic.setdefault(str(topic), []).append(record)
    write_json(root / "indexes" / "by-cwd.json", by_cwd)
    write_json(root / "indexes" / "by-topic.json", by_topic)
    write_json(root / "indexes" / "sessions.json", {"sessions": states})
    rebuild_feedback_index(root)


def configure_utf8_stdio() -> None:
    """Switch stdin/stdout/stderr to UTF-8 when the streams support it.

    On Windows a hook process whose streams are pipes inherits the ANSI code
    page (for example cp1251), so an emoji prompt raised UnicodeDecodeError and
    CJK context raised UnicodeEncodeError, killing SessionStart injection. This
    file ships verbatim as a plugin hook, so the helper is inlined here instead
    of imported from knowmade_control.stdio.
    """
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            continue


def read_hook_input() -> str:
    """Read the whole hook payload as UTF-8 regardless of the console code page."""
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is None:
        return sys.stdin.read()
    return buffer.read().decode("utf-8", errors="replace")


def hook_output(event_name: str, context: str) -> None:
    if not context.strip():
        return
    text = json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": event_name,
                "additionalContext": context,
            }
        },
        ensure_ascii=False,
    )
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        print(text)
        return
    sys.stdout.flush()
    buffer.write((text + "\n").encode("utf-8", errors="replace"))
    buffer.flush()


def warn(message: str) -> None:
    print(f"llm-wiki-session: {message}", file=sys.stderr)


def hook_root(args: argparse.Namespace) -> Path | None:
    """The `.sessions` directory a hook may write to, or ``None`` to do nothing.

    A hook runs on every machine that has the plugin installed, so it only ever
    writes into a hub that already exists: it never creates the hub, and a resolver
    failure (broken hub config, a `private_hub_path` leftover) is a warning and a
    no-op rather than a crash or a guess at ``~/wiki``.
    """
    try:
        hub = resolve_hub(args)
    except SystemExit as exc:
        warn(f"hook skipped, cannot resolve the hub: {exc}")
        return None
    if not hub.is_dir():
        return None
    return sessions_dir(hub)


def run_hook(args: argparse.Namespace) -> int:
    try:
        return hook_body(args)
    except (HookSkip, SystemExit):
        raise
    except SessionLockTimeout as exc:
        warn(f"hook event not recorded: {exc}")
        return 0
    except Exception as exc:  # a hook must never break the agent's tool call
        if os.environ.get("LLM_WIKI_SESSION_DEBUG"):
            raise
        warn(f"hook event not recorded: {type(exc).__name__}: {exc}")
        return 0


def hook_body(args: argparse.Namespace) -> int:
    raw = read_hook_input()  # drain stdin even when we end up skipping the event
    root = hook_root(args)
    if root is None:
        return 0
    config = load_config(root)
    if config.get("config_error"):
        warn(f"hook skipped (config treated as disabled): {config['config_error']}")
        raise HookSkip()
    if args.if_enabled and not config.get("enabled"):
        raise HookSkip()
    payload: dict[str, Any]
    if raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"invalid hook JSON input: {exc}") from exc
        if not isinstance(parsed, dict):
            raise SystemExit("hook JSON input must be an object")
        payload = parsed
    else:
        payload = {}
    ensure_layout(root)
    event = normalize_event(args, payload, root)
    git = event_git_info(root, event)
    with SessionStore(root).transaction() as tx:
        state, _ = record_event(tx, event, config, git)
        maybe_record_feedback_candidate(tx, state, event, payload, config)
        write, trigger = should_write_digest(state, event, config, force=False)
        if write:
            write_digest(tx, state, trigger)

    event_name = str(event.get("hook_event_name") or "")
    rehydrate_cfg = config.get("rehydrate") if isinstance(config.get("rehydrate"), dict) else {}
    if event_name == "SessionStart" and rehydrate_cfg.get("session_start"):
        hook_output(event_name, build_rehydrate_context(root, cwd=event.get("cwd"), limit=3))
    elif event_name == "UserPromptSubmit" and rehydrate_cfg.get("user_prompt"):
        hook_output(event_name, build_rehydrate_context(root, cwd=event.get("cwd"), limit=3))
    return 0


def command_root(args: argparse.Namespace) -> Path:
    """The `.sessions` directory for a command that writes; creates the hub only if the caller named it."""
    hub = resolve_hub(args)
    if not hub.is_dir():
        if not hub_is_explicit(args):
            raise SystemExit(
                f"hub not found: {hub}\n"
                "Initialize a hub first, set hub_path in ~/.config/llm-wiki/config.json or LLM_WIKI_HUB, or pass --hub."
            )
        hub.mkdir(parents=True, exist_ok=True)
    root = sessions_dir(hub)
    ensure_layout(root)
    return root


# The hub ignore rule for session state (knowmade#98). The control plane's
# `hub.STATE_IGNORE_RULES` carries the same rule with the rest of the machine
# state block; this script ships standalone, so it vendors only this one.
SESSIONS_IGNORE_RULE = "/.sessions/"
SESSIONS_IGNORE_PROBE = ".sessions/state/knowmade-ignore-probe.json"


def _git_in(hub: Path, args: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", *args], cwd=str(hub), capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def ensure_sessions_ignored(hub: Path) -> Path | None:
    """Append `/.sessions/` to HUB/.gitignore when git would otherwise commit session state.

    Only acts when git says the hub is inside a work tree (a nested hub counts)
    and `.sessions/` is not ignored yet. The existing file is kept byte for byte
    and the rule uses its newline style. Returns the `.gitignore` it changed.
    """
    inside = _git_in(hub, ["rev-parse", "--is-inside-work-tree"])
    if inside is None or inside.returncode != 0 or inside.stdout.strip() != "true":
        return None
    probe = _git_in(hub, ["check-ignore", "-q", "--", SESSIONS_IGNORE_PROBE])
    if probe is None or probe.returncode != 1:
        return None  # already ignored (0) or git could not answer
    gitignore = hub / ".gitignore"
    existing = gitignore.read_bytes() if gitignore.is_file() else b""
    first = existing.find(b"\n")
    newline = b"\r\n" if first > 0 and existing[first - 1 : first] == b"\r" else b"\n"
    lead = newline if existing and not existing.endswith(b"\n") else b""
    block = b"# Knowmade session capture state" + newline + SESSIONS_IGNORE_RULE.encode() + newline
    with open(gitignore, "ab") as handle:
        handle.write(lead + block)
    return gitignore


def run_enable(args: argparse.Namespace) -> int:
    root = command_root(args)
    ignored_in = ensure_sessions_ignored(root.parent)
    if ignored_in is not None:
        print(f"gitignore: appended {SESSIONS_IGNORE_RULE} to {ignored_in}")
    with SessionStore(root).transaction() as tx:
        config = mode_config(args.mode)
        try:
            existing = read_config_strict(root / "config.json")
        except ConfigError as exc:
            moved = quarantine_config(root)
            warn(f"replacing unusable config ({exc})" + (f"; kept as {moved}" if moved else ""))
            existing = None
        if existing is not None and not args.force:
            config = merge_dict(merge_dict(DEFAULT_CONFIG, existing), config)
        if args.tool_events is not None:
            config.setdefault("auto_capture", {})["tool_events"] = int(args.tool_events)
        config["enabled"] = args.mode != "off"
        tx.write_document(root / "config.json", config)
    print(f"llm-wiki sessions {config['mode']}: {root}")
    print(f"enabled: {config['enabled']}")
    print("raw_transcripts: false")
    return 0


def run_disable(args: argparse.Namespace) -> int:
    root = command_root(args)
    with SessionStore(root).transaction() as tx:
        config = load_config(root)
        if config.pop("config_error", None):
            moved = quarantine_config(root)
            warn("replacing unusable config" + (f"; kept as {moved}" if moved else ""))
            config = merge_dict(DEFAULT_CONFIG, {})
        config["enabled"] = False
        config["mode"] = "off"
        tx.write_document(root / "config.json", config)
    print(f"llm-wiki sessions disabled: {root}")
    return 0


def unique_manual_id(prefix: str) -> str:
    """A manual session id that two calls in the same second cannot share."""
    return f"{prefix}-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def manual_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "session_id": args.session_id or unique_manual_id("manual"),
        "hook_event_name": "ManualCapture",
        "cwd": args.cwd or os.getcwd(),
        "transcript_path": args.transcript_path,
        "model": args.model,
        "manual_summary": args.summary,
    }


def run_capture(args: argparse.Namespace) -> int:
    root = command_root(args)
    config = load_config(root)
    payload = manual_payload(args)
    event = normalize_event(args, payload, root)
    git = event_git_info(root, event)
    with SessionStore(root).transaction() as tx:
        state, _ = record_event(tx, event, config, git)
        path = write_digest(tx, state, "manual")
    print(str(path))
    return 0


def run_index(args: argparse.Namespace) -> int:
    """Rebuild the derived indexes. The hook no longer does this per event."""
    root = command_root(args)
    with SessionStore(root).transaction() as tx:
        tx.request_index()
    print(f"llm-wiki sessions indexes rebuilt: {root / 'indexes'}")
    return 0


def iter_states(root: Path) -> list[dict[str, Any]]:
    states = []
    for path in sorted((root / "state").glob("*/*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            states.append(data)
    return sorted(states, key=lambda item: str(item.get("last_seen_at") or ""), reverse=True)


def session_matches(state: dict[str, Any], args: argparse.Namespace) -> bool:
    if getattr(args, "harness", None) and state.get("harness") != args.harness:
        return False
    if getattr(args, "cwd", None) and state.get("cwd") != args.cwd:
        return False
    if getattr(args, "topic", None) and args.topic not in as_list(state.get("topics")):
        return False
    return True


def run_list(args: argparse.Namespace) -> int:
    root = sessions_dir(resolve_hub(args))
    states = [state for state in iter_states(root) if session_matches(state, args)]
    if args.json:
        print(json.dumps({"sessions_root": str(root), "sessions": states}, indent=2, sort_keys=True))
        return 0
    print(f"llm-wiki sessions: {root}")
    print("| Session | Last seen | Tools | CWD | Digest |")
    print("|---|---:|---:|---|---|")
    for state in states[: args.limit]:
        print(
            "| {sid} | {last} | {tools} | {cwd} | {digest} |".format(
                sid=str(state.get("llm_wiki_session_id") or "").replace("|", "\\|"),
                last=str(state.get("last_seen_at") or ""),
                tools=int(state.get("tool_event_count") or 0),
                cwd=str(state.get("cwd") or "").replace("|", "\\|"),
                digest=str(state.get("last_digest_path") or ""),
            )
        )
    return 0


def find_session(root: Path, session_id: str) -> dict[str, Any] | None:
    for state in iter_states(root):
        if session_id in {state.get("llm_wiki_session_id"), state.get("native_session_id")}:
            return state
    return None


# --- vendored from knowmade_control.markdown: start ---
# Generated from scripts/knowmade_control/markdown.py by scripts/sync-session-vendor.py; do not edit.

BOM = "﻿"
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
# --- vendored from knowmade_control.markdown: end ---


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    # Digests are written by this script (LF, no BOM) but may be edited by hand;
    # the fence is found by the control plane's exact-fence rule (BOM and CRLF
    # accepted, the closing line exactly ``---``), vendored above.
    bounds = frontmatter_bounds(text)
    if bounds is None:
        return {}, text.lstrip(BOM).replace("\r\n", "\n")
    start, end, body_start = bounds
    raw = text[start:end].replace("\r\n", "\n").rstrip("\r")
    # The body starts on the line after the closing fence.
    rest = text[body_start:].replace("\r\n", "\n")
    newline = rest.find("\n")
    body = rest[newline + 1 :] if newline != -1 else ""
    data: dict[str, Any] = {}
    for line in raw.splitlines():
        if not line.strip() or ":" not in line or line.startswith(" "):
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            try:
                data[key.strip()] = json.loads(value)
            except json.JSONDecodeError:
                data[key.strip()] = []
        else:
            data[key.strip()] = value.strip('"')
    return data, body


def run_show(args: argparse.Namespace) -> int:
    root = sessions_dir(resolve_hub(args))
    state = find_session(root, args.session_id)
    if not state:
        raise SystemExit(f"session not found: {args.session_id}")
    digest = state.get("last_digest_path")
    if not digest:
        raise SystemExit(f"session has no digest yet: {args.session_id}")
    path = Path(str(digest))
    if args.path_only:
        print(str(path))
    else:
        print(path.read_text(encoding="utf-8"))
    return 0


def build_rehydrate_context(root: Path, cwd: str | None = None, session_id: str | None = None, topic: str | None = None, limit: int = 3) -> str:
    candidates = iter_states(root)
    selected: list[dict[str, Any]] = []
    for state in candidates:
        if session_id and session_id not in {state.get("llm_wiki_session_id"), state.get("native_session_id")}:
            continue
        if topic and topic not in as_list(state.get("topics")):
            continue
        if cwd and not session_id and not topic and state.get("cwd") != cwd:
            continue
        if state.get("last_digest_path"):
            selected.append(state)
        if len(selected) >= limit:
            break
    if not selected:
        return ""
    lines = [
        "llm-wiki session context: review these distilled session digests before continuing.",
    ]
    for state in selected:
        lines.append(
            f"- {state.get('llm_wiki_session_id')} — {digest_summary(state, 'rehydrate')} "
            f"Digest: {state.get('last_digest_path')}"
        )
    lines.append("Do not treat session digests as canonical topic knowledge until promoted into a topic wiki.")
    return "\n".join(lines)


def run_rehydrate(args: argparse.Namespace) -> int:
    root = sessions_dir(resolve_hub(args))
    context = build_rehydrate_context(root, cwd=args.cwd, session_id=args.session_id, topic=args.topic, limit=args.limit)
    if not context:
        print("No matching session digests found.")
        return 1
    print(context)
    return 0


def read_registry(hub: Path) -> dict[str, Any]:
    path = hub / "wikis.json"
    if not path.exists():
        return {}
    return read_json_object(path, "wiki registry")


def topic_path(hub: Path, slug: str) -> Path:
    registry = read_registry(hub)
    entry = registry.get("wikis", {}).get(slug) if isinstance(registry.get("wikis"), dict) else None
    if isinstance(entry, dict) and entry.get("path"):
        return resolve_registry_path(str(entry["path"]), hub)
    return hub / "topics" / slug


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")[:80] or "session"


def append_topic_log(topic_root: Path, message: str, operation: str = "session") -> None:
    log = topic_root / "log.md"
    if log.exists():
        with log.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"\n## [{today()}] {operation} | {message}\n")


def is_private_topic(slug: str) -> bool:
    return str(slug).endswith(PRIVATE_TOPIC_SUFFIX)


def check_promotion_target(slug: str, allow_public: bool, kind: str) -> bool:
    """Return True when the target is public and machine-identity rows must be dropped."""
    if is_private_topic(slug):
        return False
    if not allow_public:
        raise SystemExit(
            f"refusing to promote {kind} into public topic '{slug}': session material carries machine identity "
            f"(cwd, transcript path, git remote). Target a '{PRIVATE_TOPIC_SUFFIX}' topic or pass --allow-public "
            "to drop those rows from the promoted note."
        )
    return True


def hub_relative(path: Path, hub: Path) -> str:
    try:
        return "<HUB>/" + path.resolve().relative_to(hub.resolve()).as_posix()
    except ValueError:
        return path.name


def scrub_machine_identity(text: str, values: list[Any]) -> str:
    """Drop machine-identity table rows and the literal cwd/transcript/remote values from promoted text."""
    kept = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if cells and cells[0] in MACHINE_IDENTITY_ROWS:
                continue
        kept.append(line)
    scrubbed = "\n".join(kept)
    for value in values:
        text = str(value or "").rstrip("/\\")
        # A filesystem root (`/`, `C:\`, `C:`) or another very short value
        # would turn every path in the note into noise (#88): skip it.
        if len(text) <= MIN_SCRUB_VALUE_CHARS:
            continue
        if WINDOWS_ABSOLUTE_PATH_RE.match(text) or "\\" in text:
            # A Windows path is the same path in either case and with either
            # separator: `C:\Users\x`, `c:/users/x`, `file:///C:/Users/x`.
            parts = [re.escape(part) for part in re.split(r"[\\/]+", text)]
            scrubbed = re.sub(r"[\\/]+".join(parts), "[omitted]", scrubbed, flags=re.IGNORECASE)
        else:
            scrubbed = scrubbed.replace(text, "[omitted]")
    return scrubbed


def run_promote(args: argparse.Namespace) -> int:
    hub = resolve_hub(args)
    root = sessions_dir(hub)
    public_target = check_promotion_target(args.topic, bool(getattr(args, "allow_public", False)), "session digest")
    with SessionStore(root).transaction() as tx:
        found = find_session(root, args.session_id)
        if not found:
            raise SystemExit(f"session not found: {args.session_id}")
        digest = found.get("last_digest_path")
        if not digest:
            raise SystemExit(f"session has no digest yet: {args.session_id}")
        state, _ = tx.load_state(str(found["harness"]), str(found["native_session_id"]))
        state = state or found
        digest_file = Path(str(digest))
        digest_text = read_text(digest_file)
        fm, body = split_frontmatter(digest_text)
        topic_root = topic_path(hub, args.topic)
        notes = topic_root / "raw" / "notes"
        if not notes.exists():
            raise SystemExit(f"topic raw notes directory not found: {notes}")
        filename = f"{today()}-session-{slugify(str(state.get('harness')))}-{slugify(str(state.get('native_session_id')))}.md"
        dest = notes / filename
        if dest.exists() and not args.force:
            raise SystemExit(f"promotion note already exists: {dest}")
        summary = fm.get("summary") or digest_summary(state, "promote")
        digest_ref = hub_relative(digest_file, hub)
        if public_target:
            identity = [state.get("cwd"), state.get("transcript_path"), state.get("git_remote")]
            body = scrub_machine_identity(body, identity)
            summary = scrub_machine_identity(str(summary), identity)
        promoted = textwrap.dedent(
            f"""
            ---
            title: {yaml_string('Session Digest Promotion: ' + str(state.get('llm_wiki_session_id')))}
            source: {yaml_string(digest_ref)}
            type: notes
            ingested: {today()}
            tags: [session-digest, session-promotion, {yaml_string(args.topic)}]
            summary: {yaml_string(summary)}
            ---

            # Session Digest Promotion: {state.get('llm_wiki_session_id')}

            Source digest: `{digest_ref}`

            This note promotes a distilled session checkpoint into the `{args.topic}` topic raw layer. It intentionally promotes the digest, not the raw transcript.

            {body.strip()}
            """
        ).strip() + "\n"
        tx.write_file(dest, promoted)
        topics = set(as_list(state.get("topics")))
        topics.add(args.topic)
        state["topics"] = sorted(topics)
        promoted_to = as_list(state.get("promoted_to"))
        promoted_to.append(str(dest))
        state["promoted_to"] = sorted(set(str(item) for item in promoted_to))
        tx.stage_state(state)
        tx.request_index()
    append_topic_log(topic_root, f"promoted session digest {state.get('llm_wiki_session_id')} → raw/notes/{filename}")
    print(str(dest))
    return 0


def find_feedback_candidate(root: Path, candidate_id: str) -> dict[str, Any] | None:
    for candidate in iter_feedback_candidates(root):
        if candidate_id in {candidate.get("id"), candidate.get("text_sha256")}:
            return candidate
    return None


def feedback_candidate_matches(candidate: dict[str, Any], args: argparse.Namespace) -> bool:
    if getattr(args, "session_id", None) and args.session_id not in {
        candidate.get("llm_wiki_session_id"),
        candidate.get("native_session_id"),
    }:
        return False
    if getattr(args, "cwd", None) and candidate.get("cwd") != args.cwd:
        return False
    if getattr(args, "type", None) and candidate.get("feedback_type") != args.type:
        return False
    if getattr(args, "min_confidence", None) and not confidence_at_least(
        str(candidate.get("confidence") or "low"), args.min_confidence
    ):
        return False
    if getattr(args, "unpromoted", False) and candidate.get("promoted"):
        return False
    return True


def run_feedback_list(args: argparse.Namespace) -> int:
    root = sessions_dir(resolve_hub(args))
    candidates = [c for c in iter_feedback_candidates(root) if feedback_candidate_matches(c, args)]
    if args.json:
        print(json.dumps({"sessions_root": str(root), "feedback_candidates": candidates}, indent=2, sort_keys=True))
        return 0
    print(f"llm-wiki feedback candidates: {root / 'feedback'}")
    print("| ID | Type | Confidence | Scope | Promoted | Preview |")
    print("|---|---|---|---|---|---|")
    for candidate in candidates[: args.limit]:
        promoted = "yes" if candidate.get("promoted") else "no"
        preview = str(candidate.get("text_preview") or "").replace("|", "\\|").replace("\n", " ")
        print(
            "| {id} | {type} | {confidence} | {scope} | {promoted} | {preview} |".format(
                id=candidate.get("id"),
                type=candidate.get("feedback_type"),
                confidence=candidate.get("confidence"),
                scope=candidate.get("scope_hint"),
                promoted=promoted,
                preview=preview,
            )
        )
    return 0


def run_feedback_show(args: argparse.Namespace) -> int:
    root = sessions_dir(resolve_hub(args))
    candidate = find_feedback_candidate(root, args.candidate_id)
    if not candidate:
        raise SystemExit(f"feedback candidate not found: {args.candidate_id}")
    if args.json:
        print(json.dumps(candidate, indent=2, sort_keys=True))
        return 0
    print(f"# Feedback Candidate: {candidate.get('id')}")
    print("")
    print(markdown_table([
        ("Type", candidate.get("feedback_type")),
        ("Confidence", candidate.get("confidence")),
        ("Scope hint", candidate.get("scope_hint")),
        ("Session", candidate.get("llm_wiki_session_id")),
        ("CWD", candidate.get("cwd")),
        ("Promoted", json.dumps(candidate.get("promoted")) if candidate.get("promoted") else "no"),
    ]))
    print("")
    print("## Distilled Lesson")
    print("")
    print(candidate.get("distilled_lesson") or "")
    print("")
    print("## Redacted User Feedback Preview")
    print("")
    print(candidate.get("text_preview") or "")
    return 0


def feedback_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "session_id": args.session_id or unique_manual_id("manual-feedback"),
        "hook_event_name": "ManualFeedback",
        "cwd": args.cwd or os.getcwd(),
        "feedback_text": args.text,
    }


def run_feedback_capture(args: argparse.Namespace) -> int:
    root = command_root(args)
    config = load_config(root)
    payload = feedback_payload(args)
    event = normalize_event(args, payload, root)
    git = event_git_info(root, event)
    with SessionStore(root).transaction() as tx:
        state, _ = record_event(tx, event, config, git)
        candidate = maybe_record_feedback_candidate(tx, state, event, payload, config, force=True)
        if not candidate:
            raise SystemExit("feedback candidate was not created")
        tx.request_index()
    print(candidate["id"])
    return 0


def run_feedback_promote(args: argparse.Namespace) -> int:
    root = sessions_dir(resolve_hub(args))
    with SessionStore(root).transaction() as tx:
        return feedback_promote_locked(tx, args)


def feedback_promote_locked(tx: Transaction, args: argparse.Namespace) -> int:
    hub = resolve_hub(args)
    root = tx.root
    public_target = check_promotion_target(args.topic, bool(getattr(args, "allow_public", False)), "feedback candidate")
    candidate = find_feedback_candidate(root, args.candidate_id)
    if not candidate:
        raise SystemExit(f"feedback candidate not found: {args.candidate_id}")
    topic_root = topic_path(hub, args.topic)
    notes = topic_root / "raw" / "notes"
    if not notes.exists():
        raise SystemExit(f"topic raw notes directory not found: {notes}")
    filename = f"{today()}-feedback-{slugify(str(candidate.get('feedback_type')))}-{slugify(str(candidate.get('id')))}.md"
    dest = notes / filename
    if dest.exists() and not args.force:
        raise SystemExit(f"feedback promotion note already exists: {dest}")
    summary = candidate.get("distilled_lesson") or f"Feedback candidate {candidate.get('id')}"
    metadata_rows = [
        ('Feedback type', candidate.get('feedback_type')),
        ('Confidence', candidate.get('confidence')),
        ('Strength', candidate.get('strength')),
        ('Scope hint', candidate.get('scope_hint')),
        ('Promotion recommendation', candidate.get('promotion_recommendation')),
        ('Session', candidate.get('llm_wiki_session_id')),
        ('Harness', candidate.get('harness')),
        ('CWD', candidate.get('cwd')),
        ('Git remote', candidate.get('git_remote')),
        ('Git branch', candidate.get('git_branch')),
        ('Captured at', candidate.get('ts')),
    ]
    if public_target:
        metadata_rows = [row for row in metadata_rows if row[0] not in MACHINE_IDENTITY_ROWS]
    promoted = textwrap.dedent(
        f"""
        ---
        title: {yaml_string('Feedback Candidate Promotion: ' + str(candidate.get('id')))}
        source: {yaml_string(hub_relative(feedback_candidates_path(root), hub))}
        type: notes
        ingested: {today()}
        tags: [feedback, feedback-candidate, user-feedback, {yaml_string(str(candidate.get('feedback_type')))}, {yaml_string(args.topic)}]
        summary: {yaml_string(summary)}
        ---

        # Feedback Candidate Promotion: {candidate.get('id')}

        This note promotes a curated user-feedback candidate from `.sessions/feedback/`. It is a distilled operational lesson, not a raw transcript.

        ## Distilled Lesson

        {candidate.get('distilled_lesson')}

        ## Redacted User Feedback Preview

        > {candidate.get('text_preview')}

        ## Metadata

        {markdown_table(metadata_rows)}

        ## Usage Guidance

        - Use corrections and preferences as candidates for future `AGENTS.md`, skill, or workflow rule updates.
        - Treat approval/decision candidates as validation of the immediate prior context unless corroborated by session digests.
        - Do not infer broader factual claims from this feedback alone.
        """
    ).strip() + "\n"
    tx.write_file(dest, promoted)
    status = read_json(feedback_status_path(root), {"promoted": {}})
    if not isinstance(status, dict):
        status = {"promoted": {}}
    promoted_map = status.get("promoted") if isinstance(status.get("promoted"), dict) else {}
    promoted_map[str(candidate["id"])] = {"ts": utc_now(), "topic": args.topic, "path": str(dest)}
    status["promoted"] = promoted_map
    tx.write_document(feedback_status_path(root), status)
    tx.request_feedback_index()
    append_topic_log(topic_root, f"promoted feedback candidate {candidate.get('id')} → raw/notes/{filename}", operation="feedback")
    print(str(dest))
    return 0


def run_status(args: argparse.Namespace) -> int:
    hub = resolve_hub(args)
    root = sessions_dir(hub)
    config = load_config(root)
    states = iter_states(root) if root.exists() else []
    report = {
        "hub": str(hub),
        "sessions_root": str(root),
        "enabled": bool(config.get("enabled")),
        "mode": config.get("mode"),
        "session_count": len(states),
        "config": config,
    }
    if config.get("config_error"):
        report["config_error"] = config["config_error"]
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"llm-wiki sessions: {root}")
        print(f"enabled: {report['enabled']}")
        print(f"mode: {report['mode']}")
        print(f"sessions: {report['session_count']}")
        if report.get("config_error"):
            print(f"config error (treated as disabled): {report['config_error']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="llm-wiki-session", description="Capture and rehydrate llm-wiki session context.")
    parser.add_argument("--hub", help="Override wiki hub path (default: LLM_WIKI_HUB, then the configured hub).")
    parser.add_argument("--local", action="store_true", help="Use .wiki/.sessions in the current directory.")
    sub = parser.add_subparsers(dest="command", required=True)

    enable = sub.add_parser("enable", help="Enable or reconfigure automated session capture in HUB/.sessions/config.json.")
    enable.add_argument("--mode", choices=sorted(MODE_CONFIGS), default="balanced")
    enable.add_argument("--tool-events", type=int, help="Override automated tool-event checkpoint threshold.")
    enable.add_argument("--force", action="store_true", help="Replace existing config instead of merging.")
    enable.set_defaults(func=run_enable)

    disable = sub.add_parser("disable", help="Disable automated session capture.")
    disable.set_defaults(func=run_disable)

    hook = sub.add_parser("hook", help="Record a harness hook event from JSON on stdin.")
    hook.add_argument("--harness", help="Harness name, e.g. codex, claude, opencode, gemini.")
    hook.add_argument("--event-name", help="Override hook event name.")
    hook.add_argument("--session-id", help="Override native session id.")
    hook.add_argument("--cwd", help="Override session cwd.")
    hook.add_argument("--topic", help="Attach a topic tag to this event/state.")
    hook.add_argument("--summary", help="Manual summary to append to state.")
    hook.add_argument("--trigger", help="Override capture trigger name.")
    hook.add_argument("--if-enabled", action="store_true", help="Run only when .sessions/config.json enables capture (llm-wiki-session enable); without a config the hook exits 0 and writes nothing. A corrupt config always disables the hook.")
    hook.add_argument("--max-event-bytes", type=int, default=MAX_EVENT_PREVIEW_CHARS)
    hook.set_defaults(func=run_hook)

    capture = sub.add_parser("capture", help="Force a manual session digest checkpoint.")
    capture.add_argument("--harness", default="manual")
    capture.add_argument("--session-id", help="Native session id; defaults to manual timestamp.")
    capture.add_argument("--cwd", help="Session working directory; defaults to current directory.")
    capture.add_argument("--transcript-path", help="Transcript pointer to record without copying contents.")
    capture.add_argument("--model", help="Model/profile label to record.")
    capture.add_argument("--topic", help="Topic tag to attach to this digest.")
    capture.add_argument("--summary", help="Short manual summary for the digest.")
    capture.set_defaults(func=run_capture)

    list_cmd = sub.add_parser("list", help="List captured sessions.")
    list_cmd.add_argument("--harness", help="Filter by harness.")
    list_cmd.add_argument("--cwd", help="Filter by cwd.")
    list_cmd.add_argument("--topic", help="Filter by topic tag.")
    list_cmd.add_argument("--limit", type=int, default=20)
    list_cmd.add_argument("--json", action="store_true")
    list_cmd.set_defaults(func=run_list)

    show = sub.add_parser("show", help="Show a session digest.")
    show.add_argument("session_id", help="llm-wiki session id or native session id.")
    show.add_argument("--path-only", action="store_true")
    show.set_defaults(func=run_show)

    rehydrate = sub.add_parser("rehydrate", help="Print a compact context block from matching session digests.")
    rehydrate.add_argument("--session-id", help="llm-wiki session id or native session id.")
    rehydrate.add_argument("--cwd", help="Match sessions by cwd; defaults to current cwd.")
    rehydrate.add_argument("--topic", help="Match sessions by topic tag.")
    rehydrate.add_argument("--limit", type=int, default=3)
    rehydrate.set_defaults(func=run_rehydrate)

    promote = sub.add_parser("promote", help="Promote a session digest into a topic raw note.")
    promote.add_argument("session_id", help="llm-wiki session id or native session id.")
    promote.add_argument("--topic", required=True, help="Target topic slug; public (non -PRIVATE) slugs require --allow-public.")
    promote.add_argument("--force", action="store_true")
    promote.add_argument(
        "--allow-public",
        action="store_true",
        help="Allow a public topic target; drops cwd, transcript path, and git remote rows from the promoted note.",
    )
    promote.set_defaults(func=run_promote)

    feedback = sub.add_parser("feedback", help="List, capture, show, and promote curated user-feedback candidates.")
    feedback_sub = feedback.add_subparsers(dest="feedback_command", required=True)

    feedback_list = feedback_sub.add_parser("list", help="List feedback candidates captured from user turns.")
    feedback_list.add_argument("--session-id", help="Filter by llm-wiki session id or native session id.")
    feedback_list.add_argument("--cwd", help="Filter by cwd.")
    feedback_list.add_argument("--type", choices=["approval", "correction", "decision", "manual", "preference"], help="Filter by feedback type.")
    feedback_list.add_argument("--min-confidence", choices=["low", "medium", "high"], default="medium")
    feedback_list.add_argument("--unpromoted", action="store_true", help="Only show candidates not yet promoted.")
    feedback_list.add_argument("--limit", type=int, default=20)
    feedback_list.add_argument("--json", action="store_true")
    feedback_list.set_defaults(func=run_feedback_list)

    feedback_show = feedback_sub.add_parser("show", help="Show one feedback candidate.")
    feedback_show.add_argument("candidate_id")
    feedback_show.add_argument("--json", action="store_true")
    feedback_show.set_defaults(func=run_feedback_show)

    feedback_capture = feedback_sub.add_parser("capture", help="Manually add a feedback candidate.")
    feedback_capture.add_argument("--text", required=True, help="User feedback text to distill and store as a candidate.")
    feedback_capture.add_argument("--harness", default="manual")
    feedback_capture.add_argument("--session-id", help="Native session id; defaults to manual-feedback timestamp.")
    feedback_capture.add_argument("--cwd", help="Session working directory; defaults to current directory.")
    feedback_capture.set_defaults(func=run_feedback_capture)

    feedback_promote = feedback_sub.add_parser("promote", help="Promote a feedback candidate into a topic raw note.")
    feedback_promote.add_argument("candidate_id")
    feedback_promote.add_argument("--topic", required=True, help="Target topic slug; public (non -PRIVATE) slugs require --allow-public.")
    feedback_promote.add_argument("--force", action="store_true")
    feedback_promote.add_argument(
        "--allow-public",
        action="store_true",
        help="Allow a public topic target; drops cwd and git remote rows from the promoted note.",
    )
    feedback_promote.set_defaults(func=run_feedback_promote)

    index = sub.add_parser("index", help="Rebuild the derived indexes under .sessions/indexes (the hook no longer does this per event).")
    index.set_defaults(func=run_index)

    status = sub.add_parser("status", help="Show session capture status.")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=run_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except HookSkip:
        return 0
    except SessionLockTimeout as exc:
        warn(str(exc))
        return 1
    except BrokenPipeError:
        return 1


if __name__ == "__main__":
    sys.exit(main())
