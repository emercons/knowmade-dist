"""Versioned corpus rule-set contract for Knowmade.

The rule-set version is deliberately independent from the plugin/package
version. It changes only when the rules used to judge an on-disk wiki change.
The running engine supports the current set and the two preceding sets (N-2).
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from . import frontmatter as _frontmatter
from .common import add_scope_arguments
from .roots import resolve_wiki_root_args
from .stdio import configure_utf8_stdio

RULE_SET_KEY = "knowmade_rule_set"
CURRENT_RULE_SET = 3
SUPPORT_DEPTH = 2
MIN_SUPPORTED_RULE_SET = max(1, CURRENT_RULE_SET - SUPPORT_DEPTH)
SUPPORTED_RULE_SETS = tuple(range(CURRENT_RULE_SET, MIN_SUPPORTED_RULE_SET - 1, -1))
_RULE_SET_RE = re.compile(r"^(?:rules/)?([0-9]+)$")


def parse_rule_set(value: Any) -> int | None:
    """Normalize ``3`` and ``rules/3`` to an integer rule-set id."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if value is None:
        return None
    match = _RULE_SET_RE.fullmatch(str(value).strip().strip("\"'"))
    return int(match.group(1)) if match else None


def rule_set_status(declared: int | None) -> str:
    if declared is None:
        return "undeclared"
    if declared == CURRENT_RULE_SET:
        return "match"
    if declared in SUPPORTED_RULE_SETS:
        return "supported"
    if declared < MIN_SUPPORTED_RULE_SET:
        return "too-old"
    if declared > CURRENT_RULE_SET:
        return "future"
    return "unsupported"


def effective_rule_set(declared: int | None) -> int:
    """Return the rule set used to judge the corpus.

    Undeclared corpora intentionally retain legacy behavior and use the current
    set. Unsupported declarations are rejected by the engine before linting.
    """
    status = rule_set_status(declared)
    if status == "undeclared":
        return CURRENT_RULE_SET
    if status in {"match", "supported"}:
        assert declared is not None
        return declared
    raise ValueError(f"unsupported rule set declaration: {declared}")


def severity_for_rule(
    introduced_in: int,
    target_rule_set: int,
    *,
    exceptional_critical: bool = False,
) -> str | None:
    """Apply the compatibility deprecation ladder to a stricter rule.

    A normal stricter check is absent before its introduction, ``info`` in the
    introducing set, ``warning`` in the next set, and ``critical`` from the
    following set onward. Security/privacy/data-loss exceptions may explicitly
    bypass that progression.
    """
    if target_rule_set < introduced_in:
        return None
    if exceptional_critical:
        return "critical"
    age = target_rule_set - introduced_in
    if age == 0:
        return "info"
    if age == 1:
        return "warning"
    return "critical"


def rules_payload() -> dict[str, Any]:
    return {
        "schema": "knowmade/rule-sets/v1",
        "key": RULE_SET_KEY,
        "current": CURRENT_RULE_SET,
        "current_label": f"rules/{CURRENT_RULE_SET}",
        "supported": list(SUPPORTED_RULE_SETS),
        "supported_labels": [f"rules/{value}" for value in SUPPORTED_RULE_SETS],
        "minimum_supported": MIN_SUPPORTED_RULE_SET,
        "window": "N-2",
        "severity_ladder": {
            "introduced": "info",
            "next": "warning",
            "following": "critical",
        },
    }


def _config_path(wiki_root: Path) -> Path:
    return wiki_root / "config.md"


def config_frontmatter_error(wiki_root: Path) -> str | None:
    """Explain why ``wiki_root/config.md`` cannot carry a declaration, or ``None``.

    Callers that are about to mutate a tree use this as a preflight so a missing
    or frontmatter-less ``config.md`` becomes one clear message instead of a
    traceback after partial writes.
    """
    config = _config_path(wiki_root)
    if not config.is_file():
        return f"{config} does not exist or is not a file"
    try:
        _frontmatter.load(config)
    except (OSError, UnicodeDecodeError) as exc:
        return f"cannot read {config}: {exc}"
    except _frontmatter.FrontmatterError:
        return f"{config} has no YAML frontmatter block to hold {RULE_SET_KEY}"
    return None


def config_fields(wiki_root: Path) -> dict[str, Any] | None:
    """Return the frontmatter fields of ``wiki_root/config.md``, or ``None``.

    The one reader of the engine contract: the rule-set declaration and every
    ``engine`` field (version, checker commit, ...) come from this same
    frontmatter block, so a body line that looks like a declaration is never one.
    ``None`` means the file is missing, unreadable or has no usable frontmatter
    (:func:`config_frontmatter_error` says which).
    """
    try:
        fields, _, _, _ = _frontmatter.read(_config_path(wiki_root))
    except (OSError, UnicodeDecodeError, _frontmatter.FrontmatterError):
        return None
    return fields


def config_problem(wiki_root: Path) -> str | None:
    """Say why an existing ``config.md`` carries no usable frontmatter, else ``None``.

    A missing file is not a problem here (``None``): callers that care about
    absence report it themselves.
    """
    config = _config_path(wiki_root)
    if not config.is_file():
        return None
    try:
        _frontmatter.load(config)
    except (OSError, UnicodeDecodeError) as exc:
        return f"it cannot be read as UTF-8 text: {exc}"
    except _frontmatter.FrontmatterError as exc:
        if exc.kind == "unterminated":
            return "the frontmatter block opened with --- is never closed by a --- line"
        return "it does not start with a --- frontmatter fence"
    return None


def declared_rule_set(wiki_root: Path) -> tuple[int | None, bool]:
    """Return ``(parsed, present)`` for the rule-set field in config.md."""
    fields = config_fields(wiki_root)
    if fields is None or RULE_SET_KEY not in fields:
        return None, False
    return parse_rule_set(fields[RULE_SET_KEY]), True


def declare_rule_set(wiki_root: Path, rule_set: int = CURRENT_RULE_SET) -> bool:
    """Declare a supported rule set in config.md; return whether it changed."""
    if rule_set not in SUPPORTED_RULE_SETS:
        supported = ", ".join(f"rules/{value}" for value in SUPPORTED_RULE_SETS)
        raise ValueError(f"cannot declare unsupported rules/{rule_set}; supported: {supported}")
    config = _config_path(wiki_root)
    if not wiki_root.is_dir():
        raise ValueError(f"wiki root does not exist or is not a directory: {wiki_root}")
    try:
        return _frontmatter.set_field(config, RULE_SET_KEY, rule_set)
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"cannot read {config}: {exc}") from exc
    except _frontmatter.FrontmatterError as exc:
        raise ValueError(f"{config} has no YAML frontmatter") from exc


def check_payload(wiki_root: Path) -> dict[str, Any]:
    """Compare one wiki root's declaration with the running engine.

    ``status`` is ``missing`` when ``wiki_root`` is not an existing directory:
    there is nothing to judge, which is different from a wiki that simply
    declares no rule set (``undeclared``). ``path_exists`` and ``config_present``
    let a caller tell the two apart without parsing the message.
    """
    path_exists = wiki_root.is_dir()
    config_present = _config_path(wiki_root).is_file()
    declared, present = declared_rule_set(wiki_root) if path_exists else (None, False)
    if not path_exists:
        status = "missing"
    elif present and declared is None:
        status = "invalid"
    else:
        status = rule_set_status(declared)
    ok = status in {"undeclared", "match", "supported"}
    effective = CURRENT_RULE_SET if status == "undeclared" else declared if ok else None
    payload = rules_payload()
    payload.update(
        {
            "wiki_root": str(wiki_root),
            "path_exists": path_exists,
            "config_present": config_present,
            "declared": declared,
            "declaration_present": present,
            "status": status,
            "ok": ok,
            "effective": effective,
            "upgrade_available": status == "supported",
        }
    )
    return payload


def _print_report(payload: dict[str, Any]) -> None:
    supported = ", ".join(payload["supported_labels"])
    print(f"Current rule set: {payload['current_label']}")
    print(f"Supported: {supported} ({payload['window']})")


RULES_HELP = "Inspect and declare the corpus rule-set contract."
RULES_DESCRIPTION = "Inspect and declare the Knowmade corpus rule-set contract."


def _add_arguments(parser: argparse.ArgumentParser) -> None:
    # The namespace-level flag has its own dest: sharing ``json`` with the
    # subcommands let their default overwrite it, so ``rules --json check``
    # printed text (knowmade#101).
    parser.add_argument(
        "--json",
        action="store_true",
        dest="rules_json",
        help="Emit machine-readable rule-set metadata.",
    )
    sub = parser.add_subparsers(dest="rules_command")

    check = sub.add_parser(
        "check",
        help="Compare one wiki declaration with the running engine.",
        description=(
            "Compare one wiki's rule-set declaration and engine pins with the running "
            "engine. The root resolves like lint: --path, --local, --wiki <slug> through "
            "the hub registry, else ./.wiki, else the hub."
        ),
    )
    check.add_argument("--path", help="Wiki root containing config.md.")
    add_scope_arguments(
        check,
        local_help="Check ./.wiki in the current directory.",
        public_help="Resolve --wiki <slug> to the public sibling without -PRIVATE.",
        include_archived_help="Allow --wiki to resolve an archived topic.",
    )
    check.add_argument("--json", action="store_true", help="Print the comparison as JSON.")

    declare = sub.add_parser("declare", help="Write a supported rule-set declaration to config.md.")
    declare.add_argument("--path", required=True, help="Wiki root containing config.md.")
    declare.add_argument("--set", type=int, default=CURRENT_RULE_SET, dest="rule_set", help="Rule set number to declare (default: the current one).")
    declare.add_argument("--json", action="store_true", help="Print the result as JSON.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="llm-wiki rules", description=RULES_DESCRIPTION)
    _add_arguments(parser)
    return parser


def register_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Register ``rules`` on a parent ``llm-wiki`` parser; dispatch through :func:`run`."""
    parser = subparsers.add_parser("rules", help=RULES_HELP, description=RULES_DESCRIPTION)
    _add_arguments(parser)
    parser.set_defaults(func=run)
    return parser


def _engine_lines(engine: dict[str, Any]) -> list[str]:
    """The engine-contract half of ``rules check`` as text lines."""
    lines = [
        f"Engine: version {engine['version'] or 'unknown'}, commit {engine['commit'] or 'unknown'}",
        f"Engine contract: {engine['state']}",
    ]
    declared = []
    if engine["declared_version"]:
        declared.append(f"{engine['declared_version_key']} {engine['declared_version']}")
    if engine["declared"]:
        declared.append(f"knowmade_checker_sha {engine['declared']}")
    if declared:
        source = engine.get("pin_config")
        suffix = f" (from {source})" if source and engine.get("pin_source") == "hub" else ""
        lines.append(f"Declared pin: {', '.join(declared)}{suffix}")
    else:
        lines.append("Declared pin: none")
    return lines


def run(args: argparse.Namespace) -> int:
    json_output = bool(getattr(args, "json", False) or getattr(args, "rules_json", False))
    if args.rules_command is None:
        payload = rules_payload()
        if json_output:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            _print_report(payload)
        return 0

    if args.rules_command == "declare":
        wiki_root = Path(args.path).expanduser().resolve(strict=False)
        try:
            changed = declare_rule_set(wiki_root, args.rule_set)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        payload = check_payload(wiki_root)
        payload["changed"] = changed
        if json_output:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            action = "declared" if changed else "already declared"
            print(f"{action}: rules/{args.rule_set} in {wiki_root / 'config.md'}")
        return 0

    # Imported here: ``engine`` imports this module for the rule-set contract.
    from . import engine as _engine

    wiki_root = Path(resolve_wiki_root_args(args)).expanduser().resolve(strict=False)
    payload = check_payload(wiki_root)
    # The exit status stays the rule-set verdict; the engine contract is the
    # same one lint reports, so the two commands agree on one config.md.
    payload["engine"] = _engine.contract_state(wiki_root) if payload["path_exists"] else None
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        declared = payload["declared"]
        print(f"Rule-set status: {payload['status']}")
        if payload["status"] == "missing":
            print(f"Path does not exist or is not a directory: {wiki_root}")
        elif not payload["config_present"]:
            print(f"No config.md at {wiki_root}; nothing declares a rule set there.")
        print(f"Declared: {f'rules/{declared}' if declared is not None else 'none'}")
        print(f"Running: rules/{payload['current']}")
        if payload["upgrade_available"]:
            print(f"Upgrade available: llm-wiki rules declare --path {wiki_root}")
        if payload["engine"] is not None:
            for line in _engine_lines(payload["engine"]):
                print(line)
    return 0 if payload["ok"] else 2


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
