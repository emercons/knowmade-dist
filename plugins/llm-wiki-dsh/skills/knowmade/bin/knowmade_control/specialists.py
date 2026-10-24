"""The ``specialist`` namespace: personal, instruction-only specialist skills.

Agent Skills-compatible specialist packages under ``HUB/.skills``, their
registry and index, validation, and the explicit per-topic allowlists.
``check_specialist_library`` (the lint hook over this library) lives with the
other ``check_*`` functions in ``lint``. Moved verbatim from ``legacy_cli``
(emercons/knowmade#67 R1.4, ``KM-CLI-001``); the compatibility module
re-exports every public name.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from . import fsio as _fsio
from .common import (
    SPECIALIST_NAME_RE,
    active_topic_path,
    file_sha256,
    is_initialized_topic,
    markdown_link_destination,
    parse_date,
    read_registry,
    strip_matching_quotes,
    table_cell,
    topic_entry,
    write_private_json,
)
from .frontmatter import field_value
from .hub import ensure_state_ignore_rules, is_inside_git_work_tree
from .markdown import (
    append_log,
    split_markdown_frontmatter,
    today_date,
    write_text_preserving,
    yaml_quote,
)
from .roots import (
    is_archived_registry_entry,
    is_hub,
    resolve_hub_args as resolve_hub,
)


SPECIALIST_REGISTRY_SCHEMA = 1
SPECIALIST_RISK_TIERS = {"low", "medium", "high"}
SPECIALIST_REQUIRED_SECTIONS = [
    "Mandate",
    "Non-claims",
    "Use when",
    "Do not use when",
    "Required inputs",
    "Method",
    "Output contract",
    "Stop and escalation rules",
    "Tool posture",
    "Evaluation cases",
    "Provenance and maintenance",
]


def specialist_library_path(hub: Path) -> Path:
    return hub / ".skills"


def specialist_registry_path(hub: Path) -> Path:
    return specialist_library_path(hub) / "registry.json"


def empty_specialist_registry() -> dict[str, Any]:
    return {"schema_version": SPECIALIST_REGISTRY_SCHEMA, "topics": {}}


def require_initialized_hub(hub: Path) -> dict[str, Any]:
    if not is_hub(hub) or not (hub / "_index.md").exists() or not (hub / "log.md").exists():
        raise SystemExit(f"initialized hub not found: {hub}")
    return read_registry(hub)


def read_specialist_registry(hub: Path) -> dict[str, Any]:
    path = specialist_registry_path(hub)
    if not path.exists():
        return empty_specialist_registry()
    if path.is_symlink() or not path.is_file():
        raise SystemExit(f"specialist registry must be a regular file: {path}")
    data = _fsio.read_json_object(path, "specialist registry")
    if data.get("schema_version") != SPECIALIST_REGISTRY_SCHEMA:
        raise SystemExit(
            f"unsupported specialist registry schema in {path}: "
            f"expected {SPECIALIST_REGISTRY_SCHEMA}"
        )
    if not isinstance(data.get("topics"), dict):
        raise SystemExit(f"invalid specialist registry {path}: topics must be an object")
    return data


def write_specialist_registry(hub: Path, data: dict[str, Any]) -> None:
    path = specialist_registry_path(hub)
    write_private_json(path, data)


def specialist_metadata_value(frontmatter: str, field: str) -> str | None:
    metadata = re.search(r"(?m)^metadata\s*:\s*$", frontmatter)
    if not metadata:
        return None
    remainder = frontmatter[metadata.end() :]
    for line in remainder.splitlines():
        if line and not line.startswith((" ", "\t")):
            break
        match = re.match(rf"\s+{re.escape(field)}\s*:\s*(.+?)\s*$", line)
        if match:
            return strip_matching_quotes(match.group(1).strip())
    return None


def read_specialist_record(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    split = split_markdown_frontmatter(text)
    frontmatter, body = split if split is not None else ("", text)
    name = strip_matching_quotes(field_value(frontmatter, "name", folded=True) or "") or path.parent.name
    description = strip_matching_quotes(field_value(frontmatter, "description", folded=True) or "")
    version = specialist_metadata_value(frontmatter, "version") or ""
    risk_tier = specialist_metadata_value(frontmatter, "risk-tier") or ""
    reviewed = specialist_metadata_value(frontmatter, "reviewed") or ""
    return {
        "name": name,
        "description": description,
        "version": version,
        "risk_tier": risk_tier,
        "reviewed": reviewed,
        "path": str(path),
        "frontmatter": frontmatter,
        "body": body.lstrip("\n"),
        "sha256": file_sha256(path),
    }


def discover_specialists(hub: Path) -> list[dict[str, Any]]:
    library = specialist_library_path(hub)
    if not library.exists():
        return []
    records: list[dict[str, Any]] = []
    for child in sorted(library.iterdir()):
        skill = child / "SKILL.md"
        if (
            child.is_dir()
            and not child.is_symlink()
            and skill.is_file()
            and not skill.is_symlink()
        ):
            records.append(read_specialist_record(skill))
    return records


def enabled_specialist_topics(registry: dict[str, Any], name: str) -> list[str]:
    return sorted(
        topic
        for topic, names in registry.get("topics", {}).items()
        if isinstance(names, list) and name in names
    )


def specialist_rows(hub: Path, registry: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    library = specialist_library_path(hub)
    for record in discover_specialists(hub):
        rows.append(
            {
                "name": record["name"],
                "description": record["description"],
                "version": record["version"],
                "risk_tier": record["risk_tier"],
                "reviewed": record["reviewed"],
                "enabled_topics": enabled_specialist_topics(registry, record["name"]),
                "path": str(Path(record["path"]).relative_to(library)),
                "sha256": record["sha256"],
            }
        )
    return rows


def write_specialist_index(hub: Path, registry: dict[str, Any] | None = None) -> None:
    registry = registry or read_specialist_registry(hub)
    library = specialist_library_path(hub)
    library.mkdir(parents=True, exist_ok=True)
    rows = specialist_rows(hub, registry)
    table_rows = [
        "| Specialist | Description | Version | Risk | Enabled topics | Reviewed |",
        "|------------|-------------|---------|------|----------------|----------|",
    ]
    for row in rows:
        topics = ", ".join(row["enabled_topics"]) or "—"
        table_rows.append(
            f"| [{table_cell(row['name'])}]({markdown_link_destination(row['path'])}) "
            f"| {table_cell(row['description'])} | {table_cell(row['version'] or '—')} "
            f"| {table_cell(row['risk_tier'] or '—')} | {table_cell(topics)} "
            f"| {table_cell(row['reviewed'] or '—')} |"
        )
    text = "\n".join(
        [
            "# Specialist Skills Index",
            "",
            "> Personal, instruction-only specialist methods shared across topic wikis.",
            "",
            f"Last updated: {today_date().isoformat()}",
            "",
            "## Contents",
            "",
            *table_rows,
            "",
            "## Safety boundary",
            "",
            "- Specialists are methods and review protocols, not human credentials.",
            "- Topic use is explicit through `registry.json`; no specialist is enabled globally.",
            "- Packages may contain only `SKILL.md` and Markdown under `references/`.",
            "- Loading a specialist never expands the orchestrator's tools or write authority.",
            "- Do not store credentials, case files, health records, customer data, or source corpora here.",
            "",
        ]
    )
    index = library / "_index.md"
    if index.is_symlink():
        raise SystemExit(f"specialist index must not be a symlink: {index}")
    write_text_preserving(index, text)


def initialize_specialist_library(hub: Path) -> bool:
    require_initialized_hub(hub)
    library = specialist_library_path(hub)
    if library.exists() and (library.is_symlink() or not library.is_dir()):
        raise SystemExit(f"specialist library must be a real directory: {library}")
    created = not library.exists()
    library.mkdir(parents=True, exist_ok=True)
    registry_path = specialist_registry_path(hub)
    if not registry_path.exists():
        write_specialist_registry(hub, empty_specialist_registry())
        created = True
    registry = read_specialist_registry(hub)
    if not (library / "_index.md").exists():
        created = True
    write_specialist_index(hub, registry)
    return created


def validate_specialist_name(name: str) -> None:
    if len(name) > 64 or not SPECIALIST_NAME_RE.fullmatch(name):
        raise SystemExit(
            "specialist name must be 1-64 lowercase letters, digits, and single hyphens"
        )


def specialist_scaffold(name: str, description: str, risk_tier: str) -> str:
    today = today_date().isoformat()
    return f'''---
name: {name}
description: {yaml_quote(description)}
metadata:
  llm-wiki-kind: specialist
  version: "0.1.0"
  risk-tier: {risk_tier}
  reviewed: "{today}"
---

# Mandate

Define the bounded review or evidence method this specialist applies. Do not
claim a human credential, license, office, degree, or professional authority.

# Non-claims

- This skill is an AI analysis protocol, not a credentialed professional.
- It does not provide professional sign-off or expand the caller's authority.

# Use when

- List concrete task features that should select this specialist.

# Do not use when

- List nearby tasks that need another method or no specialist.

# Required inputs

- Exact question and intended decision or use.
- Bounded evidence packet, source dates, and known missing information.
- Jurisdiction and decision stakes when they materially affect the answer.

# Method

1. Define the ordered analytical procedure.
2. State the domain-specific evidence hierarchy.
3. Test the strongest alternative explanation or downside case.
4. Identify what evidence would change the conclusion.

# Output contract

Return findings, cited evidence, assumptions, uncertainties, counterarguments,
confidence, and recommended next actions. Cite every material factual claim.

# Stop and escalation rules

- Stop when required inputs or current authoritative sources are missing.
- Name the qualified human review required for high-stakes decisions.

# Tool posture

Read-only by default. This skill never grants tools, filesystem access, network
access, subagents, or permission to mutate the wiki or an external system.

# Evaluation cases

- Positive selection case.
- Nearby negative selection case.
- Stale or contradictory evidence case.
- Insufficient-data and escalation case.

# Provenance and maintenance

- Author/reviewer: TODO
- Method sources: TODO
- Review cadence: TODO
- Change notes: 0.1.0 initial scaffold
'''


def validate_specialist_package(
    hub: Path, directory: Path, expected_name: str | None = None
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []

    def add(message: str, path: Path = directory) -> None:
        issues.append({"severity": "critical", "message": message, "path": str(path)})

    if directory.is_symlink():
        add("Specialist package must not be a symlink.")
        return issues
    if not directory.is_dir():
        add("Specialist package is not a directory.")
        return issues
    name = expected_name or directory.name
    if len(name) > 64 or not SPECIALIST_NAME_RE.fullmatch(name):
        add("Specialist directory name must be 1-64 lowercase letters, digits, and single hyphens.")
    allowed = {"SKILL.md", "references"}
    for child in sorted(directory.iterdir()):
        if child.name not in allowed:
            add("Instruction-only specialist packages may contain only SKILL.md and references/.", child)
        if child.is_symlink():
            add("Specialist package files and directories must not be symlinks.", child)
    references = directory / "references"
    if references.exists():
        if not references.is_dir() or references.is_symlink():
            add("references must be a real directory.", references)
        else:
            for item in sorted(references.rglob("*")):
                if item.is_symlink():
                    add("Specialist references must not be symlinks.", item)
                elif item.is_file() and item.suffix.lower() != ".md":
                    add("Specialist references must be Markdown files.", item)
                elif item.is_file() and item.stat().st_mode & 0o111:
                    add("Specialist references must not be executable.", item)
    skill = directory / "SKILL.md"
    if not skill.is_file() or skill.is_symlink():
        add("Required SKILL.md is missing or is a symlink.", skill)
        return issues
    if skill.stat().st_mode & 0o111:
        add("SKILL.md must not be executable.", skill)
    try:
        text = skill.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        add(f"SKILL.md must be readable UTF-8 Markdown: {exc}", skill)
        return issues
    split = split_markdown_frontmatter(text)
    if split is None:
        add("SKILL.md requires YAML frontmatter.", skill)
        return issues
    frontmatter, body = split
    declared_name = strip_matching_quotes(field_value(frontmatter, "name", folded=True) or "")
    description = strip_matching_quotes(field_value(frontmatter, "description", folded=True) or "")
    if declared_name != name:
        add(f"SKILL.md name must match its directory: expected {name!r}.", skill)
    if not description or len(description) > 1024:
        add("SKILL.md description must contain 1-1024 characters.", skill)
    if specialist_metadata_value(frontmatter, "llm-wiki-kind") != "specialist":
        add("metadata.llm-wiki-kind must be specialist.", skill)
    version = specialist_metadata_value(frontmatter, "version")
    if not version or not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version):
        add("metadata.version must be a semantic version such as 0.1.0.", skill)
    risk_tier = specialist_metadata_value(frontmatter, "risk-tier")
    if risk_tier not in SPECIALIST_RISK_TIERS:
        add("metadata.risk-tier must be low, medium, or high.", skill)
    reviewed = specialist_metadata_value(frontmatter, "reviewed")
    if not reviewed or parse_date(reviewed) is None:
        add("metadata.reviewed must be an ISO date.", skill)
    if re.search(r"(?m)^allowed-tools\s*:", frontmatter):
        add("allowed-tools is not permitted; the llm-wiki orchestrator owns tool authority.", skill)
    headings = set(re.findall(r"(?m)^#\s+(.+?)\s*$", body))
    for section in SPECIALIST_REQUIRED_SECTIONS:
        if section not in headings:
            add(f"Required section is missing: # {section}.", skill)
    return issues


def collect_specialist_issues(hub: Path, name: str | None = None) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    library = specialist_library_path(hub)

    def add(message: str, path: Path) -> None:
        issues.append({"severity": "critical", "message": message, "path": str(path)})

    if not library.exists():
        if name:
            add("Specialist library is not initialized.", library)
        return issues
    if library.is_symlink() or not library.is_dir():
        add(".skills must be a real directory.", library)
        return issues
    if not (library / "_index.md").is_file():
        add("Specialist library _index.md is missing.", library / "_index.md")
    allowed_root_files = {"_index.md", "registry.json"}
    packages: dict[str, Path] = {}
    for child in sorted(library.iterdir()):
        if child.name in allowed_root_files:
            if child.is_symlink() or not child.is_file():
                add("Specialist library control files must be regular files.", child)
            continue
        if not child.is_dir() or child.is_symlink():
            add("Unexpected file in specialist library.", child)
            continue
        packages[child.name] = child
    if name:
        validate_specialist_name(name)
        package = packages.get(name)
        if package is None:
            add(f"Specialist not found: {name}.", library / name)
        else:
            issues.extend(validate_specialist_package(hub, package, name))
    else:
        for package_name, package in packages.items():
            issues.extend(validate_specialist_package(hub, package, package_name))
    registry_path = specialist_registry_path(hub)
    if not registry_path.is_file() or registry_path.is_symlink():
        add("Specialist registry.json is missing or is not a regular file.", registry_path)
        return issues
    try:
        registry = _fsio.read_json_object(registry_path, "specialist registry")
    except _fsio.JSONReadError as exc:
        add(f"Specialist registry.json is invalid: {exc}", registry_path)
        return issues
    if registry.get("schema_version") != SPECIALIST_REGISTRY_SCHEMA:
        add(f"Specialist registry schema_version must be {SPECIALIST_REGISTRY_SCHEMA}.", registry_path)
        return issues
    topics = registry.get("topics")
    if not isinstance(topics, dict):
        add("Specialist registry topics must be an object.", registry_path)
        return issues
    try:
        wiki_registry = read_registry(hub)
    except SystemExit as exc:
        add(str(exc), hub / "wikis.json")
        return issues
    active_topics = {
        slug
        for slug, entry in wiki_registry.get("wikis", {}).items()
        if slug != "hub" and isinstance(entry, dict) and not is_archived_registry_entry(entry)
    }
    for topic, names in sorted(topics.items()):
        if topic not in active_topics:
            add(f"Specialists are enabled for unknown or archived topic: {topic}.", registry_path)
        if not isinstance(names, list) or any(not isinstance(item, str) for item in names):
            add(f"Specialist allowlist for {topic} must be an array of names.", registry_path)
            continue
        if len(names) != len(set(names)):
            add(f"Specialist allowlist for {topic} contains duplicates.", registry_path)
        for specialist_name in names:
            if specialist_name not in packages:
                add(f"Topic {topic} enables missing specialist: {specialist_name}.", registry_path)
    return issues


def specialist_init(args: argparse.Namespace) -> int:
    hub = resolve_hub(args)
    created = initialize_specialist_library(hub)
    if created:
        append_log(hub, "specialist", "initialized personal specialist skill library")
    # The registry names private topics; a hub in a git work tree must not
    # commit it, nor the rest of the machine state (knowmade#98).
    appended = ensure_state_ignore_rules(hub) if is_inside_git_work_tree(hub) else []
    report = {
        "status": "created" if created else "already-initialized",
        "hub": str(hub),
        "library": specialist_library_path(hub).as_posix(),
        "gitignore_appended": appended,
    }
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        action = "Initialized" if created else "Already initialized"
        print(f"{action}: {specialist_library_path(hub)}")
        if appended:
            print(f"Appended machine-state ignore rules to {hub / '.gitignore'}: {', '.join(appended)}")
    return 0


def specialist_create(args: argparse.Namespace) -> int:
    name = args.name
    validate_specialist_name(name)
    description = re.sub(r"\s+", " ", args.description).strip()
    if not description or len(description) > 1024:
        raise SystemExit("specialist description must contain 1-1024 characters")
    hub = resolve_hub(args)
    initialize_specialist_library(hub)
    directory = specialist_library_path(hub) / name
    if directory.exists():
        raise SystemExit(f"specialist already exists: {name}")
    directory.mkdir()
    skill = directory / "SKILL.md"
    skill.write_bytes(specialist_scaffold(name, description, args.risk_tier).encode("utf-8"))
    registry = read_specialist_registry(hub)
    write_specialist_index(hub, registry)
    append_log(hub, "specialist", f"created personal specialist {name} v0.1.0")
    report = {
        "status": "created",
        "name": name,
        "path": str(skill),
        "risk_tier": args.risk_tier,
        "version": "0.1.0",
    }
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"Created specialist: {skill}")
        print("Next: replace scaffold TODOs, add evaluation cases, then run specialist validate.")
    return 0


def specialist_refresh(args: argparse.Namespace) -> int:
    hub = resolve_hub(args)
    initialize_specialist_library(hub)
    registry = read_specialist_registry(hub)
    write_specialist_index(hub, registry)
    append_log(hub, "specialist", "refreshed personal specialist skill index")
    report = {
        "status": "refreshed",
        "index": (specialist_library_path(hub) / "_index.md").as_posix(),
        "specialists": len(discover_specialists(hub)),
    }
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            f"Refreshed {report['index']} ({report['specialists']} specialists)."
        )
    return 0


def active_topic_entry(hub: Path, topic: str) -> tuple[dict[str, Any], Path]:
    registry = require_initialized_hub(hub)
    entry = topic_entry(registry, topic)
    if entry is None or is_archived_registry_entry(entry):
        raise SystemExit(f"active topic not found: {topic}")
    path = active_topic_path(hub, topic, entry)
    if not is_initialized_topic(path):
        raise SystemExit(f"active topic path is not initialized: {path}")
    return entry, path


def specialist_list(args: argparse.Namespace) -> int:
    hub = resolve_hub(args)
    require_initialized_hub(hub)
    if args.wiki:
        issues = collect_specialist_issues(hub)
        if issues:
            raise SystemExit(
                "specialist library is invalid; run specialist validate before topic selection"
            )
    registry = read_specialist_registry(hub)
    rows = specialist_rows(hub, registry)
    if args.wiki:
        active_topic_entry(hub, args.wiki)
        enabled = set(registry.get("topics", {}).get(args.wiki, []))
        rows = [row for row in rows if row["name"] in enabled]
    report = {"hub": str(hub), "wiki": args.wiki, "specialists": rows}
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    label = f" enabled for {args.wiki}" if args.wiki else ""
    print(f"Specialists{label}: {len(rows)}")
    for row in rows:
        topics = ", ".join(row["enabled_topics"]) or "not enabled"
        print(
            f"- {row['name']} v{row['version'] or '?'} [{row['risk_tier'] or '?'}] "
            f"— {row['description']} ({topics})"
        )
    return 0


def specialist_show(args: argparse.Namespace) -> int:
    validate_specialist_name(args.name)
    hub = resolve_hub(args)
    require_initialized_hub(hub)
    path = specialist_library_path(hub) / args.name / "SKILL.md"
    if not path.is_file() or path.is_symlink():
        raise SystemExit(f"specialist not found: {args.name}")
    record = read_specialist_record(path)
    registry = read_specialist_registry(hub)
    record["enabled_topics"] = enabled_specialist_topics(registry, record["name"])
    if args.json:
        print(json.dumps(record, indent=2, sort_keys=True))
    else:
        print(path.read_text(encoding="utf-8"), end="")
    return 0


def specialist_validate(args: argparse.Namespace) -> int:
    hub = resolve_hub(args)
    require_initialized_hub(hub)
    issues = collect_specialist_issues(hub, args.name)
    report = {
        "status": "pass" if not issues else "fail",
        "hub": str(hub),
        "specialist": args.name,
        "issues": issues,
    }
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        target = args.name or "library"
        print(f"llm-wiki specialist validate: {target}")
        if issues:
            for issue in issues:
                print(f"- Critical: {issue['message']} ({issue['path']})")
            print(f"Result: FAIL ({len(issues)} findings)")
        else:
            print("Findings: none")
            print("Result: PASS")
    return 1 if issues else 0


def set_specialist_enabled(args: argparse.Namespace, enabled: bool) -> int:
    validate_specialist_name(args.name)
    hub = resolve_hub(args)
    _, topic_path = active_topic_entry(hub, args.wiki)
    if enabled:
        initialize_specialist_library(hub)
        package = specialist_library_path(hub) / args.name
        issues = validate_specialist_package(hub, package, args.name)
        if issues:
            raise SystemExit(
                f"specialist {args.name} is missing or invalid; run specialist validate {args.name}"
            )
    registry = read_specialist_registry(hub)
    topics = registry.setdefault("topics", {})
    names = topics.setdefault(args.wiki, [])
    if not isinstance(names, list):
        raise SystemExit(f"invalid specialist allowlist for topic: {args.wiki}")
    changed = False
    if enabled and args.name not in names:
        names.append(args.name)
        names.sort()
        changed = True
    if not enabled and args.name in names:
        names.remove(args.name)
        changed = True
        if not names:
            topics.pop(args.wiki, None)
    operation = "enabled" if enabled else "disabled"
    if changed:
        write_specialist_registry(hub, registry)
        write_specialist_index(hub, registry)
        append_log(hub, "specialist", f"{operation} {args.name} for topic {args.wiki}")
        append_log(topic_path, "specialist", f"{operation} {args.name}")
    report = {
        "status": operation if changed else "unchanged",
        "name": args.name,
        "wiki": args.wiki,
    }
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        if changed:
            print(f"{operation.title()} {args.name} for {args.wiki}.")
        else:
            print(f"No change: {args.name} is already {operation} for {args.wiki}.")
    return 0


def specialist_enable(args: argparse.Namespace) -> int:
    return set_specialist_enabled(args, True)


def specialist_disable(args: argparse.Namespace) -> int:
    return set_specialist_enabled(args, False)


def run_specialist(args: argparse.Namespace) -> int:
    handlers = {
        "init": specialist_init,
        "create": specialist_create,
        "refresh": specialist_refresh,
        "list": specialist_list,
        "show": specialist_show,
        "validate": specialist_validate,
        "enable": specialist_enable,
        "disable": specialist_disable,
    }
    return handlers[args.specialist_command](args)


def register_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Register the ``specialist`` namespace on a top-level subparsers action."""
    specialist = subparsers.add_parser(
        "specialist",
        help="Manage personal, instruction-only specialist skills in the local hub.",
        description=(
            "Create and validate Agent Skills-compatible specialist methods under "
            "HUB/.skills, then explicitly enable them per active topic. Specialists "
            "never grant tools or professional authority."
        ),
    )
    specialist.add_argument("--hub", help="Override hub path for specialist operations.")
    specialist_sub = specialist.add_subparsers(dest="specialist_command", required=True)

    specialist_init_parser = specialist_sub.add_parser(
        "init", help="Initialize HUB/.skills without creating a specialist."
    )
    specialist_init_parser.add_argument(
        "--hub", default=argparse.SUPPRESS, help="Override hub path."
    )
    specialist_init_parser.add_argument(
        "--json", action="store_true", help="Print JSON."
    )

    specialist_create_parser = specialist_sub.add_parser(
        "create", help="Create an instruction-only specialist SKILL.md scaffold."
    )
    specialist_create_parser.add_argument("name", help="Stable lowercase specialist name.")
    specialist_create_parser.add_argument(
        "--description", required=True, help="Specific selection description, up to 1024 characters."
    )
    specialist_create_parser.add_argument(
        "--risk-tier",
        choices=sorted(SPECIALIST_RISK_TIERS),
        default="medium",
        help="Declared review risk tier (default: medium).",
    )
    specialist_create_parser.add_argument(
        "--hub", default=argparse.SUPPRESS, help="Override hub path."
    )
    specialist_create_parser.add_argument(
        "--json", action="store_true", help="Print JSON."
    )

    specialist_refresh_parser = specialist_sub.add_parser(
        "refresh", help="Rebuild .skills/_index.md after editing specialist files."
    )
    specialist_refresh_parser.add_argument(
        "--hub", default=argparse.SUPPRESS, help="Override hub path."
    )
    specialist_refresh_parser.add_argument(
        "--json", action="store_true", help="Print JSON."
    )

    specialist_list_parser = specialist_sub.add_parser(
        "list", help="List all specialists or the allowlist for one topic."
    )
    specialist_list_parser.add_argument(
        "--wiki", help="Show only specialists enabled for this active topic."
    )
    specialist_list_parser.add_argument(
        "--hub", default=argparse.SUPPRESS, help="Override hub path."
    )
    specialist_list_parser.add_argument(
        "--json", action="store_true", help="Print JSON."
    )

    specialist_show_parser = specialist_sub.add_parser(
        "show", help="Print one specialist SKILL.md and its metadata."
    )
    specialist_show_parser.add_argument("name", help="Specialist name.")
    specialist_show_parser.add_argument(
        "--hub", default=argparse.SUPPRESS, help="Override hub path."
    )
    specialist_show_parser.add_argument(
        "--json", action="store_true", help="Print JSON including the instruction body."
    )

    specialist_validate_parser = specialist_sub.add_parser(
        "validate", help="Validate the library or one specialist package."
    )
    specialist_validate_parser.add_argument("name", nargs="?", help="Optional specialist name.")
    specialist_validate_parser.add_argument(
        "--hub", default=argparse.SUPPRESS, help="Override hub path."
    )
    specialist_validate_parser.add_argument(
        "--json", action="store_true", help="Print JSON."
    )

    for action, help_text in [
        ("enable", "Allow one valid specialist for an active topic."),
        ("disable", "Remove one specialist from an active topic allowlist."),
    ]:
        action_parser = specialist_sub.add_parser(action, help=help_text)
        action_parser.add_argument("name", help="Specialist name.")
        action_parser.add_argument("--wiki", required=True, help="Active topic slug.")
        action_parser.add_argument(
            "--hub", default=argparse.SUPPRESS, help="Override hub path."
        )
        action_parser.add_argument("--json", action="store_true", help="Print JSON.")

    specialist.set_defaults(func=run_specialist)
    return specialist


def run(args: argparse.Namespace) -> int:
    """Dispatch a parsed ``specialist`` namespace."""
    return run_specialist(args)
