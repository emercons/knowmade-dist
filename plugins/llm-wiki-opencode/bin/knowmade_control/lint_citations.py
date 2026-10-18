"""Lint profile ``citations`` (C26): claim-level citation heuristics.

Off by default; ``lint --profile citations`` runs ``check_citations`` after the
regular checks (emercons/knowmade#119). It judges the compiled articles of
``wiki/`` (code blocks and inline code removed, frontmatter skipped) and reports
three kinds of citation debt, each through ``rule_lint.issue_versioned`` as
check ``C26``, so a rules/3 corpus sees them as ``info`` and rules/4 as
``warning``:

- ``W_FOOTNOTE_LOCATOR``: a footnote definition with no locator. A locator is
  what lets a reader find the claim in the source: a page (``p. 12``,
  ``pp. 3-5``, ``page 4``), a section or chapter, a paragraph or line number,
  a figure or table, a ``§``, a timestamp (``12:30``, ``1:02:03``) or a
  ``#fragment``.
- ``W_LONG_UNCITED``: an article whose body is longer than
  ``citations_long_words`` words (default 400) and carries no citation.
- ``W_SIGNAL_UNCITED``: a paragraph that makes a sourced claim, one containing
  a signal phrase (``according to``, ``reported``, ``study``, ...), and cites
  nothing in the same paragraph.

A citation is a footnote reference (``[^key]``) or a Markdown link into
``raw/``. Paragraphs under a ``See Also``, ``Sources``, ``References``,
``Related`` or ``Further reading`` heading are not judged.

Thresholds come from ``config.md`` frontmatter, read through
``rules.config_fields``: ``citations_long_words`` (a positive integer) and
``citations_signal_words`` (a list of phrases, matched case-insensitively on
word boundaries; a list replaces the default). An invalid value is reported and
the default is used.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import rule_lint as _rule_lint
from . import rules as _rules
from .lint_links import FOOTNOTE_DEF_RE, FOOTNOTE_REF_RE, strip_code_blocks
from .lint_report import LintContext, document_text

INTRODUCED_IN = 3
DEFAULT_LONG_WORDS = 400
DEFAULT_SIGNAL_WORDS = (
    "according to",
    "reported",
    "reports that",
    "study",
    "studies",
    "survey",
    "research shows",
    "found that",
    "estimated",
    "announced",
)
LONG_WORDS_KEY = "citations_long_words"
SIGNAL_WORDS_KEY = "citations_signal_words"

LOCATOR_RE = re.compile(
    r"\b(?:pp?\.|pages?|sec(?:tion|s?\.)|chapters?|ch\.|paragraphs?|para\.|lines?|"
    r"fig(?:ure|\.)|tables?|slides?|minutes?)\s*\d"
    r"|§\s*\d"
    r"|\b\d{1,2}:\d{2}(?::\d{2})?\b"
    r"|#[A-Za-z0-9_-]+",
    re.IGNORECASE,
)
RAW_LINK_RE = re.compile(r"\]\((?:\./|(?:\.\./)+)?(?:[^)\s]*/)?raw/[^)\s]*\)")
HEADING_RE = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
UNJUDGED_HEADING_RE = re.compile(
    r"^(?:see also|sources?|references?|related(?: articles| topics)?|further reading|bibliography)$",
    re.IGNORECASE,
)
WORD_RE = re.compile(r"[^\W_][\w'’-]*")


def settings(ctx: LintContext) -> tuple[int, tuple[str, ...]]:
    """``(long-article word limit, signal phrases)`` from ``config.md``, reporting bad values."""
    fields = _rules.config_fields(ctx.root) or {}
    config = ctx.root / "config.md"
    long_words = DEFAULT_LONG_WORDS
    if LONG_WORDS_KEY in fields:
        value = fields[LONG_WORDS_KEY]
        # The frontmatter reader yields scalars as text: ``5000`` is "5000".
        if not isinstance(value, bool) and re.fullmatch(r"[0-9]+", str(value).strip()) and int(str(value)) > 0:
            long_words = int(str(value))
        else:
            _rule_lint.issue_versioned(
                ctx,
                INTRODUCED_IN,
                f"{LONG_WORDS_KEY} in config.md must be a positive integer, not {value!r}; "
                f"using {DEFAULT_LONG_WORDS}.",
                config,
                check="C26",
            )
    signal = DEFAULT_SIGNAL_WORDS
    if SIGNAL_WORDS_KEY in fields:
        value = fields[SIGNAL_WORDS_KEY]
        phrases = [item.strip() for item in value if isinstance(item, str) and item.strip()] if isinstance(value, list) else []
        if phrases:
            signal = tuple(phrases)
        else:
            _rule_lint.issue_versioned(
                ctx,
                INTRODUCED_IN,
                f"{SIGNAL_WORDS_KEY} in config.md must be a non-empty list of phrases, not {value!r}; "
                "using the defaults.",
                config,
                check="C26",
            )
    return long_words, signal


def signal_pattern(phrases: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(re.escape(phrase) for phrase in phrases) + r")\b", re.IGNORECASE)


def body_start_line(text: str) -> int:
    """The 0-based line index where the body begins (after a closing ``---`` fence)."""
    lines = text.removeprefix(chr(0xFEFF)).split("\n")
    if lines and lines[0].rstrip() == "---":
        for number, line in enumerate(lines[1:], start=1):
            if line.rstrip() == "---":
                return number + 1
    return 0


def paragraphs(lines: list[str], first: int) -> list[tuple[int, str, str]]:
    """``(1-based first line, section heading, text)`` of every block from ``first`` on."""
    blocks: list[tuple[int, str, str]] = []
    heading = ""
    current: list[str] = []
    start = 0
    for number in range(first, len(lines)):
        line = lines[number].rstrip("\r")
        match = HEADING_RE.match(line)
        if not line.strip() or match or (current and FOOTNOTE_DEF_RE.match(line)):
            if current:
                blocks.append((start, heading, "\n".join(current)))
                current = []
            if match:
                heading = match.group(1)
            if match or not line.strip():
                continue
        if not current:
            start = number + 1
        current.append(line)
    if current:
        blocks.append((start, heading, "\n".join(current)))
    return blocks


def has_citation(text: str) -> bool:
    return bool(FOOTNOTE_REF_RE.search(text) or RAW_LINK_RE.search(text))


def check_article(ctx: LintContext, path: Path, text: str, long_words: int, signal: re.Pattern[str]) -> None:
    lines = strip_code_blocks(text).split("\n")
    blocks = paragraphs(lines, body_start_line(text))
    prose: list[str] = []
    cited = False
    for start, heading, block in blocks:
        if FOOTNOTE_DEF_RE.match(block):
            definition = FOOTNOTE_DEF_RE.sub("", block, count=1)
            if not LOCATOR_RE.search(definition):
                key = FOOTNOTE_DEF_RE.match(block).group(1)  # type: ignore[union-attr]
                _rule_lint.issue_versioned(
                    ctx,
                    INTRODUCED_IN,
                    f"W_FOOTNOTE_LOCATOR: footnote [^{key}] names a source but no locator "
                    "(page, section, line, timestamp or #fragment).",
                    path,
                    check="C26",
                    line=start,
                )
            continue
        if has_citation(block):
            cited = True
        if UNJUDGED_HEADING_RE.match(heading):
            continue
        prose.append(block)
        found = signal.search(block)
        if found and not has_citation(block):
            _rule_lint.issue_versioned(
                ctx,
                INTRODUCED_IN,
                f"W_SIGNAL_UNCITED: paragraph says {found.group(0)!r} but cites no source.",
                path,
                check="C26",
                line=start,
            )
    words = len(WORD_RE.findall("\n".join(prose)))
    if words > long_words and not cited:
        _rule_lint.issue_versioned(
            ctx,
            INTRODUCED_IN,
            f"W_LONG_UNCITED: article body is {words} words (limit {long_words}) and cites no source.",
            path,
            check="C26",
        )


def check_citations(ctx: LintContext) -> None:
    """Run the citation heuristics over every compiled article of ``wiki/``."""
    long_words, phrases = settings(ctx)
    signal = signal_pattern(phrases)
    for doc in sorted(ctx.documents.values(), key=lambda item: str(item.path)):
        try:
            rel = doc.path.resolve().relative_to(ctx.root)
        except ValueError:
            continue
        if not rel.parts or rel.parts[0] != "wiki" or doc.path.name == "_index.md":
            continue
        check_article(ctx, doc.path, document_text(doc), long_words, signal)
