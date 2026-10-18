"""Atomic UTF-8 file I/O shared by the deterministic control plane.

Every writer that must never leave a half-written file on disk (registries,
state machines, checkpoint manifests, retract rewrites) used to carry its own
``mkstemp`` / ``fsync`` / ``os.replace`` sequence. This module is the single
copy (emercons/knowmade#67 R1.2). The single-file ``scripts/llm-wiki-session``
hook ships verbatim as a plugin hook and cannot import this module, so
``scripts/sync-session-vendor.py`` stamps the writers, ``read_text`` and the
JSON object reader into it; a change here reaches the hook through the stamper.

Guarantees of :func:`atomic_write_bytes`:

- the temporary file is created next to the target (``tempfile.mkstemp`` in
  ``path.parent``) so the final ``os.replace`` is a same-filesystem rename;
- the data is flushed and ``fsync``-ed before the rename, and the directory
  entry is synced afterwards where the platform supports it;
- the target is either the old file or the complete new file, never a mix;
- on any failure the temporary file is removed and the error propagates
  unchanged;
- permissions: with ``mode`` the result is ``chmod``-ed to it; without, the
  result carries what a plain ``open(path, "w")`` would have left, i.e. an
  existing file keeps its mode and a new file gets ``0o666`` masked by the
  process umask. (``mkstemp`` alone would leave ``0o600``.)

Text goes through :func:`atomic_write_text`, which always encodes UTF-8 and
writes ``\\n`` line endings unless told otherwise. :func:`read_text` reads
UTF-8 and tolerates a leading BOM without ever writing one back; whether an
existing file should keep its BOM is decided by the caller, not here.

JSON objects (registries, hub config, receipts, lifecycle records) are read
through :func:`read_json_object` and written through :func:`write_json_atomic`
(emercons/knowmade#91). The reader accepts a UTF-8 BOM, and every way the
file can be unusable (unreadable, permission denied, not UTF-8, not JSON, not
an object) is one :class:`JSONReadError` naming the file, never a traceback.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

# Variables git exports to hooks and `--exec` steps. Inherited by a child git,
# they make it answer for the invoking repository instead of the directory
# under ``cwd``: a hub in a temp directory then looks like it sits inside that
# repository and the private-ignore guard refuses (knowmade#135).
_GIT_ENV_KEYS = frozenset({
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
})


def git_environment() -> dict[str, str]:
    """``os.environ`` without the repository-selecting ``GIT_*`` variables.

    Every git subprocess the control plane runs against a wiki or hub passes
    this as ``env`` so the answer is about ``cwd``, whatever process (a git
    hook, ``git rebase --exec``, a CI step) launched the CLI.
    """
    return {key: value for key, value in os.environ.items() if key not in _GIT_ENV_KEYS}


# Options for every git command run inside a hub or wiki checkout. That
# checkout may be a clone whose ``.git/config`` names a program in
# ``core.fsmonitor``, which ``git status``, ``ls-files`` and ``check-ignore``
# all start; turning it off keeps a read from running repository-supplied
# commands. Pass as ``[git, *GIT_READ_OPTIONS, "status", ...]``.
GIT_READ_OPTIONS: tuple[str, ...] = ("-c", "core.fsmonitor=false")


def git_filter_environment(cwd: Path, git: str = "git") -> dict[str, str] | None:
    """:func:`git_environment` with every filter driver configured at ``cwd`` switched off.

    ``git status`` re-reads a stat-dirty file through its ``filter.<driver>``
    ``clean`` or ``process`` program, a command the checkout's own config can
    name. Listing the config names runs nothing; the returned environment adds
    ``GIT_CONFIG_COUNT`` entries (command-line scope, so they win over every
    config file) that empty each driver's commands and drop ``required``, and
    git then compares the raw bytes. The keys travel in their own variables,
    so a driver name containing ``=`` cannot split one. None when the names
    cannot be read back exactly (undecodable bytes): the caller must then not
    run the content-comparing command at all.
    """
    env = git_environment()
    try:
        result = subprocess.run(
            [git, *GIT_READ_OPTIONS, "config", "-z", "--name-only", "--get-regexp", r"^filter\."],
            cwd=str(cwd),
            env=env,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    # Exit 1 means no filter key is set; anything else is an unreadable config.
    if result.returncode not in (0, 1):
        return None
    try:
        names = result.stdout.decode("utf-8").split("\0")
    except UnicodeDecodeError:
        return None
    drivers: list[str] = []
    for name in names:
        if not name:
            continue
        driver = name[len("filter.") :].rpartition(".")[0] if name.startswith("filter.") else ""
        if not driver or "\0" in driver:
            return None
        if driver not in drivers:
            drivers.append(driver)
    try:
        count = int(env.get("GIT_CONFIG_COUNT", "0") or "0")
    except ValueError:
        return None
    for driver in drivers:
        for key, value in (("clean", ""), ("smudge", ""), ("process", ""), ("required", "false")):
            env[f"GIT_CONFIG_KEY_{count}"] = f"filter.{driver}.{key}"
            env[f"GIT_CONFIG_VALUE_{count}"] = value
            count += 1
    env["GIT_CONFIG_COUNT"] = str(count)
    return env


__all__ = [
    "JSONReadError",
    "atomic_write_bytes",
    "atomic_write_text",
    "is_permission_denied",
    "permission_denied_message",
    "read_json_object",
    "read_text",
    "write_json_atomic",
]

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


def write_json_atomic(
    path: Path,
    data: Any,
    *,
    mode: int | None = None,
    ensure_ascii: bool = False,
    sort_keys: bool = False,
    indent: int = 2,
) -> None:
    """Atomically write ``data`` as indented JSON with a trailing newline.

    Non-ASCII text is written as UTF-8 by default rather than ``\\uXXXX``
    escapes; the file never gets a BOM.
    """
    text = json.dumps(data, indent=indent, sort_keys=sort_keys, ensure_ascii=ensure_ascii) + "\n"
    atomic_write_text(path, text, mode=mode)
