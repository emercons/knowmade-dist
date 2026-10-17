"""The ``hub`` namespace: ``hub index`` and ``hub where`` (knowmade#114).

``hub index --check`` compares the topic table and statistics of
``HUB/_index.md`` with ``wikis.json``; ``--write`` refreshes them through the
same renderer ``archive topic`` and ``archive restore`` use
(``hub.render_hub_index``), so curated sections, extra columns, the BOM and
the newline style survive. :func:`index_drift` is the read-only check lint's
``hub-index-drift`` rule calls.

``hub where`` shows which hub a command would use and why: the selected hub
and its source (``--hub``, ``LLM_WIKI_HUB``, the enclosing hub of the current
directory with ``hub_discovery: cwd``, or the configured ``hub_path``), the
hub enclosing the current directory, and the git state of each, so a sibling
checkout of the same hub is visible before anything writes to it.
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
    enclosing_hub,
    hub_config_path,
    is_hub,
    require_cwd_hub,
    resolve_hub_selection,
    same_path,
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


HUB_HELP = "Check or refresh the hub index, and show which hub is selected."


def register_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Register ``hub`` on a parent ``llm-wiki`` parser; dispatch through :func:`run`."""
    hub = subparsers.add_parser("hub", help=HUB_HELP, description=HUB_HELP)
    hub_sub = hub.add_subparsers(dest="hub_command", required=True)

    index = hub_sub.add_parser(
        "index",
        help="Compare HUB/_index.md with wikis.json (--check) or refresh it (--write).",
        description=(
            "Compare the topic table and statistics of HUB/_index.md with wikis.json. "
            "--check (the default) exits 1 when they differ; --write refreshes only the "
            "generated table and counts, keeping curated sections, extra columns, the BOM "
            "and the newline style."
        ),
    )
    mode = index.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Report drift; exit 1 when out of date (default).")
    mode.add_argument("--write", action="store_true", help="Refresh the generated table and counts.")
    index.add_argument("--hub", help="Override hub path.")
    index.add_argument(
        "--require-cwd-hub",
        action="store_true",
        help="Refuse unless the selected hub is the hub enclosing the current directory.",
    )
    index.add_argument("--json", action="store_true", help="Print JSON.")

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
