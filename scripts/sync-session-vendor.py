#!/usr/bin/env python3
"""Stamp the vendored control-plane blocks into ``scripts/llm-wiki-session``.

The session script ships as one file (a Codex hook, a Claude/OpenCode helper)
and cannot import ``knowmade_control``: it vendors, never imports. Hand-copied
blocks drifted (the hub resolver made the hook create ``~/wiki``,
emercons/knowmade#43; the secret patterns fell behind the checkpoint scanner,
#88, #94), so every vendored block is now generated from its module source and
this script is the only writer. Each block sits between its own markers::

    # --- vendored from knowmade_control.<module>: start ---
    ...generated, do not edit...
    # --- vendored from knowmade_control.<module>: end ---

``BLOCKS`` names what each block carries: the whole module body after its
imports (``redaction``), or named top-level functions, classes and constants
taken by AST in source order, each with the comment lines directly above it.
Stamping refuses a block that would need anything other than the builtins,
the session script's own standard-library imports and the other vendored
blocks, and a block name the script also defines outside the markers.

    scripts/sync-session-vendor.py            # rewrite stale blocks in place
    scripts/sync-session-vendor.py --check    # exit 1 when any block differs

Both plugin sync scripts run this before they copy the session script into
the runtime mirrors; the lane asserts ``--check`` in
``tests/test-session-capture.sh``.
"""

from __future__ import annotations

import argparse
import ast
import builtins
import sys
from dataclasses import dataclass
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent
TARGET = Path("scripts/llm-wiki-session")
PACKAGE = "knowmade_control"


@dataclass(frozen=True)
class Block:
    module: str
    # None: the whole module body after its imports. Otherwise the top-level
    # names to carry, emitted in the module's source order.
    names: tuple[str, ...] | None = None

    @property
    def source(self) -> Path:
        return Path("scripts") / PACKAGE / f"{self.module}.py"

    @property
    def start(self) -> str:
        return f"# --- vendored from {PACKAGE}.{self.module}: start ---"

    @property
    def end(self) -> str:
        return f"# --- vendored from {PACKAGE}.{self.module}: end ---"


BLOCKS: tuple[Block, ...] = (
    Block(
        "roots",
        (
            "DEFAULT_PRIVATE_SUFFIX",
            "HUB_ENV",
            "WINDOWS_ABSOLUTE_PATH_RE",
            "GIT_BASH_DRIVE_RE",
            "normalize_windows_absolute_path",
            "windows_absolute_path",
            "home_directory",
            "expand_leading_tilde",
            "initialized_wiki_root",
            "resolve_registry_path",
            "_read_hub_config",
            "hub_env_value",
            "resolve_hub_path",
        ),
    ),
    Block(
        "fsio",
        (
            "PERMISSION_DENIED_ERRNOS",
            "_REPLACE_RETRY_SECONDS",
            "_REPLACE_RETRY_INTERVAL",
            "_default_mode",
            "_replace",
            "_fsync_directory",
            "atomic_write_bytes",
            "atomic_write_text",
            "read_text",
            "is_permission_denied",
            "permission_denied_message",
            "JSONReadError",
            "read_json_object",
        ),
    ),
    Block("markdown", ("BOM", "_CLOSING_FENCE_RE", "frontmatter_bounds")),
    Block("redaction"),
)
# A whole-body block may import only what the session script already imports.
WHOLE_BODY_IMPORTS = {"re", "__future__"}


class VendorError(Exception):
    """A source or marker problem that stamping cannot repair."""


def _defined_names(node: ast.stmt) -> set[str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, ast.Assign):
        return {t.id for t in node.targets if isinstance(t, ast.Name)}
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return {node.target.id}
    return set()


def _is_assignment(node: ast.stmt) -> bool:
    return isinstance(node, (ast.Assign, ast.AnnAssign))


def _segment(lines: list[str], node: ast.stmt) -> str:
    """The source of ``node`` with its decorators and the comments right above it."""
    first = min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", []))]) - 1
    while first > 0 and lines[first - 1].lstrip().startswith("#"):
        first -= 1
    return "\n".join(lines[first : node.end_lineno])


def _free_names(nodes: list[ast.stmt]) -> set[str]:
    """Names the nodes read that none of them binds (a scope-blind approximation)."""
    loaded: set[str] = set()
    bound: set[str] = set()
    for node in nodes:
        bound |= _defined_names(node)
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name):
                (loaded if isinstance(sub.ctx, ast.Load) else bound).add(sub.id)
            elif isinstance(sub, ast.arg):
                bound.add(sub.arg)
            elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.add(sub.name)
            elif isinstance(sub, ast.ExceptHandler) and sub.name:
                bound.add(sub.name)
    return loaded - bound


def render(root: Path, block: Block) -> tuple[str, set[str], set[str]]:
    """``(text, defined, free)`` for ``block`` from its module source."""
    text = (root / block.source).read_text(encoding="utf-8")
    tree = ast.parse(text)
    lines = text.splitlines()
    if block.names is None:
        last_import_line = 0
        body_nodes: list[ast.stmt] = []
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = (
                    {alias.name for alias in node.names}
                    if isinstance(node, ast.Import)
                    else {node.module or ""}
                )
                if getattr(node, "level", 0) or not names <= WHOLE_BODY_IMPORTS:
                    raise VendorError(
                        f"{block.source}: may import only {sorted(WHOLE_BODY_IMPORTS)}; found {sorted(names)}"
                    )
                last_import_line = max(last_import_line, node.end_lineno or node.lineno)
            elif not (
                node is tree.body[0]
                and isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
            ):
                body_nodes.append(node)
        body = "\n".join(lines[last_import_line:]).strip("\n")
        defined = set().union(*(_defined_names(n) for n in body_nodes))
        return body, defined, _free_names(body_nodes)
    wanted = set(block.names)
    picked = [node for node in tree.body if _defined_names(node) & wanted]
    found = set().union(*(_defined_names(n) for n in picked)) if picked else set()
    missing = wanted - found
    if missing:
        raise VendorError(f"{block.source}: no top-level definition of {sorted(missing)}")
    extra = found - wanted
    if extra:
        raise VendorError(f"{block.source}: a picked statement also binds {sorted(extra)}")
    parts: list[str] = []
    previous: ast.stmt | None = None
    for node in picked:
        if previous is not None:
            parts.append("\n" if _is_assignment(previous) and _is_assignment(node) else "\n\n\n")
        parts.append(_segment(lines, node))
        previous = node
    return "".join(parts), found, _free_names(picked)


def _target_imports(tree: ast.Module) -> set[str]:
    """Names the session script's own top-level imports bind; refuses the control plane."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == PACKAGE:
                    raise VendorError(f"{TARGET}: must not import {PACKAGE}; vendor it instead")
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level or (node.module or "").split(".")[0] == PACKAGE:
                raise VendorError(f"{TARGET}: must not import {PACKAGE}; vendor it instead")
            names.update(alias.asname or alias.name for alias in node.names)
    return names


def _split(text: str, block: Block) -> tuple[str, str, str]:
    if text.count(block.start) != 1 or text.count(block.end) != 1:
        raise VendorError(f"{TARGET}: expected exactly one marker pair for {PACKAGE}.{block.module}")
    head, rest = text.split(block.start, 1)
    if block.end not in rest:
        raise VendorError(f"{TARGET}: the {block.module} end marker precedes its start marker")
    old, tail = rest.split(block.end, 1)
    return head, old, tail


def stamped(root: Path, current: str) -> str:
    rendered = [render(root, block) for block in BLOCKS]
    vendored = set().union(*(defined for _, defined, _ in rendered))
    outside = current
    for block in BLOCKS:
        head, _old, tail = _split(outside, block)
        outside = head + tail
    imports = _target_imports(ast.parse(outside))
    own = set().union(*(_defined_names(n) for n in ast.parse(outside).body))
    clash = vendored & own
    if clash:
        raise VendorError(f"{TARGET}: defines {sorted(clash)} outside the vendored blocks as well")
    allowed = vendored | imports | set(dir(builtins))
    result = current
    for block, (body, _defined, free) in zip(BLOCKS, rendered):
        unknown = free - allowed
        if unknown:
            raise VendorError(
                f"{block.source}: the vendored block would need {sorted(unknown)}, which the "
                "standard-library-only session script does not have"
            )
        for marker in (block.start, block.end):
            if marker in body:
                raise VendorError(f"{block.source}: the module must not contain the session markers")
        head, _old, tail = _split(result, block)
        note = f"# Generated from {block.source.as_posix()} by scripts/sync-session-vendor.py; do not edit."
        result = f"{head}{block.start}\n{note}\n\n{body}\n{block.end}{tail}"
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="Fail when a stamped block is stale.")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    target = args.root / TARGET
    try:
        current = target.read_text(encoding="utf-8")
        expected = stamped(args.root, current)
    except (OSError, SyntaxError, VendorError) as exc:
        print(f"sync-session-vendor: {exc}", file=sys.stderr)
        return 1
    if current == expected:
        return 0
    if args.check:
        stale = [
            block.module
            for block in BLOCKS
            if _split(current, block)[1] != _split(expected, block)[1]
        ]
        print(
            f"sync-session-vendor: {TARGET} is stale against knowmade_control "
            f"({', '.join(stale)}); run scripts/sync-session-vendor.py (or either plugin sync script)",
            file=sys.stderr,
        )
        return 1
    target.write_bytes(expected.encode("utf-8"))
    print(f"Stamped {', '.join(block.module for block in BLOCKS)} into {TARGET.as_posix()}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
