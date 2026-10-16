"""The ``checkpoint`` namespace: seal and verify Project Knowledge Checkpoints.

Deterministic validation, hashing, privacy scanning, sealing and verification
of a checkpoint bundle exported by the agentic wiki workflow. Moved verbatim
from ``legacy_cli`` (emercons/knowmade#67 R1.4, ``KM-CLI-001``); the
compatibility module re-exports every public name.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from . import fsio as _fsio
    from . import redaction as _redaction
    from .common import (
        SPECIALIST_NAME_RE,
        file_sha256,
        load_json_object,
    )
except ImportError:  # executed directly as a script, not imported as a package
    import fsio as _fsio
    import redaction as _redaction
    from common import (
        SPECIALIST_NAME_RE,
        file_sha256,
        load_json_object,
    )


CHECKPOINT_SCHEMA = "llm-wiki/project-knowledge-checkpoint/v1"
CHECKPOINT_PRIVACY_SCHEMA = "llm-wiki/checkpoint-privacy-report/v1"
# Bump whenever checkpoint_pattern_specs() changes what it catches. verify
# rejects reports sealed by another scanner version so a bundle that passed an
# older, weaker scan must be re-sealed before it is trusted.
# 2: local-home-path and local-machine-path cover Git Bash, forward-slash
#    Windows, WSL and Cygwin drive roots (`/c/Users/x`, `C:/Users/x`,
#    `/mnt/c/Users/x`, `D:/x`, `/d/x`).
# 3: private-topic-path flags `-PRIVATE` topic path segments
#    (`topics/x-PRIVATE/...`), the deterministic backstop for the
#    knowmade-profile.md "Private Data Boundary" write-guard.
# 4: the patterns moved to the shared table in redaction.py (#94).
#    secret-known-token adds `github_pat_`, `ASIA` (STS), `sk_live_`/
#    `sk_test_`/`rk_`, `glpat-` and `npm_` tokens; local-machine-path adds
#    UNC shares (`\\server\share`); private-topic-path accepts Unicode slugs
#    (`topics/santé-PRIVATE/`). A secret-url-credentials match now ends
#    before the `@`, and secret-private-key spans the whole key block, so
#    their finding IDs differ from scanner 3.
CHECKPOINT_SCANNER_VERSION = 4
CHECKPOINT_AUDIENCES = {"private", "team", "public"}
CHECKPOINT_REQUIRED_FILES = {
    "index.md",
    "project-knowledge.md",
    "sources.md",
    "checkpoint.json",
}
CHECKPOINT_REPORT_NAME = "privacy-report.json"
CHECKPOINT_MAX_FILE_BYTES = 10 * 1024 * 1024
CHECKPOINT_MIN_KNOWLEDGE_RETENTION_RATIO = 0.20


@dataclass(frozen=True)
class CheckpointFinding:
    finding_id: str
    category: str
    severity: str
    path: str
    line: int
    message: str


def checkpoint_pattern_specs() -> list[tuple[str, str, str, re.Pattern[str]]]:
    """The checkpoint rows of the shared pattern table (``redaction.py``)."""
    return _redaction.pattern_specs("checkpoint")


def checkpoint_finding(
    category: str,
    severity: str,
    path: str,
    line: int,
    message: str,
    evidence: str,
) -> CheckpointFinding:
    digest = hashlib.sha256(
        "\0".join([category, path, str(line), evidence]).encode("utf-8")
    ).hexdigest()[:16]
    return CheckpointFinding(
        finding_id=f"privacy-{digest}",
        category=category,
        severity=severity,
        path=path,
        line=line,
        message=message,
    )


def scan_checkpoint_text(text: str, rel: str) -> list[CheckpointFinding]:
    findings: list[CheckpointFinding] = []
    for category, severity, message, pattern in checkpoint_pattern_specs():
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            findings.append(
                checkpoint_finding(
                    category,
                    severity,
                    rel,
                    line,
                    message,
                    match.group(0),
                )
            )
    return findings


def checkpoint_file_paths(root: Path, include_report: bool = False) -> list[Path]:
    names = set(CHECKPOINT_REQUIRED_FILES)
    if include_report:
        names.add(CHECKPOINT_REPORT_NAME)
    return [root / name for name in sorted(names) if (root / name).exists() or (root / name).is_symlink()]


def checkpoint_source_key(item: dict[str, Any]) -> str:
    return f"{item.get('wiki', '')}:{item.get('path', '')}"


def checkpoint_word_count(text: str) -> int:
    return len(re.findall(r"\b[\w][\w'-]*\b", text, flags=re.UNICODE))


def checkpoint_coverage_metrics(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    inputs = manifest.get("inputs", [])
    source_word_count = sum(
        item["words"]
        for item in (inputs if isinstance(inputs, list) else [])
        if isinstance(item, dict)
        and isinstance(item.get("words"), int)
        and not isinstance(item.get("words"), bool)
    )
    knowledge_path = root / "project-knowledge.md"
    try:
        knowledge_text = knowledge_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        knowledge_text = ""
    knowledge_word_count = checkpoint_word_count(knowledge_text)
    retention_ratio = (
        knowledge_word_count / source_word_count if source_word_count else 0.0
    )
    return {
        "source_word_count": source_word_count,
        "knowledge_word_count": knowledge_word_count,
        "retention_ratio": round(retention_ratio, 4),
        "minimum_retention_ratio": CHECKPOINT_MIN_KNOWLEDGE_RETENTION_RATIO,
    }


def apply_checkpoint_coverage_metrics(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    metrics = checkpoint_coverage_metrics(root, manifest)
    if metrics["retention_ratio"] < CHECKPOINT_MIN_KNOWLEDGE_RETENTION_RATIO:
        required_words = math.ceil(
            metrics["source_word_count"] * CHECKPOINT_MIN_KNOWLEDGE_RETENTION_RATIO
        )
        raise SystemExit(
            "project-knowledge.md is too thin for comprehensive mode: "
            f"{metrics['knowledge_word_count']} words for "
            f"{metrics['source_word_count']} selected-source words; "
            f"write at least {required_words} words or reduce the selected scope"
        )
    coverage = manifest["coverage"]
    for key, value in metrics.items():
        coverage[key] = value
    return metrics


def checkpoint_source_access_findings(
    manifest: dict[str, Any], audience: str
) -> list[CheckpointFinding]:
    inputs = manifest.get("inputs")
    if not isinstance(inputs, list):
        return [
            checkpoint_finding(
                "structure-inputs",
                "critical",
                "checkpoint.json",
                1,
                "Manifest inputs must be an array.",
                "inputs-not-array",
            )
        ]
    source_overrides = manifest.get("source_overrides", [])
    if not isinstance(source_overrides, list):
        source_overrides = []
    override_keys = {
        entry.get("source")
        for entry in source_overrides
        if isinstance(entry, dict)
        and isinstance(entry.get("source"), str)
        and isinstance(entry.get("reason"), str)
        and entry.get("reason", "").strip()
    }
    allowed = {
        "private": {"public", "team", "private"},
        "team": {"public", "team"},
        "public": {"public"},
    }[audience]
    findings: list[CheckpointFinding] = []
    for index, item in enumerate(inputs):
        if not isinstance(item, dict):
            findings.append(
                checkpoint_finding(
                    "structure-input",
                    "critical",
                    "checkpoint.json",
                    1,
                    f"Input {index + 1} must be an object.",
                    f"input-{index}-not-object",
                )
            )
            continue
        access = item.get("access")
        key = checkpoint_source_key(item)
        if not isinstance(access, str) or access not in {"public", "team", "private", "unknown"}:
            message = f"Input {index + 1} has no valid access classification."
        elif access not in allowed:
            message = f"Input {index + 1} exceeds the declared {audience} audience."
        else:
            continue
        if key in override_keys:
            continue
        findings.append(
            checkpoint_finding(
                "source-access",
                "critical",
                "checkpoint.json",
                1,
                message,
                f"{index}:{key}:{access}:{audience}",
            )
        )
    return findings


def scan_checkpoint_bundle(
    root: Path, audience: str, manifest: dict[str, Any] | None = None
) -> tuple[list[CheckpointFinding], list[dict[str, str]]]:
    """Scan the bundle on disk; ``manifest`` substitutes an in-memory manifest
    for the on-disk one in the source-access check, so a seal can validate its
    overrides before writing anything."""
    findings: list[CheckpointFinding] = []
    files: list[dict[str, str]] = []
    allowed_names = CHECKPOINT_REQUIRED_FILES | {CHECKPOINT_REPORT_NAME}
    for path in sorted(root.iterdir(), key=lambda item: item.name):
        if path.name in allowed_names:
            continue
        opaque_path = "unexpected-" + hashlib.sha256(path.name.encode("utf-8")).hexdigest()[:12]
        findings.append(
            checkpoint_finding(
                "structure-unexpected-entry",
                "critical",
                opaque_path,
                0,
                "Checkpoint v1 contains an unexpected file or directory; its name is redacted.",
                path.name,
            )
        )
    for required in sorted(CHECKPOINT_REQUIRED_FILES):
        if not (root / required).is_file():
            findings.append(
                checkpoint_finding(
                    "structure-required-file",
                    "critical",
                    required,
                    0,
                    "Required checkpoint file is missing.",
                    required,
                )
            )
    for path in checkpoint_file_paths(root):
        rel = path.relative_to(root).as_posix()
        findings.extend(scan_checkpoint_text(rel, rel))
        if path.is_symlink():
            findings.append(
                checkpoint_finding(
                    "structure-symlink",
                    "critical",
                    rel,
                    0,
                    "Checkpoint bundles may not contain symbolic links.",
                    rel,
                )
            )
            continue
        try:
            size = path.stat().st_size
        except OSError as exc:
            findings.append(
                checkpoint_finding(
                    "structure-unreadable",
                    "critical",
                    rel,
                    0,
                    "Checkpoint file could not be inspected.",
                    f"{rel}:{type(exc).__name__}",
                )
            )
            continue
        if size > CHECKPOINT_MAX_FILE_BYTES:
            findings.append(
                checkpoint_finding(
                    "structure-file-size",
                    "critical",
                    rel,
                    0,
                    "Checkpoint file exceeds the 10 MiB safety limit.",
                    f"{rel}:{size}",
                )
            )
            continue
        if path.suffix.lower() not in {".md", ".json"}:
            findings.append(
                checkpoint_finding(
                    "structure-file-type",
                    "critical",
                    rel,
                    0,
                    "Checkpoint contains a non-Markdown/non-JSON file.",
                    rel,
                )
            )
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            findings.append(
                checkpoint_finding(
                    "structure-non-text",
                    "critical",
                    rel,
                    0,
                    "Checkpoint file is unreadable or not UTF-8 text.",
                    f"{rel}:{type(exc).__name__}",
                )
            )
            continue
        files.append({"path": rel, "sha256": file_sha256(path)})
        findings.extend(scan_checkpoint_text(text, rel))

    manifest_path = root / "checkpoint.json"
    if manifest is not None:
        findings.extend(checkpoint_source_access_findings(manifest, audience))
    elif manifest_path.is_file() and not manifest_path.is_symlink():
        try:
            manifest = load_json_object(manifest_path, "checkpoint manifest")
        except SystemExit as exc:
            findings.append(
                checkpoint_finding(
                    "structure-manifest-json",
                    "critical",
                    "checkpoint.json",
                    1,
                    "Checkpoint manifest is not a valid JSON object.",
                    str(exc),
                )
            )
        else:
            findings.extend(checkpoint_source_access_findings(manifest, audience))
    unique = {finding.finding_id: finding for finding in findings}
    return (
        sorted(unique.values(), key=lambda item: (item.path, item.line, item.category, item.finding_id)),
        sorted(files, key=lambda item: item["path"]),
    )


def validate_checkpoint_manifest(manifest: dict[str, Any], audience: str) -> None:
    if manifest.get("schema") != CHECKPOINT_SCHEMA:
        raise SystemExit(f"checkpoint.json schema must be {CHECKPOINT_SCHEMA!r}")
    if not isinstance(audience, str) or audience not in CHECKPOINT_AUDIENCES:
        raise SystemExit("checkpoint audience must be private, team, or public")
    project = manifest.get("project")
    if not isinstance(project, dict) or not isinstance(project.get("slug"), str) or not project.get("slug"):
        raise SystemExit("checkpoint.json project.slug must be a non-empty string")
    if not SPECIALIST_NAME_RE.fullmatch(project["slug"]):
        raise SystemExit("checkpoint.json project.slug must be lowercase hyphenated text")
    if not isinstance(manifest.get("created_at"), str) or not manifest["created_at"].strip():
        raise SystemExit("checkpoint.json created_at must be a non-empty string")
    if not isinstance(manifest.get("scope"), dict):
        raise SystemExit("checkpoint.json scope must be an object")
    if not isinstance(manifest.get("inputs"), list) or not manifest["inputs"]:
        raise SystemExit("checkpoint.json inputs must be an array")
    if not isinstance(manifest.get("gaps"), list):
        raise SystemExit("checkpoint.json gaps must be an array")
    for index, item in enumerate(manifest["inputs"]):
        if not isinstance(item, dict):
            raise SystemExit(f"checkpoint.json input {index + 1} must be an object")
        for field in ("wiki", "path", "sha256", "reason", "access"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise SystemExit(f"checkpoint.json input {index + 1} requires {field}")
        if not isinstance(item.get("words"), int) or item["words"] <= 0:
            raise SystemExit(f"checkpoint.json input {index + 1} words must be a positive integer")
        if not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
            raise SystemExit(f"checkpoint.json input {index + 1} sha256 must be 64 lowercase hex characters")
        source_path = item["path"]
        if Path(source_path).is_absolute() or ".." in Path(source_path).parts:
            raise SystemExit(f"checkpoint.json input {index + 1} path must be wiki-relative")
    input_keys = [checkpoint_source_key(item) for item in manifest["inputs"]]
    if len(set(input_keys)) != len(input_keys):
        raise SystemExit("checkpoint.json inputs must have unique wiki:path keys")

    coverage = manifest.get("coverage")
    if not isinstance(coverage, dict):
        raise SystemExit("checkpoint.json coverage must be an object")
    if coverage.get("mode") != "comprehensive":
        raise SystemExit("checkpoint.json coverage.mode must be 'comprehensive'")
    source_word_count = sum(item["words"] for item in manifest["inputs"])
    if coverage.get("source_word_count") != source_word_count:
        raise SystemExit("checkpoint.json coverage.source_word_count must equal input words")
    section_map = coverage.get("section_map")
    if not isinstance(section_map, list) or not section_map:
        raise SystemExit("checkpoint.json coverage.section_map must be a non-empty array")
    mapped_sources: set[str] = set()
    valid_input_keys = set(input_keys)
    for index, item in enumerate(section_map):
        if not isinstance(item, dict):
            raise SystemExit(f"checkpoint.json coverage section {index + 1} must be an object")
        if not isinstance(item.get("section"), str) or not item["section"].strip():
            raise SystemExit(f"checkpoint.json coverage section {index + 1} requires section")
        sources = item.get("sources")
        if not isinstance(sources, list) or not sources:
            raise SystemExit(f"checkpoint.json coverage section {index + 1} requires sources")
        if any(not isinstance(source, str) or source not in valid_input_keys for source in sources):
            raise SystemExit(f"checkpoint.json coverage section {index + 1} names an unknown input")
        mapped_sources.update(sources)
    if mapped_sources != valid_input_keys:
        raise SystemExit("checkpoint.json coverage.section_map must account for every input")
    omissions = coverage.get("omissions")
    if not isinstance(omissions, list):
        raise SystemExit("checkpoint.json coverage.omissions must be an array")
    for index, item in enumerate(omissions):
        if not isinstance(item, dict):
            raise SystemExit(f"checkpoint.json coverage omission {index + 1} must be an object")
        if not isinstance(item.get("source"), str) or item["source"] not in valid_input_keys:
            raise SystemExit(f"checkpoint.json coverage omission {index + 1} names an unknown input")
        for field in ("section", "reason"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise SystemExit(f"checkpoint.json coverage omission {index + 1} requires {field}")

    source_overrides = manifest.get("source_overrides", [])
    if not isinstance(source_overrides, list):
        raise SystemExit("checkpoint.json source_overrides must be an array")
    input_keys = set(input_keys)
    for index, item in enumerate(source_overrides):
        if not isinstance(item, dict):
            raise SystemExit(f"checkpoint.json source override {index + 1} must be an object")
        if not isinstance(item.get("source"), str) or item["source"] not in input_keys:
            raise SystemExit(f"checkpoint.json source override {index + 1} names an unknown input")
        if not isinstance(item.get("reason"), str) or not item["reason"].strip():
            raise SystemExit(f"checkpoint.json source override {index + 1} requires a reason")


def checkpoint_content_file_hashes(root: Path) -> list[dict[str, str]]:
    values: list[dict[str, str]] = []
    for path in checkpoint_file_paths(root):
        rel = path.relative_to(root).as_posix()
        if rel in {"checkpoint.json", CHECKPOINT_REPORT_NAME}:
            continue
        if path.is_symlink() or not path.is_file():
            continue
        values.append({"path": rel, "sha256": file_sha256(path)})
    return sorted(values, key=lambda item: item["path"])


def checkpoint_manifest_id(manifest: dict[str, Any]) -> str:
    payload = dict(manifest)
    payload.pop("checkpoint_id", None)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def checkpoint_report_id(report: dict[str, Any]) -> str:
    payload = dict(report)
    payload.pop("report_id", None)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def checkpoint_timestamp() -> str:
    override = os.environ.get("LLM_WIKI_NOW")
    if override:
        return override
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_checkpoint_json(path: Path, value: dict[str, Any]) -> None:
    _fsio.atomic_write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n", mode=0o644)


def checkpoint_finding_json(finding: CheckpointFinding, allowed: bool) -> dict[str, Any]:
    return {
        "id": finding.finding_id,
        "category": finding.category,
        "severity": finding.severity,
        "path": finding.path,
        "line": finding.line,
        "message": finding.message,
        "status": "allowed" if allowed else "blocking",
    }


def validate_override_reason(reason: str | None) -> str:
    value = (reason or "").strip()
    if not value:
        raise SystemExit("--override-reason is required when allowing a source or finding")
    if len(value) > 240 or "\n" in value or "\r" in value:
        raise SystemExit("--override-reason must be one line and no more than 240 characters")
    if scan_checkpoint_text(value, CHECKPOINT_REPORT_NAME):
        raise SystemExit("--override-reason itself matches a sensitive-information pattern")
    return value


def checkpoint_seal(args: argparse.Namespace) -> int:
    root = Path(args.path).expanduser().resolve(strict=False)
    if not root.is_dir():
        raise SystemExit("checkpoint seal path must be an existing directory")
    for name in CHECKPOINT_REQUIRED_FILES | {CHECKPOINT_REPORT_NAME}:
        if (root / name).is_symlink():
            raise SystemExit("checkpoint seal refuses symbolic links in the fixed bundle")
    manifest_path = root / "checkpoint.json"
    manifest = load_json_object(manifest_path, "checkpoint manifest")
    validate_checkpoint_manifest(manifest, args.audience)

    allowed_findings = set(args.allow_finding or [])
    allowed_sources = set(args.allow_source or [])
    reason: str | None = None
    if allowed_findings or allowed_sources:
        reason = validate_override_reason(args.override_reason)

    input_keys = {
        checkpoint_source_key(item)
        for item in manifest.get("inputs", [])
        if isinstance(item, dict)
    }
    unknown_sources = sorted(allowed_sources - input_keys)
    if unknown_sources:
        raise SystemExit("--allow-source names an input not present in checkpoint.json")
    # A new seal never trusts hand-written or stale source overrides. The
    # caller must re-authorize every retained above-audience input by exact key.
    manifest["source_overrides"] = [
        {"source": key, "reason": reason} for key in sorted(allowed_sources)
    ]

    manifest["audience"] = args.audience
    coverage_metrics = apply_checkpoint_coverage_metrics(root, manifest)
    manifest["files"] = checkpoint_content_file_hashes(root)
    manifest["checkpoint_id"] = checkpoint_manifest_id(manifest)
    # Every --allow-finding is checked against a scan of the bundle as it is
    # now, before the manifest is rewritten: a stale or invented ID must not
    # mutate anything (knowmade#48). The scan after the write re-checks
    # against the sealed bundle and is what the report records.
    if allowed_findings:
        current_findings, _ = scan_checkpoint_bundle(root, args.audience, manifest=manifest)
        if allowed_findings - {finding.finding_id for finding in current_findings}:
            raise SystemExit("--allow-finding contains an unknown or stale finding ID")
    write_checkpoint_json(manifest_path, manifest)

    findings, scanned_files = scan_checkpoint_bundle(root, args.audience)
    finding_ids = {finding.finding_id for finding in findings}
    unknown_findings = sorted(allowed_findings - finding_ids)
    if unknown_findings:
        raise SystemExit("--allow-finding contains an unknown or stale finding ID")
    blocking = [finding for finding in findings if finding.finding_id not in allowed_findings]
    source_override_count = len(manifest.get("source_overrides", []))
    status = "blocked" if blocking else (
        "overridden" if findings or source_override_count else "passed"
    )
    overrides = [
        {"finding_id": finding_id, "reason": reason}
        for finding_id in sorted(allowed_findings)
    ]
    report: dict[str, Any] = {
        "schema": CHECKPOINT_PRIVACY_SCHEMA,
        "scanner_version": CHECKPOINT_SCANNER_VERSION,
        "checkpoint_id": manifest["checkpoint_id"],
        "audience": args.audience,
        "scanned_at": checkpoint_timestamp(),
        "status": status,
        "files": scanned_files,
        "findings": [
            checkpoint_finding_json(finding, finding.finding_id in allowed_findings)
            for finding in findings
        ],
        "overrides": overrides,
        "source_override_count": source_override_count,
        "coverage": coverage_metrics,
    }
    report["report_id"] = checkpoint_report_id(report)
    write_checkpoint_json(root / CHECKPOINT_REPORT_NAME, report)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            f"Checkpoint seal: {status} ({len(scanned_files)} files, "
            f"{len(findings)} findings, {len(overrides)} overrides)"
        )
        for finding in findings:
            print(
                f"- {finding.finding_id} {finding.category} "
                f"{finding.path}:{finding.line} - {finding.message}"
            )
    return 2 if blocking else 0


def checkpoint_verify(args: argparse.Namespace) -> int:
    root = Path(args.path).expanduser().resolve(strict=False)
    if not root.is_dir():
        raise SystemExit("checkpoint verify path must be an existing directory")
    for name in CHECKPOINT_REQUIRED_FILES | {CHECKPOINT_REPORT_NAME}:
        if (root / name).is_symlink():
            result = {
                "status": "invalid",
                "errors": ["checkpoint verify refuses symbolic links in the fixed bundle"],
            }
            print(json.dumps(result, indent=2, sort_keys=True) if args.json else result["errors"][0])
            return 2
    try:
        manifest = load_json_object(root / "checkpoint.json", "checkpoint manifest")
        report = load_json_object(root / CHECKPOINT_REPORT_NAME, "checkpoint privacy report")
    except SystemExit as exc:
        result = {"status": "invalid", "errors": [str(exc)]}
        print(json.dumps(result, indent=2, sort_keys=True) if args.json else str(exc))
        return 2
    audience = manifest.get("audience")
    try:
        validate_checkpoint_manifest(manifest, audience)
    except SystemExit as exc:
        # Nothing below can be trusted against a manifest that failed
        # validation, and most of it would crash on one (#93).
        return print_checkpoint_invalid(args, [str(exc)])
    try:
        return checkpoint_verify_manifest(args, root, manifest, report, audience)
    except (TypeError, AttributeError, KeyError, ValueError) as exc:
        return print_checkpoint_invalid(
            args, [f"checkpoint bundle is malformed: {type(exc).__name__}: {exc}"]
        )


def print_checkpoint_invalid(args: argparse.Namespace, errors: list[str]) -> int:
    """Report an ``invalid`` verification (exit 2) without a traceback."""
    result = {"schema": CHECKPOINT_PRIVACY_SCHEMA, "status": "invalid", "errors": errors}
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("Checkpoint verify: invalid")
        for error in errors:
            print(f"- {error}")
    return 2


def checkpoint_verify_manifest(
    args: argparse.Namespace,
    root: Path,
    manifest: dict[str, Any],
    report: dict[str, Any],
    audience: str,
) -> int:
    """Verify a bundle whose manifest passed ``validate_checkpoint_manifest``."""
    errors: list[str] = []
    if report.get("schema") != CHECKPOINT_PRIVACY_SCHEMA:
        errors.append("privacy-report.json has an unsupported schema")
    if report.get("scanner_version") != CHECKPOINT_SCANNER_VERSION:
        errors.append(
            "privacy-report.json scanner version "
            f"{report.get('scanner_version')!r} is unsupported; re-seal with "
            f"scanner version {CHECKPOINT_SCANNER_VERSION}"
        )
    if report.get("report_id") != checkpoint_report_id(report):
        errors.append("privacy-report.json report_id does not match its content")
    expected_checkpoint_id = checkpoint_manifest_id(manifest)
    if manifest.get("checkpoint_id") != expected_checkpoint_id:
        errors.append("checkpoint.json checkpoint_id does not match its content")
    if report.get("checkpoint_id") != manifest.get("checkpoint_id"):
        errors.append("privacy report checkpoint_id does not match checkpoint.json")
    if report.get("audience") != audience:
        errors.append("privacy report audience does not match checkpoint.json")

    coverage_metrics = checkpoint_coverage_metrics(root, manifest)
    coverage = manifest.get("coverage", {})
    for key, value in coverage_metrics.items():
        if coverage.get(key) != value:
            errors.append(f"checkpoint.json coverage {key} does not match the bundle")
    if coverage_metrics["retention_ratio"] < CHECKPOINT_MIN_KNOWLEDGE_RETENTION_RATIO:
        errors.append("project-knowledge.md is too thin for comprehensive mode")
    if report.get("coverage") != coverage_metrics:
        errors.append("privacy report coverage metrics do not match the bundle")

    actual_content_files = checkpoint_content_file_hashes(root)
    if manifest.get("files") != actual_content_files:
        errors.append("checkpoint.json generated-file hashes do not match the bundle")
    findings, scanned_files = scan_checkpoint_bundle(root, audience)
    if report.get("files") != scanned_files:
        errors.append("privacy report file hashes do not match the bundle")
    report_findings = report.get("findings", [])
    if not isinstance(report_findings, list):
        errors.append("privacy report findings must be an array")
        report_findings = []
    report_finding_ids = {
        item.get("id")
        for item in report_findings
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    actual_finding_ids = {finding.finding_id for finding in findings}
    if report_finding_ids != actual_finding_ids:
        errors.append("privacy findings do not match a fresh scan")
    override_rows = report.get("overrides")
    if not isinstance(override_rows, list):
        errors.append("privacy report overrides must be an array")
        override_rows = []
    override_ids: set[str] = set()
    for index, item in enumerate(override_rows):
        if not isinstance(item, dict) or not isinstance(item.get("finding_id"), str):
            errors.append(f"privacy report override {index + 1} is invalid")
            continue
        try:
            validate_override_reason(item.get("reason"))
        except SystemExit:
            errors.append(f"privacy report override {index + 1} has an invalid reason")
            continue
        override_ids.add(item["finding_id"])
    if len(override_ids) != len(override_rows):
        errors.append("privacy report overrides contain duplicate or invalid finding IDs")
    if not override_ids.issubset(actual_finding_ids):
        errors.append("privacy report contains an unknown or stale override")
    expected_finding_records = [
        checkpoint_finding_json(finding, finding.finding_id in override_ids)
        for finding in findings
    ]
    if report.get("findings") != expected_finding_records:
        errors.append("privacy report finding metadata does not match a fresh scan")
    blocking_ids = actual_finding_ids - override_ids
    source_override_count = len(manifest.get("source_overrides", []))
    if report.get("source_override_count") != source_override_count:
        errors.append("privacy report source override count does not match checkpoint.json")
    expected_status = "blocked" if blocking_ids else (
        "overridden" if actual_finding_ids or source_override_count else "passed"
    )
    if report.get("status") != expected_status:
        errors.append("privacy report status does not match the current findings")
    result = {
        "schema": CHECKPOINT_PRIVACY_SCHEMA,
        "status": "invalid" if errors else expected_status,
        "checkpoint_id": manifest.get("checkpoint_id"),
        "audience": audience,
        "file_count": len(scanned_files),
        "finding_count": len(findings),
        "override_count": len(override_ids),
        "source_override_count": source_override_count,
        "coverage": coverage_metrics,
        "finding_categories": sorted({finding.category for finding in findings}),
        "errors": errors,
    }
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(
            f"Checkpoint verify: {result['status']} ({result['file_count']} files, "
            f"{result['finding_count']} findings, {result['override_count']} overrides)"
        )
        for error in errors:
            print(f"- {error}")
    return 2 if errors or expected_status == "blocked" else 0


def run_checkpoint(args: argparse.Namespace) -> int:
    handlers = {"seal": checkpoint_seal, "verify": checkpoint_verify}
    return handlers[args.checkpoint_command](args)


def register_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Register the ``checkpoint`` namespace on a top-level subparsers action."""
    checkpoint = subparsers.add_parser(
        "checkpoint",
        help="Seal and verify exported Project Knowledge Checkpoints.",
        description=(
            "Deterministically validate, hash, privacy-scan, seal, and verify a "
            "Project Knowledge Checkpoint generated by the agentic wiki workflow."
        ),
    )
    checkpoint_sub = checkpoint.add_subparsers(dest="checkpoint_command", required=True)

    checkpoint_seal_parser = checkpoint_sub.add_parser(
        "seal", help="Finalize hashes and run the mandatory privacy scan."
    )
    checkpoint_seal_parser.add_argument("path", help="Checkpoint bundle directory.")
    checkpoint_seal_parser.add_argument(
        "--audience",
        required=True,
        choices=sorted(CHECKPOINT_AUDIENCES),
        help="Declared recipient boundary for source-access validation.",
    )
    checkpoint_seal_parser.add_argument(
        "--allow-finding",
        action="append",
        default=[],
        help="Explicitly acknowledge one exact finding ID; repeatable.",
    )
    checkpoint_seal_parser.add_argument(
        "--allow-source",
        action="append",
        default=[],
        help="Explicitly retain one exact wiki:path input above the audience; repeatable.",
    )
    checkpoint_seal_parser.add_argument(
        "--override-reason",
        help="One-line reason required for any source or finding override.",
    )
    checkpoint_seal_parser.add_argument("--json", action="store_true", help="Print JSON.")

    checkpoint_verify_parser = checkpoint_sub.add_parser(
        "verify", help="Recheck bundle structure, hashes, and privacy attestation."
    )
    checkpoint_verify_parser.add_argument("path", help="Checkpoint bundle directory.")
    checkpoint_verify_parser.add_argument("--json", action="store_true", help="Print JSON.")

    checkpoint.set_defaults(func=run_checkpoint)
    return checkpoint


def run(args: argparse.Namespace) -> int:
    """Dispatch a parsed ``checkpoint`` namespace."""
    return run_checkpoint(args)
