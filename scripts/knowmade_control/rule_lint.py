"""Rule-set-aware lint compatibility helpers.

This module is intentionally a thin layer over the legacy lint pipeline: old
checker/package drift behavior remains intact while corpus rule-set compatibility
gets an independent N-2 contract.

It is the only reporter of rule-set drift. ``engine.contract_state`` still
exposes the rules form for JSON consumers but keeps it out of the legacy
commit/version warning, so one declaration yields one finding.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import rules


def effective_rule_set_for_wiki(wiki_root: Path) -> int:
    if not wiki_root.is_dir():
        raise ValueError(f"wiki root does not exist or is not a directory: {wiki_root}")
    declared, present = rules.declared_rule_set(wiki_root)
    if present and declared is None:
        raise ValueError("invalid knowmade_rule_set declaration")
    return rules.effective_rule_set(declared)


_EFFECTIVE_ATTR = "_effective_rule_set"


def _effective_for_context(ctx: Any) -> int:
    """Resolve the corpus rule set once per lint run and cache it on ``ctx``."""
    cached = getattr(ctx, _EFFECTIVE_ATTR, None)
    if isinstance(cached, int):
        return cached
    target = effective_rule_set_for_wiki(ctx.root)
    try:
        setattr(ctx, _EFFECTIVE_ATTR, target)
    except AttributeError:  # a context that forbids new attributes just re-resolves
        pass
    return target


def _drift_message(status: str, declared: int | None, supported: str) -> str:
    if status == "invalid":
        return (
            f"Invalid {rules.RULE_SET_KEY} in config.md; expected an integer such as "
            f"{rules.CURRENT_RULE_SET}. Running engine supports {supported}."
        )
    if status == "too-old":
        return (
            f"Wiki declares rules/{declared}, outside the N-2 support window. "
            f"Running engine supports {supported}."
        )
    if status == "future":
        return (
            f"Wiki declares future rules/{declared}, but this engine is rules/{rules.CURRENT_RULE_SET}. "
            "Upgrade the engine before judging this corpus."
        )
    # ``rule_set_status`` yields no other drift value for a contiguous window;
    # keep one defensive wording rather than a branch no input can reach.
    return f"Wiki declares unsupported rules/{declared}. Running engine supports {supported}."


def check_rule_set_contract(ctx: Any) -> None:
    """Emit compatibility findings for the wiki being linted.

    This is the single reporter of rule-set drift. A declaration outside the
    support window cannot be graded by its own rule set (no supported set judges
    it), so the finding is stated by the running engine's set and uses the
    documented exceptional-critical branch of the severity ladder.
    """
    config_path = ctx.root / "config.md"
    declared, present = rules.declared_rule_set(ctx.root)
    status = "invalid" if present and declared is None else rules.rule_set_status(declared)

    if status == "undeclared":
        # Hub roots and other non-corpus containers may be linted without a
        # config.md. Only mutate an initialized corpus that already owns one,
        # and only through the lint fix gate (class ``create``, knowmade#111)
        # when the context has one.
        gate = getattr(ctx, "fix_allowed", None)
        if config_path.is_file() and (
            gate("create", config_path, f"declare rules/{rules.CURRENT_RULE_SET}", check="C22")
            if gate is not None
            else bool(getattr(ctx, "fix", False))
        ):
            try:
                rules.declare_rule_set(ctx.root)
            except ValueError as exc:
                ctx.issue(
                    "warning",
                    f"Could not declare rules/{rules.CURRENT_RULE_SET}: {exc}. "
                    f"Add a YAML frontmatter block to config.md, then re-run lint --fix "
                    f"or `llm-wiki rules declare --path {ctx.root}`.",
                    config_path,
                    check="C22",
                )
            else:
                if hasattr(ctx, "fixed"):
                    ctx.fixed(f"Declared rules/{rules.CURRENT_RULE_SET} in config.md.")
        # Undeclared is deliberately silent without --fix and is judged by the
        # current set for backwards compatibility.
        return

    supported = ", ".join(f"rules/{value}" for value in rules.SUPPORTED_RULE_SETS)
    if status == "supported":
        ctx.issue(
            "suggestion",
            f"Wiki declares rules/{declared}; this engine supports it but rules/{rules.CURRENT_RULE_SET} is newer. "
            f"Upgrade explicitly with `llm-wiki rules declare --path {ctx.root}` after reviewing newer checks.",
            config_path,
            check="C22",
        )
    elif status != "match":
        issue_versioned(
            ctx,
            rules.CURRENT_RULE_SET,
            _drift_message(status, declared, supported),
            config_path,
            exceptional_critical=True,
            target_rule_set=rules.CURRENT_RULE_SET,
            check="C22",
        )
    # A current declaration is silent.


def issue_versioned(
    ctx: Any,
    introduced_in: int,
    message: str,
    path: Path | None = None,
    *,
    check: str,
    fixable: bool = False,
    fix_class: str = "",
    exceptional_critical: bool = False,
    target_rule_set: int | None = None,
    line: int = 0,
) -> None:
    """Emit a stricter lint rule at the severity appropriate for this corpus.

    Existing checks are the rules/1 bootstrap baseline. New strictness must use
    this helper so an older supported corpus is not silently judged by a newer
    severity. Security/privacy/data-loss rules may pass ``exceptional_critical``
    only when the exception is documented in the change that adds the rule.

    ``check`` is the finding's check id (``references/linting.md``), passed
    through to ``ctx.issue``, as is ``fix_class`` for a fixable finding (only
    when set, so a minimal context without the keyword still works).
    ``target_rule_set`` overrides the corpus declaration. It exists for findings
    about the declaration itself (outside the window, so no supported set can
    judge it); ordinary checks must leave it unset. ``line`` is the 1-based line
    of the finding in ``path``, 0 when it has none (passed on only when set).
    """
    if target_rule_set is None:
        try:
            target_rule_set = _effective_for_context(ctx)
        except ValueError:
            return
    severity = rules.severity_for_rule(
        introduced_in,
        target_rule_set,
        exceptional_critical=exceptional_critical,
    )
    if severity is None:
        return
    extra = {"line": line} if line else {}
    if fixable and fix_class:
        ctx.issue(severity, message, path, fixable=True, check=check, fix_class=fix_class, **extra)
    else:
        ctx.issue(severity, message, path, fixable=fixable, check=check, **extra)
