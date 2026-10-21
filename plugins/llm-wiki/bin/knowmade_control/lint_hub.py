"""Root-level lint checks: the hub root, and the repository around any root.

What a hub root is judged by (``HUB_STEPS``): its required files, the
specialist library, the ``wikis.json`` registry (C19, including the rule-set
findings of emercons/knowmade#116), a slug filed both active and archived
(C19), the hub ``_index.md`` against it (C24,
knowmade#114), the private-topic ignore rules, the git-tracked paths under a
``-PRIVATE`` directory (C25, knowmade#112) and the stray topic store (C20). Two checks run on every root because they judge the
repository and the contract around it, not a file: the engine and rule-set
contract (C22) and tracked machine state (C21, knowmade#98). The ignore
helpers these use (``STATE_IGNORE_RULES``, ``ensure_state_ignore_rules``,
``git_ignores_path``, ``is_inside_git_work_tree``) belong to ``hub`` and are
only imported here. Split out of ``lint`` (emercons/knowmade#105 R3).
"""

from __future__ import annotations

from pathlib import Path

from . import engine as _engine
from . import fsio as _fsio
from . import hub_index as _hub_index
from . import identity as _identity
from . import privacy as _privacy
from . import rule_lint as _rule_lint
from . import rules as _rules
from .common import PRIVATE_SUFFIX, is_under, write_registry
from .hub import (
    PRIVATE_IGNORE_RULE,
    append_private_ignore_rule,
    ensure_state_ignore_rules,
    hub_ignores_private_topic,
    is_inside_git_work_tree,
    machine_state_files,
    missing_state_ignore_rules,
    state_ignore_repair_hint,
)
from .lint_report import LintContext, Step
from .roots import (
    expand_leading_tilde,
    initialized_wiki_root,
    is_archived_registry_entry,
    is_hub,
    resolve_registry_path,
)
from .specialists import collect_specialist_issues, specialist_library_path


PRIVATE_IGNORE_PROBE_SLUG = f"knowmade-ignore-probe{PRIVATE_SUFFIX}"


def check_hub_structure(ctx: LintContext) -> None:
    for name in ["wikis.json", "_index.md", "log.md", "topics"]:
        path = ctx.root / name
        if not path.exists():
            ctx.issue("critical", "Required hub path is missing.", path, check="C1")


def check_hub_registry(ctx: LintContext) -> None:
    registry = ctx.root / "wikis.json"
    if not registry.exists():
        return
    try:
        data = _fsio.read_json_object(registry, "wiki registry")
    except _fsio.JSONReadError as exc:
        ctx.issue("critical", str(exc), registry, check="C19")
        return

    changed = False
    default_path = data.get("default")
    if isinstance(default_path, str) and default_path not in {"<HUB>", "HUB", "."}:
        if ctx.fix_allowed("registry", registry, "rewrite the default to <HUB>", check="C19"):
            data["default"] = "<HUB>"
            ctx.fixed("Rewrote hub registry default to <HUB>.")
            changed = True
        else:
            ctx.issue(
                "suggestion",
                "Hub registry default should use the portable <HUB> token.",
                registry,
                fixable=True,
                check="C19",
                fix_class="registry",
            )

    wikis = data.get("wikis")
    if not isinstance(wikis, dict):
        return
    for slug, entry in sorted(wikis.items()):
        if not isinstance(entry, dict):
            continue
        if slug.endswith(PRIVATE_SUFFIX) and entry.get("description"):
            _rule_lint.issue_versioned(
                ctx,
                _rules.CURRENT_RULE_SET,
                f"Registry entry {slug} carries a description: a private topic is registered "
                "by slug and path only, because wikis.json is committed with the hub "
                "(private-registry-description). Remove the description key.",
                registry,
                check="C19",
            )
        raw_path = entry.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            continue
        if slug == "hub":
            if raw_path not in {"<HUB>", "HUB", "."}:
                if ctx.fix_allowed(
                    "registry", registry, "rewrite the hub entry path to <HUB>", check="C19"
                ):
                    entry["path"] = "<HUB>"
                    ctx.fixed("Rewrote hub registry entry path to <HUB>.")
                    changed = True
                else:
                    ctx.issue(
                        "suggestion",
                        "Hub registry entry should use portable path <HUB>.",
                        registry,
                        fixable=True,
                        check="C19",
                        fix_class="registry",
                    )
            continue

        relative_path = (
            f"topics/.archive/{slug}" if is_archived_registry_entry(entry) else f"topics/{slug}"
        )
        hub_topic = ctx.root / relative_path
        if initialized_wiki_root(hub_topic) and raw_path != relative_path:
            if ctx.fix_allowed(
                "registry", registry, f"rewrite the path of {slug} to {relative_path}", check="C19"
            ):
                entry["path"] = relative_path
                ctx.fixed(f"Rewrote hub-owned registry path for {slug} to {relative_path}.")
                changed = True
            else:
                ctx.issue(
                    "suggestion",
                    f"Hub-owned wiki path should be portable: use {relative_path}.",
                    registry,
                    fixable=True,
                    check="C19",
                    fix_class="registry",
                )
            continue

        resolved = resolve_registry_path(raw_path, ctx.root)

        # Identity beats path: an entry carrying an id can be verified against the
        # wiki it resolves to, so a moved or replaced directory is a detectable
        # error rather than a silent write to the wrong copy.
        mismatch = _identity.verify_entry(entry.get("id"), resolved)
        if mismatch:
            ctx.issue("critical", f"Registry entry {slug} {mismatch}.", registry, check="C19")

        if is_private_placeholder(resolved):
            _rule_lint.issue_versioned(
                ctx,
                _rules.CURRENT_RULE_SET,
                f"Registry entry {slug} points at a private placeholder (README only): "
                "the private wiki is not checked out here.",
                registry,
                check="C19",
            )
        if not expand_leading_tilde(raw_path).is_absolute():
            # A path the registry spells relative to the hub (``topics/x``,
            # ``<HUB>/x``, ``../x``). It must name something inside the hub.
            if not is_under(resolved, ctx.root):
                kind = "exists" if resolved.exists() else "does not exist"
                ctx.issue(
                    "info" if resolved.exists() else "warning",
                    f"Registry path for {slug} is a relative path that escapes the hub "
                    f"and {kind}: {raw_path}.",
                    registry,
                    check="C19",
                )
            elif not resolved.exists():
                _rule_lint.issue_versioned(
                    ctx,
                    _rules.CURRENT_RULE_SET,
                    f"Registry path for {slug} does not exist: {raw_path} "
                    "(registry-path-missing).",
                    registry,
                    check="C19",
                )
        elif not is_under(resolved, ctx.root):
            if resolved.exists():
                ctx.issue(
                    "info",
                    f"Registry path for {slug} is an external local absolute path that exists: {raw_path}.",
                    registry,
                    check="C19",
                )
            else:
                ctx.issue(
                    "warning",
                    f"Registry path for {slug} is an external local absolute path that does not exist: {raw_path}.",
                    registry,
                    check="C19",
                )

    if changed:
        write_registry(ctx.root, data)


# The archive/active collision check is new in rules/3 and walks the ladder
# (info now, warning in the next rule set).
ARCHIVE_COLLISION_INTRODUCED_IN = 3


def check_archive_collisions(ctx: LintContext) -> None:
    """Report a topic slug that exists in both ``topics/`` and ``topics/.archive/`` (C19).

    ``archive restore`` refuses an existing ``topics/<slug>`` and ``archive
    topic`` refuses an existing ``topics/.archive/<slug>``, so a slug filed in
    both places can be neither restored nor archived again until a human
    chooses which copy stays. Lint never chooses: there is no repair. The
    comparison is by directory name on disk, so it also finds a copy the
    registry does not know. The finding is new in rules/3 (info there,
    ``rule_lint.issue_versioned``).
    """
    archive = ctx.root / "topics" / ".archive"
    try:
        archived = sorted(child.name for child in archive.iterdir() if child.is_dir())
    except OSError:
        return
    for slug in archived:
        live = ctx.root / "topics" / slug
        if not live.is_dir():
            continue
        _rule_lint.issue_versioned(
            ctx,
            ARCHIVE_COLLISION_INTRODUCED_IN,
            f"Topic {slug} exists both in topics/{slug} and in topics/.archive/{slug} "
            "(archive-active-collision): it can be neither restored nor archived again. "
            "Compare the two copies and remove or rename one; lint never chooses.",
            live,
            check="C19",
        )


PLACEHOLDER_FILES = {"README.md", ".gitkeep"}


def is_private_placeholder(path: Path) -> bool:
    """True for a ``*-PRIVATE`` directory that is only a tracked anchor.

    A hub keeps ``topics/<slug>-PRIVATE/README.md`` so the sibling is visible on
    machines where the private wiki is not checked out. With no ``config.md``
    and no ``_index.md`` it is not a wiki, and judging it as one yields
    structure criticals that carry no information.
    """
    if not path.name.endswith(PRIVATE_SUFFIX):
        return False
    try:
        if not path.is_dir():
            return False
        names = {child.name for child in path.iterdir()}
    except OSError:
        return False
    return bool(names) and names <= PLACEHOLDER_FILES


def check_hub_private_ignore(ctx: LintContext) -> None:
    """A hub inside a git work tree must ignore private topics in both locations.

    Only hubs inside a git work tree are judged; a plain directory cannot
    commit anything. "Inside" is git's answer from the hub directory, so a hub
    nested in a dotfiles or project repository is judged like a repository
    root (knowmade#87), and the probes are asked of git from the hub directory
    too. Both lifecycle locations are probed: the live
    `topics/<slug>-PRIVATE/` and the archived `topics/.archive/<slug>-PRIVATE/`
    (the legacy `topics/*-PRIVATE/` rule covers only the first).

    Severity is `critical` on introduction. COMPATIBILITY.md allows a new rule
    to bypass the info/warning/critical ladder for privacy failures, and a
    private topic that becomes committable is one; the check also only fires
    for hubs in a git work tree and `--fix` repairs it in place. The exception
    is recorded in COMPATIBILITY.md ("Documented ladder exceptions").
    """
    if not is_inside_git_work_tree(ctx.root):
        return
    probes = {
        "live": f"topics/{PRIVATE_IGNORE_PROBE_SLUG}",
        "archived": f"topics/.archive/{PRIVATE_IGNORE_PROBE_SLUG}",
    }
    gaps = [label for label, probe in probes.items() if not hub_ignores_private_topic(ctx.root, probe)]
    if not gaps:
        return
    gitignore = ctx.root / ".gitignore"
    if ctx.fix_allowed("gitignore", gitignore, f"append {PRIVATE_IGNORE_RULE}", check="C19"):
        append_private_ignore_rule(ctx.root)
        ctx.fixed(f"Appended `{PRIVATE_IGNORE_RULE}` to .gitignore so live and archived private topics stay ignored.")
        return
    locations = {
        "live": f"live private topics (topics/<slug>{PRIVATE_SUFFIX}/)",
        "archived": f"archived private topics (topics/.archive/<slug>{PRIVATE_SUFFIX}/)",
    }
    ctx.issue(
        "critical",
        f"Hub .gitignore does not ignore {' or '.join(locations[label] for label in gaps)}; "
        f"they are committable in this git work tree"
        + ("" if "live" in gaps else f" once `archive topic --private` moves them")
        + f". Add `{PRIVATE_IGNORE_RULE}` or run `llm-wiki lint --fix`. "
        "Critical on introduction: a committable private topic is a privacy failure "
        "(COMPATIBILITY.md severity-ladder exception).",
        gitignore,
        fixable=True,
        check="C19",
        fix_class="gitignore",
    )


# The hub-index drift check is new in rules/3: info on the current set, and it
# walks the ladder from there.
HUB_INDEX_INTRODUCED_IN = 3


def check_hub_index(ctx: LintContext) -> None:
    """Report a hub ``_index.md`` that no longer matches ``wikis.json`` (C24, knowmade#114).

    The comparison is ``hub_index.index_drift``: read-only, and blind to the
    ``Last updated`` date, so a day passing is never drift. The repair is the
    writer, `llm-wiki hub index --write`, so there is no lint fixer. A missing
    ``_index.md`` is C1's critical and an unreadable registry is C19's, so
    neither is reported a second time here.
    """
    if not is_hub(ctx.root) or not (ctx.root / "_index.md").is_file():
        return
    try:
        data = _fsio.read_json_object(ctx.root / "wikis.json", "wiki registry")
    except _fsio.JSONReadError:
        return
    if "wikis" in data and not isinstance(data["wikis"], dict):
        return
    for problem in _hub_index.index_drift(ctx.root, data):
        _rule_lint.issue_versioned(
            ctx,
            HUB_INDEX_INTRODUCED_IN,
            f"Hub _index.md differs from wikis.json: {problem}. "
            "Run `llm-wiki hub index --write`.",
            ctx.root / "_index.md",
            check="C24",
        )


def _private_anchors(root: Path) -> list[str]:
    """Basenames the hub tolerates under a ``-PRIVATE`` directory (``private_anchors``).

    A comma-separated string or a YAML list in the hub ``config.md``
    frontmatter, read through the one config reader; absent means none.
    """
    fields = _rules.config_fields(root) or {}
    raw = fields.get("private_anchors")
    items = raw if isinstance(raw, (list, tuple)) else str(raw or "").split(",")
    return [str(item).strip() for item in items if str(item).strip()]


def check_tracked_private(ctx: LintContext) -> None:
    """Report git-tracked paths under a ``-PRIVATE`` directory (C25, knowmade#112).

    ``check_hub_private_ignore`` cannot see a file added with ``git add -f`` or
    tracked before its ignore rule existed. Each such path is `critical`, a
    documented privacy exception to the ladder (COMPATIBILITY.md): a tracked
    private file is already committable and, once pushed, public. Hub roots
    carry no rule-set declaration, so the severity is stated directly, as in
    ``check_hub_private_ignore``. Only hubs inside a git work tree are judged;
    nothing is repaired. When git cannot list the files the check says so as a
    warning: an unlisted tree is not a clean one.
    """
    if not is_hub(ctx.root) or not is_inside_git_work_tree(ctx.root):
        return
    tracked = _privacy.tracked_private_paths(ctx.root, _private_anchors(ctx.root))
    if tracked is None:
        ctx.issue(
            "warning",
            "git could not list tracked files; the tracked-private check did not run.",
            ctx.root,
            check="C25",
        )
        return
    for rel in tracked:
        ctx.issue(
            "critical",
            "Git tracks a path under a -PRIVATE directory (private-tracked); "
            "`git rm --cached` it after the owner agrees, or list the basename in "
            "`private_anchors`.",
            ctx.root / rel,
            check="C25",
        )


# Directories an older source-lifecycle default created directly under
# `topics/`, shared by every topic and outside every `-PRIVATE` ignore rule.
STRAY_TOPIC_STORE_DIRS = (".wiki-local", "sources")


def check_hub_topic_store_siblings(ctx: LintContext) -> None:
    """Warn about source-store directories sitting beside the topics (knowmade#89).

    Before the fix, a hub topic without an explicit `source_store` resolved its
    store to `topics/.wiki-local/` and `topics/sources/`. Originals and text of
    a `-PRIVATE` topic could land there, committable. `warning` on introduction
    rather than `info`: a documented privacy exception to the ladder (hub roots
    carry no rule-set declaration, and a private original outside its topic is
    a privacy failure); no repair is automatic because the records name their
    paths.
    """
    topics = ctx.root / "topics"
    for name in STRAY_TOPIC_STORE_DIRS:
        stray = topics / name
        if not stray.exists():
            continue
        ctx.issue(
            "warning",
            f"Source-store directory topics/{name}/ sits beside the topic wikis, outside every "
            f"`{PRIVATE_SUFFIX}` ignore rule; an older source-lifecycle default wrote originals or "
            "text of any topic here. Move each source into its topic (`<topic>/.wiki-local/`, "
            "`<topic>/sources/`) or set `source_store` explicitly in that topic's config.md.",
            stray,
            check="C20",
        )


# The tracked-machine-state check is new in rules/3 and walks the ladder
# (info now, warning in the next rule set, critical after that).
TRACKED_MACHINE_STATE_INTRODUCED_IN = 3


def check_tracked_machine_state(ctx: LintContext, hub_root: bool) -> None:
    """Report machine-local state that git tracks or would commit (knowmade#98).

    Session state, the specialist registry, audit/librarian run state, editor
    state and local source originals (`hub.STATE_IGNORE_RULES`) carry local
    paths and private slugs. Git is asked from the lint root, so a hub or wiki
    nested in a larger repository is judged on its own subtree. Tracked files
    need `git rm --cached`; untracked ones that no rule ignores are fixed on a
    hub by `--fix`, which appends the missing rules to the hub `.gitignore`.
    """
    if not is_inside_git_work_tree(ctx.root):
        return
    if hub_root and ctx.fixing:
        missing = missing_state_ignore_rules(ctx.root)
        if missing and ctx.fix_allowed(
            "gitignore",
            ctx.root / ".gitignore",
            f"append machine-state rules {', '.join(missing)}",
            check="C21",
        ):
            appended = ensure_state_ignore_rules(ctx.root)
            if appended:
                ctx.fixed(f"Appended machine-state ignore rules to .gitignore: {', '.join(appended)}.")
    found = machine_state_files(ctx.root)
    if found is None:
        return
    tracked, committable = found
    if tracked:
        shown = ", ".join(tracked[:5]) + (f" and {len(tracked) - 5} more" if len(tracked) > 5 else "")
        _rule_lint.issue_versioned(
            ctx,
            TRACKED_MACHINE_STATE_INTRODUCED_IN,
            f"Machine-local state is tracked by git: {shown}. It carries local paths, "
            "transcript locations or private slugs; untrack it with `git rm --cached` "
            "and keep it ignored.",
            ctx.root / tracked[0],
            check="C21",
        )
    if committable:
        shown = ", ".join(committable[:5]) + (f" and {len(committable) - 5} more" if len(committable) > 5 else "")
        _rule_lint.issue_versioned(
            ctx,
            TRACKED_MACHINE_STATE_INTRODUCED_IN,
            f"Machine-local state is not ignored and would be committed: {shown}; "
            + (state_ignore_repair_hint(ctx.root) if hub_root else "add the machine-state rules to the repository's .gitignore")
            + ".",
            ctx.root / committable[0],
            fixable=hub_root,
            check="C21",
            fix_class="gitignore",
        )


def check_engine_contract(ctx: LintContext, strict: bool = False) -> None:
    """Report drift between the engine running and the one the wiki declares.

    The engine is versioned independently of every wiki it judges, so a wiki that
    declares an engine version or checker commit is asking to be told when the
    rules move under it. A wiki that declares nothing is not nagged: the contract
    is opt-in, and each declared form is judged separately.

    Drift is a warning by default. An out-of-date declaration is a coordination
    problem, not a protocol violation, and must not block a corpus that is
    otherwise clean. ``--strict-engine`` raises it to critical for a CI lane that
    should refuse to run an engine other than the declared one.

    A ``knowmade_checker_sha`` that differs from the running commit while
    ``knowmade_version`` equals the running version is the same release (the tag
    commit and the packaged build identity cannot be one commit): ``info``, and
    ``--strict-engine`` leaves it alone.
    """
    state = _engine.contract_state(ctx.root)
    ctx.engine = state
    problem = _rules.config_problem(ctx.root)
    if problem is not None:
        # The file that carries the engine contract cannot be read, so the rule
        # set and engine pins it declares are silently ignored. Like a declared
        # rule set outside the window, it cannot be graded by any rule set.
        _rule_lint.issue_versioned(
            ctx,
            _rules.CURRENT_RULE_SET,
            f"config.md has no usable frontmatter: {problem}. The engine contract "
            f"({_rules.RULE_SET_KEY}, engine pins) cannot be read from it.",
            ctx.root / "config.md",
            exceptional_critical=True,
            target_rule_set=_rules.CURRENT_RULE_SET,
            check="C1",
        )
    if state["state"] == "drift":
        ctx.issue(
            "critical" if strict else "warning",
            f"Wiki {_engine.drift_summary(state)}. Upgrade the declaration "
            "deliberately, or run the declared engine.",
            # An inherited pin lives in the hub's config.md: point the finding
            # at the file to edit, not at the topic that declares nothing.
            Path(state["pin_config"]) if state.get("pin_config") else ctx.root / "config.md",
            check="C22",
        )
    note = _engine.equivalence_note(state)
    if note:
        # Same package version, different commit of the same release: not drift.
        ctx.issue(
            "info",
            note,
            Path(state["pin_config"]) if state.get("pin_config") else ctx.root / "config.md",
            check="C22",
        )
    _rule_lint.check_rule_set_contract(ctx)


def check_specialist_library(ctx: LintContext) -> None:
    if not specialist_library_path(ctx.root).exists():
        return
    for issue in collect_specialist_issues(ctx.root):
        path = Path(issue["path"])
        ctx.issue(issue["severity"], issue["message"], path, check="C23")


# What a hub root runs after the checks every root runs (``lint.lint_steps``),
# in run order. Add a later hub-level check here as one more ``Step``.
HUB_STEPS: tuple[Step, ...] = (
    Step("specialist library", check_specialist_library),
    Step(
        "hub registry",
        check_hub_registry,
        fix="rewrite the default, hub and hub-owned topic paths to portable form",
        fix_class="registry",
    ),
    Step("archive collisions", check_archive_collisions),
    Step("hub index", check_hub_index),
    Step(
        "private-topic ignore rules",
        check_hub_private_ignore,
        fix=f"append {PRIVATE_IGNORE_RULE} to .gitignore",
        fix_class="gitignore",
    ),
    Step("tracked private files", check_tracked_private),
    Step("stray topic source store", check_hub_topic_store_siblings),
)
