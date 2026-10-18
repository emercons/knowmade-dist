"""Article freshness: configurable windows, dated snapshots and the due report (knowmade#108).

A compiled article (``wiki/``) is due for refresh when more days have passed
since its ``verified`` (else ``updated``) date than the window of its
``volatility``. The windows default to hot 30, warm 180 and cold 365 days; a
topic's ``config.md`` frontmatter may set ``freshness_hot_days``,
``freshness_warm_days`` and ``freshness_cold_days``, and a topic under
``HUB/topics/`` inherits each key it does not set from ``HUB/config.md``.
``freshness_threshold`` is the librarian's score threshold and is not a
window: lint never reads it.

An article with ``freshness: snapshot`` records a dated state that is old by
design (a research note named ``*-2026-08-18.md``); past its window it is
reported as ``info``, not as a stale-article warning, and ``freshness report``
leaves it out unless asked.

Severity follows COMPATIBILITY.md: a window longer than the default and a
snapshot only relax lint. An article that is stale only because a configured
window is shorter than the default, an invalid window and an unknown
``freshness`` value are new strictness, reported through
``rule_lint.issue_versioned`` from rules/:data:`WINDOW_RULE_SET` on.

``freshness score`` computes the librarian's composite 0-100 score
(``references/librarian.md`` § Staleness Scoring, knowmade#83): four 0-25
dimensions (source freshness, verification, compilation, source-chain
integrity), each age decayed with the volatility half-life (hot 30, warm 90,
cold 365 days), a ``compiled-from: conversation`` article rebased on its two
dated dimensions. It lists the articles below ``freshness_threshold``
(``config.md``, inherited from ``HUB/config.md``, default 70). Lint never
reads the threshold.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import engine as _engine
from . import rule_lint as _rule_lint
from . import rules as _rules
from .common import add_scope_arguments, as_string_list, parse_date
from .lint_docs import load_documents, resolve_source_ref
from .lint_report import LintContext
from .markdown import today_date
from .roots import is_hub, resolve_wiki_root_args

DEFAULT_WINDOWS: dict[str, int] = {"hot": 30, "warm": 180, "cold": 365}
WINDOW_KEYS: dict[str, str] = {
    "hot": "freshness_hot_days",
    "warm": "freshness_warm_days",
    "cold": "freshness_cold_days",
}
FRESHNESS_KEY = "freshness"
SNAPSHOT = "snapshot"
FRESHNESS_VALUES = frozenset({SNAPSHOT})
# The rule set in which configurable windows and ``freshness:`` were introduced.
WINDOW_RULE_SET = 3


@dataclass
class Windows:
    """The window of each volatility for one wiki root, and where each came from.

    ``source`` maps a volatility to ``default``, ``wiki`` (the root's own
    ``config.md``) or ``hub`` (``HUB/config.md``); ``problems`` lists
    ``(config path, message)`` for every key that was set but unusable.
    """

    days: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_WINDOWS))
    source: dict[str, str] = field(default_factory=lambda: {key: "default" for key in DEFAULT_WINDOWS})
    config: dict[str, str | None] = field(default_factory=lambda: {key: None for key in DEFAULT_WINDOWS})
    problems: list[tuple[Path, str]] = field(default_factory=list)


def _window_value(value: Any) -> int | None:
    """A positive whole number of days, or None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    text = str(value).strip().strip("\"'")
    return int(text) if text.isdigit() and int(text) > 0 else None


def resolve_windows(wiki_root: Path) -> Windows:
    """The freshness windows of ``wiki_root``: its own keys, then the hub's, then the defaults."""
    windows = Windows()
    layers: list[tuple[str, Path, dict[str, Any]]] = []
    own = _rules.config_fields(wiki_root)
    if own:
        layers.append(("wiki", wiki_root / "config.md", own))
    hub = _engine.topic_hub(wiki_root)
    if hub is not None:
        inherited = _rules.config_fields(hub)
        if inherited:
            layers.append(("hub", hub / "config.md", inherited))
    for volatility, key in WINDOW_KEYS.items():
        for source, config, fields in layers:
            if key not in fields:
                continue
            days = _window_value(fields[key])
            if days is None:
                windows.problems.append(
                    (config, f"Invalid {key}: {fields[key]!r}; expected a positive number of days.")
                )
                continue
            windows.days[volatility] = days
            windows.source[volatility] = source
            windows.config[volatility] = str(config)
            break
    return windows


def article_status(frontmatter: dict[str, Any], windows: Windows, today: dt.date) -> dict[str, Any]:
    """The freshness of one compiled article.

    ``state`` is ``no-volatility``, ``unknown-volatility`` (reported by the enum
    check, not here), ``undated``, ``fresh`` or ``stale``. ``due_in`` is the
    number of days left in the window (negative once stale).
    """
    raw_freshness = frontmatter.get(FRESHNESS_KEY)
    snapshot = isinstance(raw_freshness, str) and raw_freshness.strip().lower() == SNAPSHOT
    status: dict[str, Any] = {
        "volatility": None,
        "verified": None,
        "age": None,
        "window": None,
        "window_source": None,
        "due_in": None,
        "snapshot": snapshot,
        "freshness": raw_freshness,
    }
    volatility = frontmatter.get("volatility")
    if volatility is None:
        status["state"] = "no-volatility"
        return status
    # A non-string value (for example ``volatility: [hot]``) is already
    # reported by the enum check; it is also unhashable, so it must never
    # reach the ``in`` test against the window table.
    if not isinstance(volatility, str) or volatility not in windows.days:
        status["state"] = "unknown-volatility"
        return status
    status["volatility"] = volatility
    status["window"] = windows.days[volatility]
    status["window_source"] = windows.source[volatility]
    verified = frontmatter.get("verified") or frontmatter.get("updated")
    verified_date = parse_date(str(verified)) if verified else None
    if not verified_date:
        status["state"] = "undated"
        return status
    age = (today - verified_date).days
    status["verified"] = verified_date.isoformat()
    status["age"] = age
    status["due_in"] = windows.days[volatility] - age
    status["state"] = "stale" if age > windows.days[volatility] else "fresh"
    return status


def _compiled_documents(ctx: LintContext) -> list[tuple[str, Any]]:
    """``(relative path, document)`` of every loaded document under ``wiki/``."""
    rows = []
    for doc in ctx.documents.values():
        try:
            rel = doc.path.resolve().relative_to(ctx.root)
        except ValueError:
            continue
        if rel.parts and rel.parts[0] == "wiki":
            rows.append((rel.as_posix(), doc))
    return sorted(rows, key=lambda row: row[0])


def check_freshness(ctx: LintContext) -> None:
    """Lint step ``freshness`` (C14 and C15)."""
    windows = resolve_windows(ctx.root)
    for config, message in windows.problems:
        _rule_lint.issue_versioned(ctx, WINDOW_RULE_SET, message, config, check="C14")
    today = today_date()
    for _rel, doc in _compiled_documents(ctx):
        status = article_status(doc.frontmatter, windows, today)
        raw_freshness = status["freshness"]
        if raw_freshness is not None and not status["snapshot"]:
            _rule_lint.issue_versioned(
                ctx,
                WINDOW_RULE_SET,
                f"Unknown {FRESHNESS_KEY} value {raw_freshness!r}; the only value is {SNAPSHOT}.",
                doc.path,
                check="C14",
            )
        state = status["state"]
        if state == "no-volatility":
            ctx.issue("info", "Compiled article has no volatility field.", doc.path, check="C15")
        elif state == "undated":
            ctx.issue("warning", "Compiled article has no valid verified/updated date.", doc.path, check="C14")
        elif state == "stale":
            _report_stale(ctx, doc.path, status)


def _report_stale(ctx: LintContext, path: Path, status: dict[str, Any]) -> None:
    age, volatility, window = status["age"], status["volatility"], status["window"]
    if status["snapshot"]:
        ctx.issue(
            "info",
            f"Snapshot article is {age} days old ({volatility} window {window} days); "
            "freshness: snapshot records a dated state, so it is not refreshed.",
            path,
            check="C14",
        )
        return
    message = f"Compiled article may be stale: {age} days since verification for {volatility} volatility."
    if status["window_source"] != "default":
        message = message[:-1] + f" (window {window} days from the {status['window_source']} config.md)."
    if age > DEFAULT_WINDOWS[volatility]:
        ctx.issue("warning", message, path, check="C14")
    else:
        # Stale only because a configured window is shorter than the default.
        _rule_lint.issue_versioned(ctx, WINDOW_RULE_SET, message, path, check="C14")


def report_payload(wiki_root: Path, *, due: int = 0, include_snapshots: bool = False) -> dict[str, Any]:
    """Articles due for refresh: ``due_in < due`` (default: already stale).

    Deterministic and read-only; input for a scheduled ``wiki refresh``.
    """
    ctx = LintContext(wiki_root)
    load_documents(ctx)
    windows = resolve_windows(ctx.root)
    today = today_date()
    due_rows: list[dict[str, Any]] = []
    undated: list[str] = []
    snapshots_skipped = 0
    for rel, doc in _compiled_documents(ctx):
        status = article_status(doc.frontmatter, windows, today)
        if status["state"] == "undated":
            undated.append(rel)
            continue
        if status["due_in"] is None or status["due_in"] >= due:
            continue
        if status["snapshot"] and not include_snapshots:
            snapshots_skipped += 1
            continue
        due_rows.append(
            {
                "path": rel,
                "volatility": status["volatility"],
                "verified": status["verified"],
                "age": status["age"],
                "window": status["window"],
                "due_in": status["due_in"],
                "snapshot": status["snapshot"],
            }
        )
    due_rows.sort(key=lambda row: (row["due_in"], row["path"]))
    return {
        "schema": "knowmade/freshness-report/v1",
        "root": str(ctx.root),
        "today": today.isoformat(),
        "due": due,
        "windows": {
            volatility: {
                "days": windows.days[volatility],
                "source": windows.source[volatility],
                "config": windows.config[volatility],
            }
            for volatility in DEFAULT_WINDOWS
        },
        "articles": due_rows,
        "undated": undated,
        "snapshots_skipped": snapshots_skipped,
        "problems": [f"{config}: {message}" for config, message in windows.problems],
    }


HALF_LIVES: dict[str, int] = {"hot": 30, "warm": 90, "cold": 365}
THRESHOLD_KEY = "freshness_threshold"
DEFAULT_THRESHOLD = 70


def resolve_threshold(wiki_root: Path) -> tuple[int, str, list[str]]:
    """``(threshold, source, problems)``: the root's ``freshness_threshold``, else the hub's, else 70."""
    problems: list[str] = []
    layers = [("wiki", wiki_root, _rules.config_fields(wiki_root))]
    hub = _engine.topic_hub(wiki_root)
    if hub is not None:
        layers.append(("hub", hub, _rules.config_fields(hub)))
    for source, root, fields in layers:
        if not fields or THRESHOLD_KEY not in fields:
            continue
        value = fields[THRESHOLD_KEY]
        text = str(value).strip().strip("\"'")
        if isinstance(value, bool) or not text.isdigit() or int(text) > 100:
            problems.append(f"{root / 'config.md'}: Invalid {THRESHOLD_KEY}: {value!r}; expected 0-100.")
            continue
        return int(text), source, problems
    return DEFAULT_THRESHOLD, "default", problems


def _decayed(days: int, half_life: int) -> float:
    return 25 * 0.5 ** (max(days, 0) / half_life)


def article_score(ctx: LintContext, doc: Any, today: dt.date) -> dict[str, Any]:
    """The composite freshness score of one compiled article (librarian.md § Staleness Scoring)."""
    fields = doc.frontmatter
    volatility = fields.get("volatility")
    if not isinstance(volatility, str) or volatility not in HALF_LIVES:
        volatility = "warm"
    half_life = HALF_LIVES[volatility]
    verified = parse_date(str(fields.get("verified") or ""))
    updated = parse_date(str(fields.get("updated") or fields.get("created") or ""))
    factors = {
        "verification": _decayed((today - verified).days, half_life) if verified else 0.0,
        "compilation": _decayed((today - updated).days, half_life) if updated else 0.0,
    }
    conversation = str(fields.get("compiled-from") or "").strip().lower() == "conversation"
    unresolved: list[str] = []
    if conversation:
        score = 2 * sum(factors.values())
    else:
        refs = as_string_list(fields.get("sources"))
        ages: list[int] = []
        for ref in refs:
            target = resolve_source_ref(ctx, doc.path, ref, True)
            raw = ctx.documents.get(target) if target is not None else None
            if target is None:
                unresolved.append(ref)
                continue
            ingested = parse_date(str(raw.frontmatter.get("ingested") or "")) if raw else None
            if ingested is not None:
                ages.append((today - ingested).days)
        factors["source_freshness"] = _decayed(round(sum(ages) / len(ages)), half_life) if ages else 0.0
        factors["integrity"] = 25 * (len(refs) - len(unresolved)) / len(refs) if refs else 0.0
        score = sum(factors.values())
    raw_freshness = fields.get(FRESHNESS_KEY)
    return {
        "score": round(score),
        "volatility": volatility,
        "half_life": half_life,
        "factors": {name: round(value) for name, value in factors.items()},
        "conversation": conversation,
        "unresolved_sources": unresolved,
        "snapshot": isinstance(raw_freshness, str) and raw_freshness.strip().lower() == SNAPSHOT,
    }


def score_payload(
    wiki_root: Path, *, threshold: int | None = None, include_all: bool = False, include_snapshots: bool = False
) -> dict[str, Any]:
    """Articles below the freshness threshold (or every article), lowest score first. Read-only."""
    ctx = LintContext(wiki_root)
    load_documents(ctx)
    configured, source, problems = resolve_threshold(ctx.root)
    if threshold is None:
        threshold = configured
    else:
        source = "argument"
    today = today_date()
    rows: list[dict[str, Any]] = []
    snapshots_skipped = 0
    for rel, doc in _compiled_documents(ctx):
        row = {"path": rel, **article_score(ctx, doc, today)}
        if not include_all and row["score"] >= threshold:
            continue
        if row["snapshot"] and not include_snapshots:
            snapshots_skipped += 1
            continue
        rows.append(row)
    rows.sort(key=lambda row: (row["score"], row["path"]))
    return {
        "schema": "knowmade/freshness-score/v1",
        "root": str(ctx.root),
        "today": today.isoformat(),
        "threshold": threshold,
        "threshold_source": source,
        "half_lives": dict(HALF_LIVES),
        "articles": rows,
        "snapshots_skipped": snapshots_skipped,
        "problems": problems,
    }


def _print_score(payload: dict[str, Any]) -> None:
    print(f"Freshness threshold {payload['threshold']} ({payload['threshold_source']}); half-lives hot 30, warm 90, cold 365.")
    for problem in payload["problems"]:
        print(f"  ignored: {problem}")
    if not payload["articles"]:
        print("No article below the threshold.")
    for row in payload["articles"]:
        factors = ", ".join(f"{name} {value}" for name, value in sorted(row["factors"].items()))
        print(f"  {row['score']:>3}  {row['path']}  {row['volatility']} ({factors})")
    if payload["snapshots_skipped"]:
        print(f"{payload['snapshots_skipped']} snapshot article(s) left out (--include-snapshots).")


def _print_report(payload: dict[str, Any]) -> None:
    windows = ", ".join(
        f"{volatility} {spec['days']}" + ("" if spec["source"] == "default" else f" ({spec['source']} config.md)")
        for volatility, spec in payload["windows"].items()
    )
    print(f"Freshness windows (days): {windows}")
    for problem in payload["problems"]:
        print(f"  ignored: {problem}")
    label = "stale" if payload["due"] == 0 else f"due within {payload['due']} days"
    if not payload["articles"]:
        print(f"No article is {label}.")
    else:
        print(f"{len(payload['articles'])} article(s) {label}:")
        for row in payload["articles"]:
            marker = " snapshot" if row["snapshot"] else ""
            print(
                f"  {row['due_in']:>5}  {row['path']}  {row['volatility']}{marker}, "
                f"verified {row['verified']} (age {row['age']}, window {row['window']})"
            )
    if payload["snapshots_skipped"]:
        print(f"{payload['snapshots_skipped']} snapshot article(s) past their window left out (--include-snapshots).")
    for path in payload["undated"]:
        print(f"  undated: {path}")


def run(args: argparse.Namespace) -> int:
    wiki_root = resolve_wiki_root_args(args)
    if not wiki_root.is_dir():
        print(f"freshness report: no wiki root at {wiki_root}", file=sys.stderr)
        return 2
    if is_hub(wiki_root):
        print(
            f"freshness report: {wiki_root} is a hub; pass --wiki <slug> or --path to a topic",
            file=sys.stderr,
        )
        return 2
    if args.freshness_command == "score":
        if args.threshold is not None and not 0 <= args.threshold <= 100:
            print("freshness score: --threshold must be 0-100", file=sys.stderr)
            return 2
        payload = score_payload(
            wiki_root, threshold=args.threshold, include_all=args.all, include_snapshots=args.include_snapshots
        )
        printer = _print_score
    else:
        if args.due < 0:
            print("freshness report: --due must be zero or more days", file=sys.stderr)
            return 2
        payload = report_payload(wiki_root, due=args.due, include_snapshots=args.include_snapshots)
        printer = _print_report
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        printer(payload)
    return 0


FRESHNESS_HELP = "List compiled articles due for refresh under the configured freshness windows."


def register_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Register ``freshness`` on a parent ``llm-wiki`` parser; dispatch through :func:`run`."""
    freshness = subparsers.add_parser("freshness", help=FRESHNESS_HELP, description=FRESHNESS_HELP)
    sub = freshness.add_subparsers(dest="freshness_command", required=True)
    report = sub.add_parser(
        "report",
        help="List articles past (or within --due days of) their volatility window.",
        description=(
            "Read-only. Windows come from freshness_hot_days, freshness_warm_days and "
            "freshness_cold_days in config.md (a topic inherits HUB/config.md's), default "
            "30/180/365. Articles with freshness: snapshot are left out unless "
            "--include-snapshots. Exit 0, or 2 when the root is missing or is a hub."
        ),
    )
    report.add_argument("--path", help="Report on the wiki root at this path.")
    add_scope_arguments(
        report,
        local_help="Report on ./.wiki in the current directory.",
        public_help="Resolve --wiki <slug> to the public sibling without -PRIVATE.",
        include_archived_help="Allow --wiki to resolve an archived topic.",
    )
    report.add_argument(
        "--due",
        type=int,
        default=0,
        metavar="N",
        help="List articles with fewer than N days left in their window (default 0: already stale).",
    )
    report.add_argument(
        "--include-snapshots", action="store_true", help="Also list freshness: snapshot articles."
    )
    report.add_argument("--json", action="store_true", help="Print JSON.")
    score = sub.add_parser(
        "score",
        help="Composite 0-100 freshness score; list articles below freshness_threshold.",
        description=(
            "Read-only. librarian.md's four 0-25 dimensions with volatility half-lives (hot 30, "
            "warm 90, cold 365 days); compiled-from: conversation rebased on two. Threshold from "
            "freshness_threshold in config.md (a topic inherits HUB/config.md's), default 70. "
            "Exit 0, or 2 when the root is missing or is a hub."
        ),
    )
    score.add_argument("--path", help="Score the wiki root at this path.")
    add_scope_arguments(
        score,
        local_help="Score ./.wiki in the current directory.",
        public_help="Resolve --wiki <slug> to the public sibling without -PRIVATE.",
        include_archived_help="Allow --wiki to resolve an archived topic.",
    )
    score.add_argument("--threshold", type=int, metavar="N", help="Override freshness_threshold (0-100).")
    score.add_argument("--all", action="store_true", help="List every article, not only those below the threshold.")
    score.add_argument("--include-snapshots", action="store_true", help="Also list freshness: snapshot articles.")
    score.add_argument("--json", action="store_true", help="Print JSON.")
    freshness.set_defaults(func=run)
    return freshness
