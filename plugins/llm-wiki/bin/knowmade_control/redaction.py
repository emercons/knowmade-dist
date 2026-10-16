"""The one secret and machine-path pattern table (emercons/knowmade#94, #88).

A leaf module: it imports only ``re``. Two consumers read the same table:

- ``checkpoints.checkpoint_pattern_specs`` scans a checkpoint bundle with
  every row whose contexts include ``"checkpoint"`` (secrets, identities,
  machine paths, transcript and prompt markers, private topic paths);
- ``scripts/llm-wiki-session`` redacts hook events and feedback previews with
  every row whose contexts include ``"redact"`` (secrets only). The session
  script ships as a single file and cannot import this package, so the sync
  scripts stamp the body of this module into it between generated markers
  (``scripts/sync-session-vendor.py``); never edit that copy by hand.

Each row is ``(category, severity, message, regex, flags, contexts)``. A
secret regex may capture a named group ``keep``: the redactor keeps that
prefix (``password=``, ``Bearer ``, ``https://user:``) and replaces only the
rest. Several rows may share a category.

Changing what the ``checkpoint`` rows catch requires a bump of
``checkpoints.CHECKPOINT_SCANNER_VERSION``.
"""

from __future__ import annotations

import re

_I = re.IGNORECASE | re.MULTILINE

PATTERN_TABLE: tuple[tuple[str, str, str, str, int, frozenset[str]], ...] = (
    (
        "secret-private-key",
        "critical",
        "Possible private-key material.",
        # The whole block, header to matching footer (or the end of the text
        # when the footer is missing), so a redaction removes the key body too.
        r"-----BEGIN ((?:[A-Z0-9 ]+ )?PRIVATE KEY|PGP PRIVATE KEY BLOCK)-----"
        r"[\s\S]*?(?:-----END \1-----|\Z)",
        _I,
        frozenset({"checkpoint", "redact"}),
    ),
    (
        "secret-known-token",
        "critical",
        "Possible service credential or bearer token.",
        # Provider token shapes: AWS long-term and STS key ids, GitHub classic
        # and fine-grained tokens, Slack, OpenAI/Anthropic-style `sk-` keys
        # (hyphens included: `sk-ant-api03-...`, `sk-proj-...`), Stripe live
        # and test keys, GitLab and npm tokens, Google API keys and JWTs.
        r"(?:\b(?:AKIA|ASIA)[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9]{20,}\b|"
        r"\bgithub_pat_[A-Za-z0-9_]{20,}\b|"
        r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b|\bsk-[A-Za-z0-9_-]{16,}\b|"
        r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}\b|\bglpat-[A-Za-z0-9_-]{20,}\b|"
        r"\bnpm_[A-Za-z0-9]{36,}\b|"
        r"\bAIza[0-9A-Za-z_-]{25,}\b|"
        r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b)",
        _I,
        frozenset({"checkpoint", "redact"}),
    ),
    (
        "secret-known-token",
        "critical",
        "Possible service credential or bearer token.",
        r"(?P<keep>\bBearer\s+)[A-Za-z0-9._~+/=-]{12,}",
        _I,
        frozenset({"checkpoint", "redact"}),
    ),
    (
        "secret-assignment",
        "critical",
        "Possible credential assigned to a sensitive field.",
        r"(?P<keep>\b(?:api[_-]?key|private[_-]?key|(?:access|auth|api|refresh)?[_-]?token|"
        r"client[_-]?secret|password|passwd|secret|session[_-]?cookie)\b"
        r"\s*[\"']?\s*[:=]\s*[\"']?)"
        r"[A-Za-z0-9_./+=$~-]{8,}",
        _I,
        frozenset({"checkpoint", "redact"}),
    ),
    (
        "secret-url-credentials",
        "critical",
        "Possible username/password embedded in a URL.",
        r"(?P<keep>\b[a-z][a-z0-9+.-]*://[^\s/@:]+:)[^\s/@]+(?=@)",
        _I,
        frozenset({"checkpoint", "redact"}),
    ),
    (
        # Prose credentials ("my password is hunter2", "token: abc") of any
        # length. Redaction only: in a checkpoint, prose such as "the token
        # is stored in the keychain" would block every honest bundle.
        "secret-prose-credential",
        "critical",
        "Possible credential written as prose.",
        r"(?P<keep>\b(?:password|passwd|pwd|secret|token|api[_-]?key)(?:\s+is\s+|\s*[=:]\s*))"
        r"[\"']?[^\s\"']+",
        _I,
        frozenset({"redact"}),
    ),
    (
        # Opaque three-part dotted tokens (signed cookies, JWT-like values
        # without the `eyJ` header). Redaction only: long dotted identifiers
        # are ordinary prose in a checkpoint.
        "secret-opaque-token",
        "critical",
        "Possible opaque signed token.",
        r"\b[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\b",
        0,
        frozenset({"redact"}),
    ),
    (
        "personal-email",
        "warning",
        "Email address may identify a person or private account.",
        r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        0,
        frozenset({"checkpoint"}),
    ),
    (
        "personal-account-field",
        "warning",
        "Account or username field may expose an identity.",
        r"^[ \t]*[\"']?(?:user(?:name)?|login|account(?:[_-]?name)?|handle)"
        r"[\"']?[ \t]*[:=][ \t]*[\"']?@?[A-Za-z0-9_.-]{2,}",
        _I,
        frozenset({"checkpoint"}),
    ),
    (
        "local-home-path",
        "critical",
        "Machine-local home directory path.",
        # The `(?<![:A-Za-z0-9])` lookbehind keeps the POSIX forms anchored
        # at an absolute root so `api/Users/1` or `https://h/Users/x` stay
        # quiet. It also rejects `/c/Users/x` (preceded by `c`) and
        # `C:/Users/x` (preceded by `:`), so Git Bash, forward-slash
        # Windows, WSL and Cygwin drive roots get their own alternatives.
        r"(?:(?<![:A-Za-z0-9])/Users/[A-Za-z0-9._-]+(?:/|\b)|"
        r"(?<![:A-Za-z0-9])/home/[A-Za-z0-9._-]+(?:/|\b)|"
        r"(?<![:A-Za-z0-9])/root(?:/|\b)|[A-Za-z]:\\Users\\[A-Za-z0-9._ -]+(?:\\|\b)|"
        r"(?<![A-Za-z0-9])[A-Za-z]:/Users/[A-Za-z0-9._-]+(?:/|\b)|"
        r"(?<![:/A-Za-z0-9])/[A-Za-z]/Users/[A-Za-z0-9._-]+(?:/|\b)|"
        r"(?<![:/A-Za-z0-9])/(?:mnt|cygdrive)/[A-Za-z]/Users/[A-Za-z0-9._-]+(?:/|\b))",
        _I,
        frozenset({"checkpoint"}),
    ),
    (
        "local-machine-path",
        "critical",
        "Machine-local volume, temporary, network share, or file URL path.",
        # Drive roots: `D:/x`, `D:\x` (not preceded by a word character,
        # so `https://x/y` is not a drive) and Git Bash `/d/x` (not
        # preceded by `:`, `/` or a word character, so `https://x/y` and
        # relative `a/b/c` stay quiet). UNC shares: `\\server\share`, not
        # preceded by another backslash or a word character.
        r"(?:file://|(?:~|\$HOME|\$\{HOME\})/|(?<![:A-Za-z0-9])/Volumes/[^\s/]+(?:/|\b)|"
        r"(?<![:A-Za-z0-9])/private/var/folders/|(?<![:A-Za-z0-9])/(?:tmp|var|etc|opt|srv|mnt|media|workspace|Applications|Library)/[^\s`\"']+|"
        r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s`\"']+|"
        r"(?<![:/A-Za-z0-9])/[A-Za-z]/[^\s`\"']+|"
        r"(?<![\\A-Za-z0-9])\\\\[A-Za-z0-9][A-Za-z0-9._$-]*\\[^\s`\"'\\]+)",
        _I,
        frozenset({"checkpoint"}),
    ),
    (
        "remote-login-identity",
        "warning",
        "Remote-login syntax may expose a username or private host.",
        r"\b(?:ssh|scp|sftp|rsync)\b[^\n]*\b[A-Za-z0-9._-]+@[A-Za-z0-9._-]+",
        _I,
        frozenset({"checkpoint"}),
    ),
    (
        "local-network-address",
        "warning",
        "Private-network address may reveal local infrastructure.",
        r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|"
        r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b",
        0,
        frozenset({"checkpoint"}),
    ),
    (
        "local-hostname",
        "warning",
        "Local hostname may reveal private infrastructure.",
        r"(?:\b[A-Za-z0-9][A-Za-z0-9-]{0,62}\.local\b|"
        r"\b[A-Za-z0-9][A-Za-z0-9.-]{0,126}\.(?:lan|internal)\b|"
        r"^[ \t]*[\"']?hostname[\"']?[ \t]*[:=][ \t]*[\"']?[^\s\"']+)",
        _I,
        frozenset({"checkpoint"}),
    ),
    (
        "session-transcript",
        "critical",
        "Session or transcript metadata must not enter a checkpoint.",
        r"(?:transcript[_-]?path|rollout-[^\s]+\.jsonl|\.sessions/(?:digests|feedback)/|"
        r"\b(?:codex|claude):[0-9a-f]{8}-[0-9a-f-]{27,}\b|UserPromptSubmit|PreCompact|PostCompact)",
        _I,
        frozenset({"checkpoint"}),
    ),
    (
        "prompt-conversation-role",
        "warning",
        "Conversation-role content may be copied prompt or transcript text.",
        r"(?:^[ \t]*(?:system|developer|assistant|user)[ \t]*:[ \t]+\S|"
        r"[\"']role[\"'][ \t]*:[ \t]*[\"'](?:system|developer|assistant|user)[\"'])",
        _I,
        frozenset({"checkpoint"}),
    ),
    (
        "prompt-instruction-marker",
        "warning",
        "Prompt/instruction marker may expose agent instructions.",
        r"(?:<\|(?:system|user|assistant)\|>|\[INST\]|</?system>|"
        r"AGENTS\.md instructions for|(?:system|developer) prompt[ \t]*:)",
        _I,
        frozenset({"checkpoint"}),
    ),
    (
        "private-topic-path",
        "critical",
        "Path into a -PRIVATE topic must not enter a checkpoint.",
        # A whole path segment ending in the canonical uppercase `-PRIVATE`
        # suffix: `topics/x-PRIVATE/...`, `x-PRIVATE/goals.md` after a space,
        # `` `x-PRIVATE` `` in backticks, or `x-PRIVATE` alone. `\w` is
        # Unicode, so `topics/santé-PRIVATE/` is caught too. The lookarounds
        # stop at any non-path character, so `x-PRIVATE-notes/`,
        # `PRIVATE.md`, `<slug>-PRIVATE` and prose such as `non-private`
        # (case-sensitive on purpose) stay quiet.
        r"(?<![\w.-])[\w.-]+-PRIVATE(?![\w-])",
        0,
        frozenset({"checkpoint"}),
    ),
)

_COMPILED: dict[str, list[tuple[str, str, str, re.Pattern[str]]]] = {}


def pattern_specs(context: str) -> list[tuple[str, str, str, re.Pattern[str]]]:
    """``(category, severity, message, compiled pattern)`` for one context, in table order."""
    if context not in _COMPILED:
        _COMPILED[context] = [
            (category, severity, message, re.compile(regex, flags))
            for category, severity, message, regex, flags, contexts in PATTERN_TABLE
            if context in contexts
        ]
    return list(_COMPILED[context])


def first_secret_category(text: str) -> str | None:
    """The category of the first redaction row that matches ``text``, or None."""
    for category, _severity, _message, pattern in pattern_specs("redact"):
        if pattern.search(text):
            return category
    return None


def redact_secrets(text: str, marker: str = "[REDACTED]") -> str:
    """Replace every secret in ``text`` by ``marker``, keeping each row's ``keep`` prefix."""

    def replace(match: re.Match[str]) -> str:
        keep = match.groupdict().get("keep") or ""
        return keep + marker

    for _category, _severity, _message, pattern in pattern_specs("redact"):
        text = pattern.sub(replace, text)
    return text
