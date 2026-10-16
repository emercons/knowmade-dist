"""Engine and corpus compatibility reporting for the deterministic checker.

The engine and the wikis it judges live in different repositories by design, so
the coupling between them is a declared contract rather than a shared tree. The
contract now has three independent forms:

``knowmade_rule_set``
    Which versioned corpus rules the wiki is written against. This is the
    authoritative portable compatibility form and follows the N-2 policy.

``knowmade_version`` / ``llm_wiki_version``
    Package/protocol version declaration retained for compatibility with
    existing consumers. Package releases no longer define corpus lint semantics.
    ``knowmade_version`` is the unambiguous key and must equal the running
    version; when present it wins and ``llm_wiki_version`` is ignored. The
    older ``llm_wiki_version`` also accepts the running upstream baseline (the
    two trailing components behind a leading ``0.``: ``0.5.0.25.1`` is based
    on upstream ``0.25.1``), so a corpus that follows the upstream convention
    can be drift-clean.

``knowmade_checker_sha``
    Which exact checker build validated the corpus. Compared by prefix so a
    short SHA works.

``llm_wiki_checker_repo`` / ``llm_wiki_checker_sha``
    The upstream checker pin. Informational: reported as ``declared_upstream``
    and never compared.

All of these are read from the frontmatter block of ``config.md``, the same
block the rule-set declaration comes from (``rules.config_fields``); text in the
body is never a declaration.

A current or N-1/N-2 rule-set declaration satisfies the engine contract. Older,
future, or invalid rule-set declarations drift. A wiki that declares no form at
all remains ``undeclared`` for backwards compatibility and is judged by the
current rule set.

The engine's own identity (version, commit, rule set) comes first from the
generated ``_build.py`` next to this module, which the sync scripts write into
every packaged copy. The upward plugin-manifest search is the fallback for a
copy without one. The commit is the one exception: a source checkout always
asks git, so a developer sees the live commit rather than the one embedded by
the last sync.
"""

from __future__ import annotations

import functools
import importlib.util
import json
import re
import subprocess
from pathlib import Path
from typing import Any

try:
    from . import rules as _rules
except ImportError:  # executed directly as a script, not imported as a package
    import rules as _rules

CHECKER_SHA_KEY = "knowmade_checker_sha"
CHECKER_REPO_KEY = "knowmade_checker_repo"
CHECKER_VERSION_KEY = "llm_wiki_version"
KNOWMADE_VERSION_KEY = "knowmade_version"
UPSTREAM_REPO_KEY = "llm_wiki_checker_repo"
UPSTREAM_SHA_KEY = "llm_wiki_checker_sha"
_SHA = re.compile(r"^[0-9a-f]{7,40}$")
_VERSION = re.compile(r"^[0-9][0-9A-Za-z.+-]*$")

_PACKAGE_DIR = Path(__file__).resolve().parent

# The package is vendored into several layouts: the source checkout keeps it at
# ``scripts/knowmade_control`` next to ``claude-plugin/``, while the runtime
# mirrors copy it under a plugin root's ``bin/``. Each ancestor of the package is
# tried against these manifest forms, in this order, and the first hit wins.
_PLUGIN_ROOT_FORMS = (
    (".claude-plugin", "plugin.json"),
    (".codex-plugin", "plugin.json"),
)
_SOURCE_CHECKOUT_FORM = ("claude-plugin", ".claude-plugin", "plugin.json")
_MANIFEST_FORMS = (*_PLUGIN_ROOT_FORMS, _SOURCE_CHECKOUT_FORM)


@functools.lru_cache(maxsize=None)
def _locate_manifest() -> tuple[Path, Path, bool] | None:
    """Find the nearest plugin manifest above the package directory.

    Returns ``(manifest, root, is_source_checkout)`` or None when no ancestor up
    to the filesystem root carries a manifest. The result is cached because the
    package never moves while the interpreter runs.
    """
    for ancestor in (_PACKAGE_DIR, *_PACKAGE_DIR.parents):
        for form in _MANIFEST_FORMS:
            candidate = ancestor.joinpath(*form)
            if candidate.is_file():
                return candidate, ancestor, form is _SOURCE_CHECKOUT_FORM
    return None


_BUILD_MODULE = _PACKAGE_DIR / "_build.py"
_BUILD_COMMIT_UNKNOWN = "unknown"


@functools.lru_cache(maxsize=None)
def build_identity() -> dict[str, Any] | None:
    """Return the generated build identity shipped next to this module, or None.

    ``_build.py`` is loaded from its path rather than imported by name so the
    copy consulted is always the one beside ``engine.py``, whether the package
    is imported or run as a script. A missing or malformed module is simply
    absent: every caller falls back to the manifest search.
    """
    if not _BUILD_MODULE.is_file():
        return None
    spec = importlib.util.spec_from_file_location("knowmade_control._build", _BUILD_MODULE)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:  # noqa: BLE001 - a broken generated file must not break lint
        return None
    version = getattr(module, "VERSION", None)
    commit = getattr(module, "COMMIT", None)
    rule_set = getattr(module, "RULE_SET", None)
    if not isinstance(version, str) or not _VERSION.match(version):
        return None
    if not isinstance(commit, str):
        commit = _BUILD_COMMIT_UNKNOWN
    if isinstance(rule_set, bool) or not isinstance(rule_set, int):
        rule_set = None
    return {
        "version": version,
        "commit": commit if _SHA.match(commit) else _BUILD_COMMIT_UNKNOWN,
        "rule_set": rule_set,
        "generated_by": getattr(module, "GENERATED_BY", None),
        "path": str(_BUILD_MODULE),
    }


def engine_manifest() -> Path | None:
    """Return the plugin manifest this package ships with, or None."""
    located = _locate_manifest()
    return located[0] if located else None


def engine_root() -> Path | None:
    """Return the directory the manifest was resolved against, or None."""
    located = _locate_manifest()
    return located[1] if located else None


def _git_head(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    commit = result.stdout.strip()
    return commit if _SHA.match(commit) else None


def _build_commit() -> str | None:
    build = build_identity()
    if build is None or build["commit"] == _BUILD_COMMIT_UNKNOWN:
        return None
    return build["commit"]


def engine_commit(root: Path | None = None) -> str | None:
    """Return the commit of the engine build, or None when it is unknown.

    A source checkout asks git, so the live commit wins over whatever the last
    sync embedded. A vendored copy under a plugin root belongs to whatever
    repository installed it, so git is never consulted there: the commit is the
    one ``_build.py`` carries, or None when there is no usable build identity.
    """
    if root is not None:
        return _git_head(root)
    located = _locate_manifest()
    if located is not None:
        _manifest, checkout, is_source = located
        if is_source and (checkout / ".git").exists():
            return _git_head(checkout) or _build_commit()
    return _build_commit()


def manifest_version(manifest: Path | None = None) -> str | None:
    """Return the version declared by the plugin manifest, or None."""
    manifest = manifest or engine_manifest()
    if manifest is None:
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    version = data.get("version")
    return version if isinstance(version, str) and version else None


def engine_version(manifest: Path | None = None) -> str | None:
    """Return the engine's package version, or None when it cannot be read.

    The generated build identity wins; the manifest is the fallback. An
    explicit ``manifest`` argument keeps the manifest-only reading for callers
    that ask about a specific file.
    """
    if manifest is not None:
        return manifest_version(manifest)
    build = build_identity()
    if build is not None:
        return build["version"]
    return manifest_version()


def engine_rule_set() -> int:
    """Return the corpus rule set this engine build declares.

    ``_build.py`` is generated from ``rules.CURRENT_RULE_SET`` by the same sync
    that copies the package, so the two agree in any synchronized tree; the
    module constant is the fallback for a copy without a build identity.
    """
    build = build_identity()
    if build is not None and build["rule_set"] is not None:
        return build["rule_set"]
    return _rules.CURRENT_RULE_SET


def _scalar(fields: dict[str, Any] | None, key: str) -> str | None:
    """A scalar frontmatter value as text, or None for absent, empty or list values."""
    if not fields:
        return None
    value = fields.get(key)
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    text = str(value).strip()
    return text or None


def declared_field(wiki_root: Path, key: str) -> str | None:
    """Return a scalar field declared in the wiki's config.md frontmatter."""
    return _scalar(_rules.config_fields(wiki_root), key)


def declared_commit(wiki_root: Path) -> str | None:
    value = declared_field(wiki_root, CHECKER_SHA_KEY)
    return value if value and _SHA.match(value) else None


def declared_version(wiki_root: Path) -> str | None:
    value = declared_field(wiki_root, CHECKER_VERSION_KEY)
    return value if value and _VERSION.match(value) else None


def upstream_baseline(version: str | None) -> str | None:
    """Return the upstream llm-wiki version a Knowmade version is based on.

    The two trailing components are the absorbed upstream baseline
    (COMPATIBILITY.md, "Upstream Merge Semantics"): ``0.5.0.25.1`` is based on
    upstream ``0.25.1``. A version that does not have the Knowmade shape has no
    baseline.
    """
    if not version:
        return None
    parts = version.split(".")
    if len(parts) < 5 or not (parts[-2].isdigit() and parts[-1].isdigit()):
        return None
    return f"0.{parts[-2]}.{parts[-1]}"


def _version_form(
    fields: dict[str, Any] | None, running: str | None
) -> tuple[str | None, str | None, str]:
    """Return ``(declared, key, form)`` for the package-version declaration.

    ``knowmade_version`` wins when present and must equal the running version.
    ``llm_wiki_version`` also matches the running upstream baseline.
    """
    for key in (KNOWMADE_VERSION_KEY, CHECKER_VERSION_KEY):
        value = _scalar(fields, key)
        if value and _VERSION.match(value):
            if key == KNOWMADE_VERSION_KEY:
                return value, key, _compare(value, running, prefix=False)
            form = _compare(value, running, prefix=False)
            if form == "drift" and value == upstream_baseline(running):
                form = "match"
            return value, key, form
    return None, None, "undeclared"


def _compare(declared: str | None, running: str | None, *, prefix: bool) -> str:
    """Classify one legacy contract form: undeclared, unknown, match or drift."""
    if declared is None:
        return "undeclared"
    if running is None:
        return "unknown"
    if declared == running:
        return "match"
    if prefix and (running.startswith(declared) or declared.startswith(running)):
        return "match"
    return "drift"


def _engine_checkout() -> Path | None:
    """The git checkout this engine runs from, or None for a vendored copy."""
    located = _locate_manifest()
    if located is None:
        return None
    _manifest, checkout, is_source = located
    return checkout if is_source and (checkout / ".git").exists() else None


def _git_lines(checkout: Path, *args: str) -> tuple[int, str] | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(checkout), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.returncode, result.stdout.strip()


def commits_behind(declared: str, running: str) -> int | None:
    """How many commits ``declared`` is behind ``running``, or None.

    None when the engine has no git checkout, the declared commit is unknown to
    it, or ``declared`` is not an ancestor of ``running`` (diverged or ahead).
    """
    checkout = _engine_checkout()
    if checkout is None:
        return None
    ancestor = _git_lines(checkout, "merge-base", "--is-ancestor", declared, running)
    if ancestor is None or ancestor[0] != 0:
        return None
    count = _git_lines(checkout, "rev-list", "--count", f"{declared}..{running}")
    if count is None or count[0] != 0 or not count[1].isdigit():
        return None
    return int(count[1])


def _rule_form(status: str) -> str:
    if status in {"match", "supported"}:
        return "match"
    if status == "undeclared":
        return "undeclared"
    return "drift"


def contract_state(wiki_root: Path) -> dict[str, Any]:
    """Describe the engine/wiki compatibility contract for one wiki root.

    Current and supported older rule sets satisfy the contract. Unsupported
    rule-set declarations are drift even when a legacy package/commit pin still
    matches, because the corpus rules are the authoritative on-disk contract.

    Rule-set drift is reported exactly once, by
    ``rule_lint.check_rule_set_contract``, at a severity derived from the
    compatibility ladder. It stays visible here in ``forms["rules"]``,
    ``rule_set_status`` and ``effective_rule_set``, but it does not feed
    ``drifted`` or ``state``: those drive the legacy commit/version warning, and
    counting the rules form in them would report the same declaration twice with
    two severities.
    """
    commit = engine_commit()
    version = engine_version()
    declared_sha = declared_commit(wiki_root)
    declared_ver, version_key, version_form = _version_form(
        _rules.config_fields(wiki_root), version
    )
    declared_rules, rule_present = _rules.declared_rule_set(wiki_root)
    rule_status = (
        "invalid"
        if rule_present and declared_rules is None
        else _rules.rule_set_status(declared_rules)
    )

    forms = {
        "commit": _compare(declared_sha, commit, prefix=True),
        "version": version_form,
        "rules": _rule_form(rule_status),
    }
    legacy_forms = {name: value for name, value in forms.items() if name != "rules"}
    if "drift" in legacy_forms.values():
        state = "drift"
    elif "unknown" in legacy_forms.values():
        state = "unknown"
    elif "match" in forms.values():
        state = "match"
    else:
        state = "undeclared"

    if rule_status == "undeclared":
        effective_rules = _rules.CURRENT_RULE_SET
    elif rule_status in {"match", "supported"}:
        effective_rules = declared_rules
    else:
        effective_rules = None

    build = build_identity()
    state_doc: dict[str, Any] = {
        "commit": commit,
        "version": version,
        "rule_set": engine_rule_set(),
        "identity_source": "build" if build is not None else "manifest",
        "supported_rule_sets": list(_rules.SUPPORTED_RULE_SETS),
        "minimum_supported_rule_set": _rules.MIN_SUPPORTED_RULE_SET,
        "declared": declared_sha,
        "declared_version": declared_ver,
        "declared_rule_set": declared_rules,
        "rule_set_status": rule_status,
        "effective_rule_set": effective_rules,
        "declared_repo": declared_field(wiki_root, CHECKER_REPO_KEY),
        "declared_version_key": version_key,
        "upstream_baseline": upstream_baseline(version),
        "forms": forms,
        "state": state,
        "drifted": sorted(
            form for form, value in legacy_forms.items() if value == "drift"
        ),
    }
    # Optional keys, present only when there is something to say.
    upstream = {
        "repo": declared_field(wiki_root, UPSTREAM_REPO_KEY),
        "sha": declared_field(wiki_root, UPSTREAM_SHA_KEY),
    }
    if upstream["repo"] or upstream["sha"]:
        state_doc["declared_upstream"] = upstream
    if forms["commit"] == "drift" and declared_sha and commit:
        behind = commits_behind(declared_sha, commit)
        if behind is not None:
            state_doc["behind"] = behind
    return state_doc


def drift_summary(state: dict[str, Any]) -> str:
    """Render mismatched contract forms as one human-readable clause."""
    parts: list[str] = []
    if "version" in state["drifted"]:
        parts.append(
            f"declares engine version {state['declared_version']} "
            f"but this lint is version {state['version']}"
        )
    if "commit" in state["drifted"]:
        clause = (
            f"declares checker {state['declared']} "
            f"but this lint is running {state['commit']}"
        )
        behind = state.get("behind")
        if behind is not None:
            noun = "commit" if behind == 1 else "commits"
            clause += f" (declared engine is an ancestor, {behind} {noun} behind)"
        parts.append(clause)
    return "; ".join(parts)
