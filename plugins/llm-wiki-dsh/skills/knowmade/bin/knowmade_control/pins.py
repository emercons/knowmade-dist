"""Engine identity and engine pins: the ``engine`` and ``pin`` namespaces.

``engine info`` reports who the running engine is (knowmade#115). ``pin``
reads and writes the engine pin a wiki or hub declares in ``config.md``:

``pin status``
    The declared pin of one wiki, or of a hub and every active topic in it,
    judged against the running engine by ``engine.contract_state``. A topic
    that declares no pin inherits the hub's ``HUB/config.md`` pin.
``pin set``
    Writes the Knowmade and/or upstream pin into ``config.md`` (the hub's by
    default), keeping the file's newline style and BOM. A tag pins the release
    version (``knowmade_version`` / ``llm_wiki_version``), the supported
    identity; a commit pins ``knowmade_checker_sha`` / ``llm_wiki_checker_sha``
    for source checkouts. Each pin holds one form: writing one removes the
    other form of the same pin.
``pin resolve``
    The commit a tag names, from the engine's git checkout or ``--repo``.
``pin verify --engine``
    The CI step: exits 2 unless the running engine is the pinned one.

The pin keys and their comparison live in ``engine``; this module only drives
them from the command line.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from . import engine as _engine
from . import frontmatter as _frontmatter
from . import fsio as _fsio
from .common import add_scope_arguments, is_under, read_registry
from .markdown import sniff_style
from .roots import (
    is_archived_registry_entry,
    is_hub,
    resolve_hub_args,
    resolve_registry_path,
    resolve_wiki_identity,
)

_REF_SHA = re.compile(r"^[0-9a-fA-F]{7,40}$")
_REF_VERSION = re.compile(r"^v?([0-9]+(?:\.[0-9]+)+)$")

# (version key, commit key) of each pin; a pin holds exactly one of the two.
_PIN_FORMS = {
    "knowmade": (_engine.KNOWMADE_VERSION_KEY, _engine.CHECKER_SHA_KEY),
    "upstream": (_engine.CHECKER_VERSION_KEY, _engine.UPSTREAM_SHA_KEY),
}

_HUB_CONFIG_BODY = (
    "\n"
    "# Hub engine pin\n"
    "\n"
    "Topics under `topics/` that declare no engine pin of their own inherit the\n"
    "pin above. Change it with `llm-wiki pin set`.\n"
)


def classify_ref(ref: str) -> tuple[str, str]:
    """``("version", "0.5.1.25.1")`` for a tag or version, ``("commit", sha)`` for a SHA."""
    text = ref.strip()
    if _REF_SHA.match(text) and "." not in text:
        return "commit", text.lower()
    match = _REF_VERSION.match(text)
    if match:
        return "version", match.group(1)
    raise SystemExit(
        f"cannot read {ref!r} as a pin: give a release tag (v0.5.1.25.1), a version "
        "(0.5.1.25.1) or a commit SHA (7-40 hex digits)"
    )


def pin_changes(knowmade: str | None, upstream: str | None) -> list[tuple[str, str | None]]:
    """``(key, value)`` edits for ``pin set``; a None value removes the key."""
    changes: list[tuple[str, str | None]] = []
    for name, ref in (("knowmade", knowmade), ("upstream", upstream)):
        if ref is None:
            continue
        kind, value = classify_ref(ref)
        version_key, commit_key = _PIN_FORMS[name]
        if kind == "version":
            changes += [(version_key, value), (commit_key, None)]
        else:
            changes += [(commit_key, f'"{value}"'), (version_key, None)]
    return changes


def _drop_key(block: str, key: str) -> str:
    wanted = re.compile(rf"^{re.escape(key)}\s*:")
    output: list[str] = []
    lines = block.splitlines()
    index = 0
    while index < len(lines):
        if wanted.match(lines[index]):
            index += 1
            while index < len(lines) and lines[index].startswith("  - "):
                index += 1
            continue
        output.append(lines[index])
        index += 1
    return "\n".join(output)


def apply_pin_changes(block: str, changes: list[tuple[str, str | None]]) -> str:
    for key, value in changes:
        block = _drop_key(block, key) if value is None else _frontmatter.set_field_in_block(block, key, value)
    return block


def write_pins(config: Path, changes: list[tuple[str, str | None]], *, create: bool = False) -> bool:
    """Apply ``changes`` to ``config``'s frontmatter; True if the file changed.

    The file keeps its newline style and BOM and everything after the
    frontmatter. With ``create``, a missing ``config.md`` is written new (in the
    newline style of the hub's ``_index.md``).
    """
    if config.is_file():
        return _frontmatter.update(config, lambda block: apply_pin_changes(block, changes))
    if not create:
        raise FileNotFoundError(config)
    newline, bom = sniff_style(config.parent / "_index.md")
    block = apply_pin_changes("", changes).strip("\n")
    _frontmatter.write_document(config, block, _HUB_CONFIG_BODY, newline=newline, bom=bom)
    return True


def _declared(wiki_root: Path, state: dict[str, Any]) -> dict[str, Any]:
    declared: dict[str, Any] = dict(_engine.declared_pins(wiki_root))
    if state.get("declared_rule_set") is not None:
        declared["knowmade_rule_set"] = state["declared_rule_set"]
    return declared


def root_status(wiki_root: Path, *, kind: str, slug: str | None = None) -> dict[str, Any]:
    """The pin status of one root: what it declares, where from, and the verdict."""
    state = _engine.contract_state(wiki_root)
    row: dict[str, Any] = {
        "root": str(wiki_root),
        "kind": kind,
        "slug": slug,
        "exists": wiki_root.is_dir(),
        "pin_source": state["pin_source"],
        "pin_config": state["pin_config"],
        "declared": _declared(wiki_root, state),
        "state": state["state"],
        "forms": state["forms"],
        "drifted": state["drifted"],
        "summary": _engine.drift_summary(state),
    }
    if "behind" in state:
        row["behind"] = state["behind"]
    note = _engine.equivalence_note(state)
    if note:
        row["note"] = note
    return row


def hub_topics(hub: Path) -> list[tuple[str, Path]]:
    """``(slug, root)`` of every active registry topic, in slug order."""
    data = read_registry(hub)
    topics: list[tuple[str, Path]] = []
    for slug, entry in sorted(data.get("wikis", {}).items()):
        if slug == "hub" or not isinstance(entry, dict) or is_archived_registry_entry(entry):
            continue
        topics.append((slug, resolve_registry_path(str(entry.get("path") or f"topics/{slug}"), hub)))
    return topics


def _identity(args: argparse.Namespace):
    return resolve_wiki_identity(
        path=getattr(args, "path", None),
        local=bool(getattr(args, "local", False)),
        wiki=getattr(args, "wiki", None),
        hub=getattr(args, "hub", None),
        private=bool(getattr(args, "private", False)),
        public=bool(getattr(args, "public", False)),
        include_archived=bool(getattr(args, "include_archived", False)),
    )


def status_payload(args: argparse.Namespace) -> dict[str, Any]:
    identity = _identity(args)
    roots: list[dict[str, Any]] = []
    if identity.kind == "hub":
        roots.append(root_status(identity.path, kind="hub"))
        if is_hub(identity.path):
            roots += [root_status(root, kind="topic", slug=slug) for slug, root in hub_topics(identity.path)]
    else:
        roots.append(root_status(identity.path, kind=identity.kind, slug=identity.slug))
    return {"schema": "knowmade/pin-status/v1", "engine": _engine.engine_info(), "roots": roots}


def _engine_line(info: dict[str, Any]) -> str:
    commit = info["commit"][:12] if info["commit"] else "unknown"
    source = f"{info['commit_source']}, informational" if info["commit_source"] else "informational"
    return (
        f"Engine: {info['version'] or 'unknown'} (tag {info['tag'] or 'unknown'}), "
        f"commit {commit} ({source}), rules/{info['rule_set']}"
    )


def _declared_text(declared: dict[str, Any]) -> str:
    pins = [f"{key} {value}" for key, value in declared.items() if key != "knowmade_rule_set"]
    return ", ".join(pins) if pins else "no pin"


def run_engine(args: argparse.Namespace) -> int:
    info = _engine.engine_info()
    if args.json:
        print(json.dumps(info, indent=2, sort_keys=True))
        return 0
    print(_engine_line(info))
    build, head = info.get("build_commit"), info.get("checkout_commit")
    if build and head and build != head:
        print(f"Build identity: commit {build} (_build.py, what a packaged copy reports)")
        print(f"Checkout HEAD: commit {head} (what this checkout reports)")
    supported = ", ".join(f"rules/{value}" for value in info["supported_rule_sets"])
    print(f"Supported rule sets: {supported}")
    if info["upstream_baseline"]:
        print(f"Upstream baseline: llm-wiki {info['upstream_baseline']}")
    print("Pin a release by its tag and version; the commit is informational.")
    return 0


def _run_status(args: argparse.Namespace) -> int:
    payload = status_payload(args)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print(_engine_line(payload["engine"]))
    for row in payload["roots"]:
        label = row["slug"] or row["kind"]
        inherited = " (inherited from the hub)" if row["pin_source"] == "hub" else ""
        print(f"{label}: {row['state']} - {_declared_text(row['declared'])}{inherited}")
        if row["summary"]:
            print(f"  {row['summary']}")
        if row.get("note"):
            print(f"  {row['note']}")
    return 0


def _run_set(args: argparse.Namespace) -> int:
    if args.knowmade is None and args.upstream is None:
        raise SystemExit("pin set needs --knowmade and/or --upstream")
    changes = pin_changes(args.knowmade, args.upstream)
    if args.path:
        root = Path(args.path).expanduser()
        if not (root / "config.md").is_file():
            raise SystemExit(f"no config.md at {root}; pin set --path writes an existing one")
        hub = None
    else:
        hub = resolve_hub_args(args)
        if not is_hub(hub):
            raise SystemExit(f"initialized hub not found: {hub}")
        root = hub
    written: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    try:
        changed = write_pins(root / "config.md", changes, create=hub is not None)
    except _frontmatter.FrontmatterError as exc:
        raise SystemExit(f"{root / 'config.md'} has no YAML frontmatter: {exc}") from exc
    written.append({"config": str(root / "config.md"), "changed": changed})
    if args.write_topics:
        if hub is None:
            raise SystemExit("--write-topics applies to a hub, not to --path")
        for slug, topic in hub_topics(hub):
            config = topic / "config.md"
            if not is_under(topic, hub / "topics"):
                skipped.append({"slug": slug, "reason": "registered outside the hub's topics/"})
            elif not config.is_file():
                skipped.append({"slug": slug, "reason": "no config.md (not checked out here?)"})
            else:
                try:
                    written.append({"config": str(config), "changed": write_pins(config, changes)})
                except _frontmatter.FrontmatterError:
                    skipped.append({"slug": slug, "reason": "config.md has no frontmatter"})
    payload = {
        "schema": "knowmade/pin-set/v1",
        "changes": {key: value for key, value in changes},
        "written": written,
        "skipped": skipped,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    for item in written:
        print(f"{'pinned' if item['changed'] else 'already pinned'}: {item['config']}")
    for item in skipped:
        print(f"skipped {item['slug']}: {item['reason']}")
    return 0


def resolve_tag(tag: str, repo: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{tag}^{{commit}}"],
            env=_fsio.git_environment(),
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    commit = result.stdout.strip()
    return commit if result.returncode == 0 and commit else None


def _run_resolve(args: argparse.Namespace) -> int:
    repo = Path(args.repo).expanduser() if args.repo else _engine.engine_checkout()
    if repo is None:
        raise SystemExit("this engine is not a git checkout; pass --repo <clone> to resolve a tag")
    commit = resolve_tag(args.tag, repo)
    if args.json:
        print(json.dumps({"tag": args.tag, "commit": commit, "repo": str(repo)}, indent=2, sort_keys=True))
    elif commit:
        print(commit)
    else:
        print(f"{args.tag} does not name a commit in {repo}")
    return 0 if commit else 2


def _run_verify(args: argparse.Namespace) -> int:
    identity = _identity(args)
    row = root_status(identity.path, kind=identity.kind, slug=identity.slug)
    pinned = any(row["forms"][form] != "undeclared" for form in ("commit", "version"))
    if not pinned:
        ok, message = False, f"{identity.path} declares no engine pin (and inherits none)"
    elif row["state"] == "match":
        ok, message = True, f"engine matches the pin of {identity.path}"
    elif row["state"] == "unknown":
        ok, message = False, "the running engine's identity is unknown, so the pin cannot be verified"
    else:
        ok, message = False, f"{identity.path}: {row['summary']}"
    if args.json:
        print(json.dumps({"ok": ok, "message": message, **row}, indent=2, sort_keys=True))
    else:
        print(message)
    return 0 if ok else 2


def run_pin(args: argparse.Namespace) -> int:
    handlers = {
        "status": _run_status,
        "set": _run_set,
        "resolve": _run_resolve,
        "verify": _run_verify,
    }
    return handlers[args.pin_command](args)


ENGINE_HELP = "Print the running engine's identity (version, tag, commit, rule sets)."
PIN_HELP = "Show, set, resolve and verify the engine pin in config.md."


def register_engine_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Register ``engine`` on a parent ``llm-wiki`` parser; dispatch through :func:`run_engine`."""
    engine = subparsers.add_parser("engine", help=ENGINE_HELP, description=ENGINE_HELP)
    engine_sub = engine.add_subparsers(dest="engine_command", required=True)
    info = engine_sub.add_parser(
        "info",
        help="Print the engine identity.",
        description=(
            "Print the running engine's version, its release tag v<version>, the commit "
            "(informational: a packaged copy carries the commit its last sync embedded), "
            "the rule sets it supports and the absorbed upstream baseline."
        ),
    )
    info.add_argument("--json", action="store_true", help="Print JSON.")
    engine.set_defaults(func=run_engine)
    return engine


def _add_scope(parser: argparse.ArgumentParser, verb: str) -> None:
    parser.add_argument("--path", help=f"{verb} the wiki root at this path.")
    add_scope_arguments(
        parser,
        local_help=f"{verb} ./.wiki in the current directory.",
        public_help="Resolve --wiki <slug> to the public sibling without -PRIVATE.",
        include_archived_help="Allow --wiki to resolve an archived topic.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON.")


def register_pin_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Register ``pin`` on a parent ``llm-wiki`` parser; dispatch through :func:`run_pin`."""
    pin = subparsers.add_parser(
        "pin",
        help=PIN_HELP,
        description=(
            f"{PIN_HELP} A hub may declare the pin once in HUB/config.md; topics that "
            "declare none inherit it."
        ),
    )
    pin_sub = pin.add_subparsers(dest="pin_command", required=True)

    status = pin_sub.add_parser(
        "status",
        help="Show the declared pin and its verdict; for a hub, also every active topic.",
    )
    _add_scope(status, "Show")

    set_parser = pin_sub.add_parser(
        "set",
        help="Write the Knowmade and/or upstream pin into config.md (the hub's by default).",
        description=(
            "Write the engine pin. A tag or version pins the release (knowmade_version / "
            "llm_wiki_version); a commit SHA pins knowmade_checker_sha / llm_wiki_checker_sha. "
            "Writing one form of a pin removes the other. The hub's config.md is created "
            "when missing; newline style and BOM are kept."
        ),
    )
    set_parser.add_argument("--knowmade", help="Knowmade release tag, version or commit SHA.")
    set_parser.add_argument("--upstream", help="Upstream llm-wiki release tag, version or commit SHA.")
    set_parser.add_argument("--hub", help="Override hub path.")
    set_parser.add_argument("--path", help="Write this wiki root's existing config.md instead of the hub's.")
    set_parser.add_argument(
        "--write-topics",
        action="store_true",
        help="Also write the pin into every active topic's config.md under the hub's topics/.",
    )
    set_parser.add_argument(
        "--require-cwd-hub",
        action="store_true",
        help="Refuse unless the selected hub is the hub enclosing the current directory.",
    )
    set_parser.add_argument("--json", action="store_true", help="Print JSON.")

    resolve = pin_sub.add_parser("resolve", help="Print the commit a tag names.")
    resolve.add_argument("tag", help="Tag to resolve, for example v0.5.1.25.1.")
    resolve.add_argument("--repo", help="Git clone to resolve in (default: the engine's own checkout).")
    resolve.add_argument("--json", action="store_true", help="Print JSON.")

    verify = pin_sub.add_parser(
        "verify",
        help="Exit 2 unless the running engine is the pinned one (the CI step).",
    )
    verify.add_argument(
        "--engine",
        action="store_true",
        required=True,
        help="Verify the running engine against the declared (or inherited) pin.",
    )
    _add_scope(verify, "Verify")

    pin.set_defaults(func=run_pin)
    return pin
