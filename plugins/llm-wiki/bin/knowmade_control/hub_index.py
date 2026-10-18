"""The ``hub`` namespace: ``hub index`` and ``hub where`` (knowmade#114).

``hub index --check`` compares the topic table and statistics of
``HUB/_index.md`` with ``wikis.json``; ``--write`` refreshes them through the
same renderer ``archive topic`` and ``archive restore`` use
(``hub.render_hub_index``), so curated sections, extra columns, the BOM and
the newline style survive. :func:`index_drift` is the read-only check lint's
``hub-index-drift`` rule calls. ``--stats`` (:func:`stats_payload`) counts the
registry and compares it with ``topics/``, read-only.

``hub where`` shows which hub a command would use and why: the selected hub
and its source (``--hub``, ``LLM_WIKI_HUB``, the enclosing hub of the current
directory with ``hub_discovery: cwd``, or the configured ``hub_path``), the
hub enclosing the current directory, and the git state of each, so a sibling
checkout of the same hub is visible before anything writes to it.

``hub registry check`` reports, per tracked ``wikis.json`` entry, whether its
path is portable and where it resolves on this machine through the overlay
``HUB/wikis.local.json``; ``--portable`` exits 1 on a machine-specific tracked
path or an overlay git does not ignore.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from . import fsio as _fsio
from . import hub as _hub
from .common import PRIVATE_SUFFIX, read_registry
from .markdown import BOM
from .roots import (
    HUB_DISCOVERY_KEY,
    HUB_ENV,
    LOCAL_REGISTRY_NAME,
    enclosing_hub,
    hub_config_path,
    initialized_wiki_root,
    is_archived_registry_entry,
    is_hub,
    is_portable_registry_path,
    overlay_path,
    read_local_registry,
    registry_entry_path,
    require_cwd_hub,
    resolve_hub_selection,
    resolve_registry_path,
    same_path,
    wiki_root_of,
)
from .fsio import read_json_object


def _decode(raw: bytes) -> str:
    bom = BOM.encode("utf-8")
    return (raw[len(bom) :] if raw.startswith(bom) else raw).decode("utf-8")


def index_drift(hub: Path, data: dict[str, Any] | None = None) -> list[str]:
    """How ``hub/_index.md`` differs from the registry; empty when it is current.

    Read-only. Each entry is one sentence naming what is out of date (missing
    rows, rows of archived topics, private rows that carry text, stale counts).
    The ``Last updated`` date alone is never drift: the index is compared with
    the refresh it would get on the date it already carries.
    """
    data = read_registry(hub) if data is None else data
    target = hub / "_index.md"
    if not target.is_file():
        return ["hub _index.md is missing; `llm-wiki hub index --write` creates it"]
    current_text = _decode(target.read_bytes())
    _target, current, refreshed = _hub.render_hub_index(
        hub, data, today=_hub.stats_last_updated(current_text)
    )
    if current == refreshed:
        return []
    active, archived = _hub.hub_index_entries(hub, data)
    current_rows = _hub.topic_table_rows(current_text)
    refreshed_rows = _hub.topic_table_rows(_decode(refreshed))
    listed = {_hub.topic_row_slug(row) for row in current_rows}
    problems: list[str] = []
    missing = sorted(slug for slug in active if slug not in listed)
    if missing:
        problems.append(f"active topics without a row: {', '.join(missing)}")
    stale = sorted(slug for slug in archived if slug in listed)
    if stale:
        problems.append(f"archived topics still listed: {', '.join(stale)}")
    scrubbed = sorted(
        _hub.topic_row_slug(row)
        for row in current_rows
        if _hub.topic_row_slug(row).endswith(PRIVATE_SUFFIX) and row not in refreshed_rows
    )
    if scrubbed:
        problems.append(f"private topic rows carry a title or description: {', '.join(scrubbed)}")
    if not problems:
        problems.append("the statistics or the topic table differ from the registry")
    return problems


def index_payload(hub: Path, *, write: bool) -> dict[str, Any]:
    data = _hub.ensure_hub_for_archive(hub)
    drift = index_drift(hub, data)
    changed = _hub.write_hub_index(hub, data) if write and drift else False
    return {
        "schema": "knowmade/hub-index/v1",
        "hub": str(hub),
        "mode": "write" if write else "check",
        "drift": drift,
        "current": not drift,
        "changed": changed,
    }


def stats_payload(hub: Path) -> dict[str, Any]:
    """Registry counts and the registry/``topics/`` mismatch of ``hub``; read-only.

    ``counts.private`` counts ``-PRIVATE`` slugs among the active and archived
    ones. ``unregistered`` are directories under ``topics/`` and
    ``topics/.archive/`` the registry does not name (``root`` is their wiki
    root, nested ``.wiki`` included, or None when they hold none);
    ``missing`` are entries whose path on this machine (``wikis.local.json``
    first) is not a directory. ``last_updated`` is the date in the index
    statistics, None without one.
    """
    data = _hub.ensure_hub_for_archive(hub)
    overlay = read_local_registry(hub)
    wikis = data.get("wikis", {})
    counts = {"active": 0, "archived": 0, "private": 0}
    missing: list[dict[str, str]] = []
    for slug, entry in sorted(wikis.items()):
        if slug == "hub" or not isinstance(entry, dict):
            continue
        counts["archived" if is_archived_registry_entry(entry) else "active"] += 1
        if slug.endswith(PRIVATE_SUFFIX):
            counts["private"] += 1
        if not registry_entry_path(hub, slug, entry, overlay).is_dir():
            raw = overlay_path(slug, entry, overlay) or str(entry.get("path") or f"topics/{slug}")
            missing.append({"slug": slug, "path": raw})
    unregistered: list[dict[str, Any]] = []
    topics = hub / "topics"
    for parent in (topics, topics / ".archive"):
        if not parent.is_dir():
            continue
        for topic in sorted(child for child in parent.iterdir() if child.is_dir()):
            if topic.name.startswith(".") or topic.name in wikis:
                continue
            root = wiki_root_of(topic)
            unregistered.append(
                {
                    "slug": topic.name,
                    "path": _hub.hub_relative(hub, topic),
                    "root": _hub.hub_relative(hub, root) if initialized_wiki_root(root) else None,
                }
            )
    index = hub / "_index.md"
    return {
        "schema": "knowmade/hub-stats/v1",
        "hub": str(hub),
        "counts": counts,
        "unregistered": unregistered,
        "missing": missing,
        "last_updated": _hub.stats_last_updated(_decode(index.read_bytes())) if index.is_file() else None,
    }


def _print_stats(payload: dict[str, Any]) -> None:
    counts = payload["counts"]
    print(f"Hub statistics: {payload['hub']}")
    print(f"  Active topics: {counts['active']}")
    print(f"  Archived topics: {counts['archived']}")
    print(f"  Private topics (active or archived): {counts['private']}")
    print(f"  Index last updated: {payload['last_updated'] or 'never'}")
    rows = [f"{row['slug']} ({row['root'] or 'no wiki'})" for row in payload["unregistered"]]
    print(f"  Topic directories without a registry entry: {len(rows)}" + (f": {', '.join(rows)}" if rows else ""))
    gone = [f"{row['slug']} ({row['path']})" for row in payload["missing"]]
    print(f"  Registry entries without a directory: {len(gone)}" + (f": {', '.join(gone)}" if gone else ""))


def _local_wiki_paths(data: dict[str, Any]) -> list[tuple[str, str]]:
    """``(label, path)`` of each ``local_wikis`` item: a path string or an object with ``path``."""
    items = data.get("local_wikis")
    found: list[tuple[str, str]] = []
    for index, item in enumerate(items if isinstance(items, list) else []):
        raw = item.get("path") if isinstance(item, dict) else item
        if isinstance(raw, str) and raw:
            found.append((f"local_wikis[{index}]", raw))
    return found


def registry_payload(hub: Path, *, portable: bool) -> dict[str, Any]:
    """The tracked registry against this machine's overlay; read-only.

    Every tracked path (each ``wikis`` entry and each ``local_wikis`` item) is
    judged by :func:`roots.is_portable_registry_path`. ``overlay.covers`` are
    the slugs ``wikis.local.json`` relocates; ``overlay.unused`` are overlay
    slugs without an active tracked entry, which resolution ignores.
    """
    data = _hub.ensure_hub_for_archive(hub)
    overlay = read_local_registry(hub)
    wikis = data.get("wikis", {})
    entries: list[dict[str, Any]] = []
    for slug, entry in sorted(wikis.items()):
        if not isinstance(entry, dict):
            continue
        raw = str(entry.get("path") or f"topics/{slug}")
        root = registry_entry_path(hub, slug, entry, overlay)
        entries.append(
            {
                "slug": slug,
                "path": raw,
                "portable": is_portable_registry_path(raw),
                "local_path": overlay_path(slug, entry, overlay),
                "root": str(root),
                "exists": initialized_wiki_root(root),
            }
        )
    for label, raw in _local_wiki_paths(data):
        root = wiki_root_of(resolve_registry_path(raw, hub))
        entries.append(
            {
                "slug": label,
                "path": raw,
                "portable": is_portable_registry_path(raw),
                "local_path": None,
                "root": str(root),
                "exists": initialized_wiki_root(root),
            }
        )
    covers = sorted(slug for slug in overlay if overlay_path(slug, wikis.get(slug), overlay))
    overlay_file = hub / LOCAL_REGISTRY_NAME
    ignored = None
    if overlay_file.exists() and _hub.is_inside_git_work_tree(hub):
        ignored = _hub.git_ignores_path(hub, LOCAL_REGISTRY_NAME)
    non_portable = [item["slug"] for item in entries if not item["portable"]]
    return {
        "schema": "knowmade/hub-registry-check/v1",
        "hub": str(hub),
        "mode": "portable" if portable else "report",
        "entries": entries,
        "non_portable": non_portable,
        "overlay": {
            "path": str(overlay_file),
            "exists": overlay_file.exists(),
            "covers": covers,
            "unused": sorted(slug for slug in overlay if slug not in covers),
            "ignored": ignored,
        },
        "ok": not non_portable and ignored is not False,
    }


def _print_registry(payload: dict[str, Any]) -> None:
    overlay = payload["overlay"]
    print(f"Registry: {Path(payload['hub']) / 'wikis.json'}")
    print(
        f"  {len(payload['entries'])} tracked paths, {len(payload['non_portable'])} not portable; "
        f"{LOCAL_REGISTRY_NAME} covers {len(overlay['covers'])}"
        + (f": {', '.join(overlay['covers'])}" if overlay["covers"] else "")
    )
    for item in payload["entries"]:
        if not item["portable"]:
            print(f"  - not portable: {item['slug']} -> {item['path']}")
        if item["local_path"]:
            print(f"  - {item['slug']}: this machine uses {item['local_path']}")
    if overlay["unused"]:
        print(f"  - overlay slugs without an active tracked entry: {', '.join(overlay['unused'])}")
    if overlay["ignored"] is False:
        print(f"  - {LOCAL_REGISTRY_NAME} is not ignored by git; {_hub.state_ignore_repair_hint(Path(payload['hub']))}")
    if payload["non_portable"]:
        print(
            f"  Track a <HUB>-relative path in wikis.json and put this machine's path in "
            f"{LOCAL_REGISTRY_NAME} under the same slug."
        )


def git_state(path: Path) -> dict[str, Any] | None:
    """Branch, upstream, ahead/behind and dirtiness of the subtree at ``path``.

    None when ``path`` is not inside a git work tree, or when its filter
    drivers cannot be switched off (:func:`fsio.git_filter_environment`); git
    status then never runs. Submodules are not entered. Dirtiness is judged on
    ``path``'s own subtree, so a hub nested in a larger repository is not dirty
    because of files outside it.
    """
    if not path.is_dir() or not _hub.is_inside_git_work_tree(path):
        return None
    env = _fsio.git_filter_environment(path)
    if env is None:
        return None
    try:
        result = subprocess.run(
            [
                "git",
                *_fsio.GIT_READ_OPTIONS,
                "--no-optional-locks",
                "status",
                "--porcelain=v1",
                "--branch",
                "--ignore-submodules=all",
                "--",
                ".",
            ],
            cwd=str(path),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    lines = result.stdout.splitlines()
    head = lines[0][3:] if lines and lines[0].startswith("## ") else ""
    state: dict[str, Any] = {
        "branch": None,
        "upstream": None,
        "ahead": 0,
        "behind": 0,
        "dirty": any(line and not line.startswith("## ") for line in lines),
    }
    tracking = ""
    if " [" in head:
        head, tracking = head.split(" [", 1)
    if head.startswith("No commits yet on "):
        state["branch"] = head[len("No commits yet on ") :]
    elif not head.startswith("HEAD (no branch)"):
        branch, _, upstream = head.partition("...")
        state["branch"] = branch or None
        state["upstream"] = upstream or None
    for part in tracking.rstrip("]").split(", "):
        name, _, count = part.partition(" ")
        if name in {"ahead", "behind"} and count.isdigit():
            state[name] = int(count)
    return state


def where_payload(explicit_hub: str | None = None, cwd: Path | None = None) -> dict[str, Any]:
    here = Path(cwd or Path.cwd()).absolute()
    selection = resolve_hub_selection(explicit_hub, cwd=here)
    configured = resolve_hub_selection(None, environ={}, discover_cwd=False)
    config = hub_config_path()
    discovery = None
    if config.is_file():
        discovery = read_json_object(config, "hub config").get(HUB_DISCOVERY_KEY)
    cwd_hub = enclosing_hub(here)
    agrees = None if cwd_hub is None else same_path(cwd_hub, selection.path)
    payload: dict[str, Any] = {
        "schema": "knowmade/hub-where/v1",
        "selected": {
            "path": str(selection.path),
            "source": selection.source,
            "is_hub": is_hub(selection.path),
            "git": git_state(selection.path),
        },
        "configured": {"path": str(configured.path), "source": configured.source},
        "env": {"name": HUB_ENV, "value": os.environ.get(HUB_ENV) or None},
        "hub_discovery": discovery,
        "cwd": str(here),
        "cwd_hub": None,
        "agrees": agrees,
    }
    if cwd_hub is not None:
        payload["cwd_hub"] = {
            "path": str(cwd_hub),
            "git": git_state(cwd_hub) if not agrees else payload["selected"]["git"],
        }
    return payload


def _git_text(git: dict[str, Any] | None) -> str:
    if git is None:
        return "not a git checkout"
    parts = [f"branch {git['branch'] or '(detached)'}"]
    if git["upstream"]:
        parts.append(f"ahead {git['ahead']}, behind {git['behind']} {git['upstream']}")
    parts.append("dirty" if git["dirty"] else "clean")
    return ", ".join(parts)


def run(args: argparse.Namespace) -> int:
    if args.hub_command == "registry":
        payload = registry_payload(resolve_hub_selection(args.hub).path, portable=args.portable)
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            _print_registry(payload)
        return 1 if args.portable and not payload["ok"] else 0
    if args.hub_command == "where":
        payload = where_payload(args.hub)
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0
        selected = payload["selected"]
        print(f"Selected hub: {selected['path']} (from {selected['source']})")
        if not selected["is_hub"]:
            print("  not an initialized hub (no wikis.json plus topics/)")
        print(f"  git: {_git_text(selected['git'])}")
        if payload["cwd_hub"] is None:
            print("Current directory is not inside a hub.")
        elif payload["agrees"]:
            print("Current directory is inside the selected hub.")
        else:
            print(f"Current directory is inside a DIFFERENT hub: {payload['cwd_hub']['path']}")
            print(f"  git: {_git_text(payload['cwd_hub']['git'])}")
            print(f"  Use --hub {payload['cwd_hub']['path']} or set {HUB_ENV} to write there.")
        return 0

    selection = resolve_hub_selection(args.hub)
    hub = selection.path
    if args.require_cwd_hub:
        require_cwd_hub(hub)
    if args.stats:
        stats = stats_payload(hub)
        if args.json:
            print(json.dumps(stats, indent=2, sort_keys=True))
        else:
            _print_stats(stats)
        return 0
    payload = index_payload(hub, write=args.write)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif payload["current"]:
        print(f"Hub index is current: {hub / '_index.md'}")
    else:
        verb = "Refreshed" if payload["changed"] else "Hub index is out of date"
        print(f"{verb}: {hub / '_index.md'}")
        for problem in payload["drift"]:
            print(f"  - {problem}")
    if args.write:
        return 0
    return 0 if payload["current"] else 1


HUB_HELP = "Check or refresh the hub index, check registry paths, and show which hub is selected."


def register_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Register ``hub`` on a parent ``llm-wiki`` parser; dispatch through :func:`run`."""
    hub = subparsers.add_parser("hub", help=HUB_HELP, description=HUB_HELP)
    hub_sub = hub.add_subparsers(dest="hub_command", required=True)

    index = hub_sub.add_parser(
        "index",
        help="Compare HUB/_index.md with wikis.json (--check), refresh it (--write), or count topics (--stats).",
        description=(
            "Compare the topic table and statistics of HUB/_index.md with wikis.json. "
            "--check (the default) exits 1 when they differ; --write refreshes only the "
            "generated table and counts, keeping curated sections, extra columns, the BOM "
            "and the newline style; --stats prints the registry counts and its mismatch "
            "with topics/ and writes nothing."
        ),
    )
    mode = index.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Report drift; exit 1 when out of date (default).")
    mode.add_argument("--write", action="store_true", help="Refresh the generated table and counts.")
    mode.add_argument(
        "--stats",
        action="store_true",
        help=(
            "Count active, archived and private topics, topic directories without a registry "
            "entry and entries without a directory, and show the index date; exit 0."
        ),
    )
    index.add_argument("--hub", help="Override hub path.")
    index.add_argument(
        "--require-cwd-hub",
        action="store_true",
        help="Refuse unless the selected hub is the hub enclosing the current directory.",
    )
    index.add_argument("--json", action="store_true", help="Print JSON.")

    registry = hub_sub.add_parser(
        "registry",
        help=f"Check wikis.json paths against this machine's {LOCAL_REGISTRY_NAME}.",
        description=f"Read-only checks of the hub registry and its per-machine overlay {LOCAL_REGISTRY_NAME}.",
    )
    registry_sub = registry.add_subparsers(dest="registry_command", required=True)
    registry_check = registry_sub.add_parser(
        "check",
        help="Report each tracked path, whether it is portable, and the overlay path this machine uses.",
        description=(
            f"Report each tracked wikis.json path, whether it is portable (<HUB>-relative, no drive, "
            f"absolute, ~ or escaping .. path), and the {LOCAL_REGISTRY_NAME} path this machine "
            "uses instead. --portable exits 1 on a path that is not portable or an overlay git "
            "does not ignore."
        ),
    )
    registry_check.add_argument(
        "--portable",
        action="store_true",
        help=f"Exit 1 unless every tracked path is portable and {LOCAL_REGISTRY_NAME} is ignored.",
    )
    registry_check.add_argument("--hub", help="Override hub path.")
    registry_check.add_argument("--json", action="store_true", help="Print JSON.")

    where = hub_sub.add_parser(
        "where",
        help="Show the selected hub, why it was selected, and the hub enclosing the current directory.",
        description=(
            f"Hub precedence: --hub, then {HUB_ENV}, then the hub enclosing the current "
            f'directory when config.json sets "{HUB_DISCOVERY_KEY}": "cwd", then hub_path. '
            "Shows the git branch, ahead/behind and dirty state of each hub."
        ),
    )
    where.add_argument("--hub", help="Override hub path.")
    where.add_argument("--json", action="store_true", help="Print JSON.")

    hub.set_defaults(func=run)
    return hub
