"""Checkpoint pattern pack and finding ids (emercons/knowmade#105 R7).

The checkpoint rows of the shared table in ``redaction.py``, the
``privacy-<sha16>`` finding id and the scanner version. ``privacy`` and
``checkpoint_bundle`` both build on it; it imports only ``redaction``, so
neither the ``privacy`` scan nor a bundle seal needs the other. Moved
verbatim from ``checkpoints``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from . import redaction as _redaction


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
# 5: `checkpoint seal` also resolves the Markdown links, wiki-links and
#    source paths of the bundle (privacy_boundary), so a reference into a
#    -PRIVATE sibling is a blocking `cross-boundary` finding; no pattern changed.
CHECKPOINT_SCANNER_VERSION = 5


@dataclass(frozen=True)
class CheckpointFinding:
    finding_id: str
    category: str
    severity: str
    path: str
    line: int
    message: str
    # The matched text, kept only so an --allowlist regex can see it; never
    # serialized (checkpoint_finding_json omits it).
    evidence: str = field(default="", compare=False, repr=False)


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
        evidence=evidence,
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
