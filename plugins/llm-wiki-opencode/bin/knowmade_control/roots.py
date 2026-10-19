"""Shared root and wiki identity resolution for the deterministic control plane.

This module is intentionally runtime-neutral.  It resolves one local hub and
one wiki identity at a time; it does not implement federation or cross-hub
conflict policy.  Federation must build on this layer rather than inventing a
second path/identity model.

It is the one implementation of hub/registry resolution: ``legacy_cli`` imports
these functions rather than carrying its own copies.  A permission-denied
registry read, like any other unreadable or malformed registry, fails visibly
instead of falling back to an initialized topic path (emercons/knowmade#91).

The hub is selected in this order (knowmade#114): ``--hub``, the
``LLM_WIKI_HUB`` environment variable, the hub enclosing the current directory
when ``config.json`` opts in with ``"hub_discovery": "cwd"``, then the
configured ``hub_path`` / ``resolved_path``, then ``~/wiki``.
:func:`resolve_hub_selection` applies that order and says which source won;
:func:`resolve_hub_path` is only its configured rungs (``--hub``, then
``config.json``, then ``~/wiki``). ``scripts/llm-wiki-session`` vendors it
with :func:`hub_env_value` (stamped by ``scripts/sync-session-vendor.py``),
so the session capture honours ``--hub`` and ``LLM_WIKI_HUB`` but not cwd
discovery. Mutating commands that
take ``--require-cwd-hub`` refuse through :func:`require_cwd_hub` when the
selected hub is not the one the current directory is in.

A registry entry names its wiki root in two steps (:func:`registry_entry_path`,
knowmade#114): the gitignored per-machine overlay ``HUB/wikis.local.json``
replaces the ``path`` of an active entry with the same slug
(:func:`read_local_registry`), and a directory without ``_index.md`` whose
``.wiki/`` has one names that nested ``.wiki`` (:func:`wiki_root_of`).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .fsio import (
    PERMISSION_DENIED_ERRNOS,
    is_permission_denied,
    permission_denied_message,
    read_json_object,
)


DEFAULT_PRIVATE_SUFFIX = "-PRIVATE"
HUB_ENV = "LLM_WIKI_HUB"
LOCAL_REGISTRY_NAME = "wikis.local.json"
NESTED_WIKI_DIR = ".wiki"
HUB_DISCOVERY_KEY = "hub_discovery"
# A drive path as written on Windows: ``C:\x``, ``C:/x``, ``/C:/x``, or the Git
# Bash spelling ``/c/x`` (also ``/c`` alone). Whether it applies is decided by
# the platform, never by the text: see ``normalize_windows_absolute_path``.
WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"^(?:/?[A-Za-z]:[\\/]|/[A-Za-z](?:[\\/]|$))")
GIT_BASH_DRIVE_RE = re.compile(r"^/([A-Za-z])(?:[\\/]|$)")


@dataclass(frozen=True)
class RootIdentity:
    """Resolved local identity used by control-plane commands.

    ``kind`` is deliberately local-only: ``path``, ``local``, ``hub``, or
    ``topic``.  ``slug`` and ``archived`` describe a selected topic when one is
    known.  No global/federated identifier is implied by this structure.
    """

    path: Path
    kind: str
    hub: Path | None = None
    slug: str | None = None
    archived: bool = False


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


def wiki_root_of(path: Path) -> Path:
    """The wiki root a topic or registry path names.

    ``path`` itself when it holds ``_index.md`` or when neither it nor its
    nested ``.wiki/`` does; ``path/.wiki`` when only the nested one does (the
    hybrid ``topics/<slug>/.wiki`` layout, knowmade#114). A path that already
    ends in ``.wiki`` is returned unchanged unless it nests another one.
    """
    nested = path / NESTED_WIKI_DIR
    if not initialized_wiki_root(path) and initialized_wiki_root(nested):
        return nested
    return path


def topic_directory_of(root: Path) -> Path:
    """The directory that holds the wiki root ``root``: its parent for a nested ``.wiki``."""
    return root.parent if root.name == NESTED_WIKI_DIR else root


def is_hub(path: Path) -> bool:
    return (path / "wikis.json").exists() and (path / "topics").exists()


def enclosing_hub(start: Path) -> Path | None:
    """The nearest directory at or above ``start`` that is a hub, or None.

    ``start`` is made absolute first, so a relative path walks up from the
    current directory. A hub is ``wikis.json`` plus ``topics/``.
    """
    start = Path(os.path.abspath(start))
    for candidate in (start, *start.parents):
        if is_hub(candidate):
            return candidate
    return None


def same_path(left: Path, right: Path) -> bool:
    """Whether two paths name the same directory (resolving links and case)."""
    try:
        return os.path.samefile(left, right)
    except OSError:
        return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def is_archived_registry_entry(entry: dict[str, Any] | None) -> bool:
    """True for a registry entry marked archived or filed under ``topics/.archive``.

    The path is tested however it was spelled: ``topics\\.archive\\x``,
    ``<HUB>/topics/.archive/x``, ``./topics/.archive/x`` and an absolute
    ``.../topics/.archive/x`` are all archived. A backslash is a separator here
    on every platform, because it only ever fails safe: an entry that merely
    looks archived is held back rather than served as an active wiki.
    """
    if not entry:
        return False
    status = str(entry.get("status") or "")
    path = str(entry.get("path") or "").replace("\\", "/")
    for prefix in ("<HUB>/", "HUB/", "./"):
        if path.startswith(prefix):
            path = path[len(prefix) :]
    return status == "archived" or path.startswith("topics/.archive/") or "/topics/.archive/" in path


def scoped_wiki_slug(
    slug: str,
    *,
    private: bool = False,
    public: bool = False,
    private_suffix: str = DEFAULT_PRIVATE_SUFFIX,
) -> str:
    if private and public:
        raise SystemExit("private and public wiki scopes are mutually exclusive")
    if private and not slug.endswith(private_suffix):
        return f"{slug}{private_suffix}"
    if public and slug.endswith(private_suffix):
        return slug[: -len(private_suffix)]
    return slug


def scoped_wiki_slug_args(args: Any, slug: str) -> str:
    """``scoped_wiki_slug`` driven by an argparse namespace (``--private``/``--public``)."""
    return scoped_wiki_slug(
        slug,
        private=bool(getattr(args, "private", False)),
        public=bool(getattr(args, "public", False)),
    )


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


def read_local_registry(hub: Path) -> dict[str, str]:
    """Slug to path from the per-machine overlay ``hub/wikis.local.json``.

    The file has the shape of ``wikis.json`` (``{"wikis": {"<slug>": {"path":
    "..."}}}``); only ``path`` is read. A missing file is an empty overlay; an
    unreadable or malformed one is fatal, as an unreadable registry is (#91),
    so a machine never silently resolves a slug to the tracked path it meant
    to replace.
    """
    path = hub / LOCAL_REGISTRY_NAME
    if not path.exists():
        return {}
    data = read_json_object(path, "local wiki registry")
    wikis = data.get("wikis", {})
    if not isinstance(wikis, dict):
        raise SystemExit(f"invalid local wiki registry {path}: wikis must be an object")
    overlay: dict[str, str] = {}
    for slug, entry in wikis.items():
        raw = entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(raw, str) or not raw.strip():
            raise SystemExit(
                f"invalid local wiki registry {path}: wikis.{slug}.path must be a non-empty string"
            )
        overlay[str(slug)] = raw
    return overlay


def overlay_path(slug: str, entry: dict[str, Any] | None, overlay: dict[str, str]) -> str | None:
    """The overlay path that replaces ``entry``'s on this machine, or None.

    Only an active tracked entry is overlaid: an archived entry, and a slug the
    tracked registry does not know, keep their tracked meaning.
    """
    if not isinstance(entry, dict) or is_archived_registry_entry(entry):
        return None
    return overlay.get(slug)


def registry_entry_path(
    hub: Path,
    slug: str,
    entry: dict[str, Any] | None,
    overlay: dict[str, str] | None = None,
) -> Path:
    """The wiki root of registry entry ``slug`` on this machine.

    The overlay path when :func:`overlay_path` gives one, else the tracked
    ``path`` (default ``topics/<slug>``), resolved by
    :func:`resolve_registry_path` and then :func:`wiki_root_of`. ``overlay``
    defaults to ``hub/wikis.local.json``.
    """
    overlay = read_local_registry(hub) if overlay is None else overlay
    raw = overlay_path(slug, entry, overlay)
    if raw is None:
        raw = str((entry or {}).get("path") or f"topics/{slug}")
    return wiki_root_of(resolve_registry_path(raw, hub))




def is_portable_registry_path(raw_path: str) -> bool:
    """Whether a tracked registry path means the same on every machine.

    Judged on the text, never on the running platform, because a tracked
    ``wikis.json`` is read on all of them: a drive path, a POSIX absolute path,
    a UNC or backslash-rooted path, a leading ``~`` and a relative path that
    climbs out of the hub with ``..`` are machine-specific; ``<HUB>``, ``HUB``,
    ``.`` and paths below them are portable.
    """
    text = raw_path.strip().replace("\\", "/")
    if not text or text.startswith(("/", "~")) or WINDOWS_ABSOLUTE_PATH_RE.match(text):
        return False
    for prefix in ("<HUB>/", "HUB/"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    else:
        if text in {"<HUB>", "HUB"}:
            return True
    depth = 0
    for part in text.split("/"):
        if part == "..":
            depth -= 1
            if depth < 0:
                return False
        elif part not in {"", "."}:
            depth += 1
    return True


def _read_hub_config(config: Path) -> dict[str, Any]:
    return read_json_object(config, "hub config")


@dataclass(frozen=True)
class HubSelection:
    """The selected hub and which source chose it.

    ``source`` is ``explicit`` (``--hub``), ``env`` (``LLM_WIKI_HUB``), ``cwd``
    (the enclosing hub, with ``hub_discovery: cwd``), ``config`` (``hub_path``),
    ``resolved_path`` or ``default`` (``~/wiki``).
    """

    path: Path
    source: str


def hub_env_value(environ: dict[str, str] | None = None) -> str | None:
    """The ``LLM_WIKI_HUB`` value, stripped; ``None`` when unset or blank."""
    environ = os.environ if environ is None else environ
    return (environ.get(HUB_ENV) or "").strip() or None


def hub_config_path(home: Path | None = None) -> Path:
    return (home or home_directory()) / ".config" / "llm-wiki" / "config.json"


def federation_registry_path(home: Path | None = None) -> Path:
    """The machine-local federation registry, next to ``config.json`` (``federation.py``)."""
    return (home or home_directory()) / ".config" / "llm-wiki" / "registry.json"


def resolve_hub_selection(
    explicit_hub: str | Path | None = None,
    *,
    home: Path | None = None,
    config_path: Path | None = None,
    private_suffix: str = DEFAULT_PRIVATE_SUFFIX,
    environ: dict[str, str] | None = None,
    cwd: Path | None = None,
    discover_cwd: bool = True,
) -> HubSelection:
    if explicit_hub:
        return HubSelection(expand_leading_tilde(str(explicit_hub)), "explicit")
    from_env = hub_env_value(environ)
    if from_env:
        return HubSelection(expand_leading_tilde(from_env), "env")
    home = home or home_directory()
    config = config_path or hub_config_path(home)
    data = _read_hub_config(config) if config.exists() else {}
    if discover_cwd and data.get(HUB_DISCOVERY_KEY) == "cwd":
        found = enclosing_hub(cwd or Path.cwd())
        if found is not None:
            return HubSelection(found, "cwd")
    selected = resolve_hub_path(
        home=home, config_path=config, private_suffix=private_suffix
    )
    for source, key in (("config", "hub_path"), ("resolved_path", "resolved_path")):
        if data.get(key) and selected == expand_leading_tilde(str(data[key])):
            return HubSelection(selected, source)
    return HubSelection(selected, "default")


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


def resolve_hub_args(args: Any) -> Path:
    hub = resolve_hub_selection(getattr(args, "hub", None)).path
    if getattr(args, "require_cwd_hub", False):
        require_cwd_hub(hub)
    return hub


def require_cwd_hub(hub: Path, cwd: Path | None = None) -> None:
    """Refuse unless ``hub`` is the hub that encloses the current directory.

    The guard for mutating commands on a machine with several checkouts of one
    hub: a configured ``hub_path`` that points at a sibling checkout must not
    receive the write meant for the checkout the user is standing in.
    """
    here = cwd or Path.cwd()
    found = enclosing_hub(here)
    if found is None:
        raise SystemExit(
            f"--require-cwd-hub: {here} is not inside a hub; the selected hub is {hub}"
        )
    if not same_path(found, hub):
        raise SystemExit(
            f"--require-cwd-hub: the selected hub {hub} is not the hub enclosing the "
            f"current directory ({found}). Pass --hub {found} or change directory."
        )


def _read_registry(hub: Path) -> dict[str, Any]:
    """Read ``hub/wikis.json``. Any read or parse failure is fatal (#91).

    Only a slug that is missing from a readable registry may fall back to an
    initialized ``topics/<slug>``; a registry that cannot be read must never
    send the resolver to a root the registry may declare elsewhere.
    """
    return read_json_object(hub / "wikis.json", "wiki registry")


def resolve_wiki_identity(
    *,
    path: str | Path | None = None,
    local: bool = False,
    wiki: str | None = None,
    hub: str | Path | None = None,
    private: bool = False,
    public: bool = False,
    include_archived: bool = False,
    cwd: Path | None = None,
    home: Path | None = None,
    config_path: Path | None = None,
    private_suffix: str = DEFAULT_PRIVATE_SUFFIX,
) -> RootIdentity:
    cwd = cwd or Path.cwd()
    if path:
        return RootIdentity(expand_leading_tilde(str(path)), "path")
    if local:
        return RootIdentity(cwd / ".wiki", "local")

    hub_path = resolve_hub_selection(
        hub,
        home=home,
        config_path=config_path,
        private_suffix=private_suffix,
        cwd=cwd,
    ).path
    if wiki:
        slug = scoped_wiki_slug(
            wiki,
            private=private,
            public=public,
            private_suffix=private_suffix,
        )
        registry_path = hub_path / "wikis.json"
        fallback_topic = wiki_root_of(hub_path / "topics" / slug)
        fallback_archived = wiki_root_of(hub_path / "topics" / ".archive" / slug)
        data: dict[str, Any] | None = None
        if registry_path.exists():
            data = _read_registry(hub_path)
        else:
            if initialized_wiki_root(fallback_topic):
                return RootIdentity(fallback_topic, "topic", hub_path, slug, False)
            if initialized_wiki_root(fallback_archived):
                if include_archived:
                    return RootIdentity(fallback_archived, "topic", hub_path, slug, True)
                raise SystemExit(
                    f"wiki is archived: {slug}. Use --include-archived or restore it first."
                )
            raise SystemExit(f"wiki registry not found: {registry_path}")

        entry = data.get("wikis", {}).get(slug) if isinstance(data.get("wikis"), dict) else None
        if not isinstance(entry, dict) or not entry.get("path"):
            if initialized_wiki_root(fallback_topic):
                return RootIdentity(fallback_topic, "topic", hub_path, slug, False)
            if initialized_wiki_root(fallback_archived):
                if include_archived:
                    return RootIdentity(fallback_archived, "topic", hub_path, slug, True)
                raise SystemExit(
                    f"wiki is archived: {slug}. Use --include-archived or restore it first."
                )
            raise SystemExit(f"wiki not found in registry: {slug}")

        archived = is_archived_registry_entry(entry)
        if archived and not include_archived:
            raise SystemExit(
                f"wiki is archived: {slug}. Use --include-archived or restore it first."
            )
        selected = registry_entry_path(hub_path, slug, entry)
        if not initialized_wiki_root(selected) and initialized_wiki_root(fallback_topic):
            selected = fallback_topic
            archived = False
        if (
            not initialized_wiki_root(selected)
            and include_archived
            and initialized_wiki_root(fallback_archived)
        ):
            selected = fallback_archived
            archived = True
        return RootIdentity(selected, "topic", hub_path, slug, archived)

    local_path = cwd / ".wiki"
    if local_path.exists():
        return RootIdentity(local_path, "local")
    return RootIdentity(hub_path, "hub", hub_path)


def resolve_wiki_identity_args(args: Any) -> RootIdentity:
    """:func:`resolve_wiki_identity` over parsed scope arguments."""
    return resolve_wiki_identity(
        path=getattr(args, "path", None),
        local=bool(getattr(args, "local", False)),
        wiki=getattr(args, "wiki", None),
        hub=getattr(args, "hub", None),
        private=bool(getattr(args, "private", False)),
        public=bool(getattr(args, "public", False)),
        include_archived=bool(getattr(args, "include_archived", False)),
    )


def resolve_wiki_root_args(args: Any) -> Path:
    return resolve_wiki_identity_args(args).path


def resolve_project_wiki_roots(
    project_root: str | Path,
    wiki_root: str | Path | None = None,
) -> tuple[Path, Path]:
    project = expand_leading_tilde(str(project_root)).resolve()
    wiki = (
        expand_leading_tilde(str(wiki_root)).resolve()
        if wiki_root is not None
        else (project / ".wiki").resolve()
    )
    return project, wiki
