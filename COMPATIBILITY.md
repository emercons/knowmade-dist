# Knowmade Compatibility Policy

This document is the normative compatibility contract for Knowmade. It separates
compatibility concerns that were previously represented by one package version.
The `KM-CMP` compatibility tranche (status in [`BACKLOG.md`](BACKLOG.md))
implements this contract; the last section, "Rule-Set Implementation", records
how.

## Compatibility Surfaces

Knowmade has three versioned surfaces. They may change independently and must not
be collapsed into one semantic meaning.

### 1. Corpus rule set

The corpus rule set defines the structural and lint rules used to judge a wiki
on disk: required structure, frontmatter constraints, source coverage rules,
and other checks that can make an existing corpus pass or fail without changing
its content.

The rule-set version is distinct from the distribution/package version. It is
bumped only when the rules used to judge a corpus become stricter or otherwise
change compatibly enough that a corpus must be able to declare which rules it
expects. Refactors, upstream merges, packaging changes, and documentation-only
changes do not bump the rule set by themselves.

Newly initialized/adopted corpora declare `knowmade_rule_set` in `config.md`.
Older undeclared corpora retain backward-compatible behavior and are judged by
the running engine's current rule set until deliberately declared/migrated.

Migration is recipe-driven: an upstream or older Knowmade corpus reaches the
current layout through the ordered existing commands of the "Deterministic
Upgrade Recipe" in
[`references/migration.md`](claude-plugin/skills/wiki-manager/references/migration.md),
never through a silent rewrite. The recipe leaves every evidence file under
`raw/` and `wiki/` byte-identical; `tests/test-migration-fixtures.sh` proves it
on frozen upstream and Knowmade wikis.

#### Corpus divergence from upstream

Knowmade's corpus rules equal those of the absorbed upstream baseline except
where this list says otherwise. A divergence is recorded here, classified, and
mirrored as one line in `KNOWMADE_VS_LLM_WIKI.md`.

- **Five-point `confidence` scale** (knowmade#121). Lint accepts `high`,
  `medium-high`, `medium`, `medium-low` and `low` (`CONFIDENCE_VALUES` in
  `scripts/knowmade_control/lint_docs.py`); upstream accepts `high`, `medium`, `low`
  and warns on the rest. Classification: a superset of upstream's enum, so it
  makes no Knowmade corpus fail and needs no rule-set bump; a corpus that uses
  an intermediate value gets one upstream *warning* per article. Agents writing
  new articles keep to the three upstream values (`references/compilation.md`);
  the intermediates are for authors who chose them. Proposed upstream as
  UPS-F005, [nvk/llm-wiki#94](https://github.com/nvk/llm-wiki/issues/94)
  (`UPSTREAM_CONTRIBUTIONS.md`).
- **`category: thesis` is not a divergence.** Knowmade accepted it for a time
  (UPS-F004, `8e375f2`) and reverted it: theses are marked `type: thesis`, which
  stock upstream already routes to `wiki/theses/` and exempts from `category`,
  while `category` stays `concept`, `topic` or `reference` in both. No upstream
  proposal is planned, because a wider enum would create a dialect that
  unpatched installations reject (`docs/history/upstream-contributions.md`,
  UPS-F004). Corpora that still carry `category: thesis` fail C2 on both
  checkers and fix it by switching to `type: thesis`.

### 2. Deterministic CLI and protocol surface

The deterministic CLI/protocol surface includes command names, flags, exit
behavior, machine-readable output, state-machine transitions, and contracts that
scripts or agents call directly.

CLI compatibility is not inferred from the corpus rule set. A release may keep
the same rule set while changing CLI behavior, or introduce a new rule set
without changing a stable CLI entry point. Breaking CLI changes require an
explicit migration path or compatibility shim appropriate to the affected
callers.

### 3. Distribution and runtime packaging surface

The distribution surface includes the Knowmade package version, plugin
manifests, generated runtime mirrors, marketplace metadata, install instructions,
and the tagged artifact users install.

The package version identifies a shipped distribution. It does not by itself
identify the corpus rule set or promise that every internal CLI contract changed.
Generated Claude/Codex/OpenCode packaging must remain synchronized with the
canonical source according to `CLAUDE.md`.

Every packaged copy of the deterministic control plane carries a generated
build identity, `knowmade_control/_build.py`, written by the sync scripts into
`scripts/knowmade_control/`, `claude-plugin/bin/`, `plugins/llm-wiki/bin/` and
`plugins/llm-wiki-opencode/bin/`:

- `VERSION` — the package version from `claude-plugin/.claude-plugin/plugin.json`;
- `COMMIT` — the short git hash of `HEAD` when the sync ran, or `"unknown"`
  when git was unavailable;
- `RULE_SET` — the corpus rule set the engine code declares
  (`rules.CURRENT_RULE_SET`), never a separately maintained number;
- `GENERATED_BY` — the sync entry points that own the file.

The engine prefers `_build.py` for its version and rule set and falls back to
the upward plugin-manifest search only when the module is missing, so a
vendored copy with no manifest in any ancestor is still identified. `COMMIT`
is what a packaged runtime reports, since it has no git checkout of its own; a
source checkout keeps asking git so a developer sees the live commit. `COMMIT`
is informational only: in a release commit it names the commit before the
release commit, and the authoritative release identity remains `VERSION` plus
the `v<version>` tag. The mirror-sync tests compare `_build.py` with the
`COMMIT` line filtered out, so a routine commit does not dirty the mirrors.
`llm-wiki engine info --json` reports that identity (version, tag, commit and
whether it came from git or `_build.py`, rule sets, upstream baseline), and
`llm-wiki pin set --knowmade v<version>` pins a wiki or hub by it.

#### Which sha to pin

A consumer that pins a commit (`knowmade_checker_sha`) pins **the release tag
commit** of the version it declares in `knowmade_version`
(`llm-wiki pin resolve v<version>`). A packaged build cannot report that
commit: its `COMMIT` is taken before the release commit exists, so the packaged
build identity (`engine info`, `build_commit`) names an earlier commit of the
same release. The two are therefore **equivalent pins for the same version**:
when `knowmade_version` equals the running version, a `knowmade_checker_sha`
that differs from the running commit is reported as `info`
(`engine.forms.commit` is `equivalent`, `engine.commit_equivalent` says why),
never as a warning, and `--strict-engine` does not escalate it. It is
equivalent when it is the packaged build identity, or, in a source checkout,
an ancestor or descendant of the running commit; a packaged copy has no
history to check and accepts any sha at the declared version. The rule applies
when `knowmade_version` (not `llm_wiki_version` alone) equals the running
version. A source checkout whose git cannot be run fails closed: the warning
stays and says git could not be run. A sha that git can place nowhere in the
running checkout's history, a sha declared without a
matching `knowmade_version`, and a sha declared with another version keep the
`warning` (`critical` under `--strict-engine`). A hub pins one set for every
topic: `knowmade_version` plus either sha.

## Supported Rule-Set Window

Let `N` be the newest corpus rule set supported by the running engine. The normal
support window is **N-2**:

- `N` — current rule set;
- `N-1` — supported;
- `N-2` — supported;
- older than `N-2` — unsupported and must fail clearly rather than being judged
  silently by newer rules;
- newer than `N` — unsupported by that engine and must fail clearly.

A corpus declaring `N-1` or `N-2` is judged by the rules it declares, not by the
newest rules. The engine also emits one suggestion-level upgrade notice naming
the newer available rule set and the explicit upgrade path. The upgrade notice
does not turn an otherwise valid supported corpus into a failure.

Removing a rule set from the supported window is a deliberate compatibility
event. It must be accompanied by removal/update of that rule set's fixture lane,
not by silent drift in current tests.

## Severity And Deprecation Ladder

Knowmade uses the existing severities `info`, `suggestion`, `warning`, and
`critical` for different compatibility roles.

For a newly introduced stricter corpus check, the default progression is:

1. **Introduction:** the check enters as `info` in the rule set that introduces
   it. Existing users can see the future requirement without being broken.
2. **Next rule set:** the same condition may become `warning`.
3. **Following rule set:** it may become `critical` only after having spent the
   preceding supported generations on the deprecation path.

`suggestion` is used for non-failing upgrade guidance, including the notice that
a valid `N-1` or `N-2` corpus can upgrade to a newer rule set.

A new rule may start at `critical` only for an explicit exceptional reason, such
as preventing data loss, unsafe destructive behavior, or a security/privacy
failure. The exception must be recorded in the change/release documentation with
the reason the normal ladder was bypassed.

Severity is evaluated according to the corpus's declared rule set. A rule that
is `critical` in `N` must not retroactively become `critical` when judging a
corpus that validly declares `N-1` or `N-2`.

## Release Semantics

Knowmade uses **tagged, explicitly authorized releases**. The supported release
install path must pin a Knowmade tag rather than follow moving `master`.

A **Knowmade release** is the complete distribution event in which:

1. a human-selected Knowmade package version is present consistently across the
   distribution manifests;
2. a Knowmade version tag identifies the shipped commit;
3. the GitHub release corresponds to that tag/version;
4. supported release install instructions resolve to that tag rather than a
   moving branch; and
5. the release has explicit user authorization under
   `.claude/release-checklist.md`.

Consequently:

- a push or merge to `master` is **not** a release;
- an upstream merge is **not** a release;
- a rule-set bump is **not** by itself a release;
- a manifest/version edit without the authorized tag + GitHub release is release
  preparation, not a shipped release.

The normal Codex release installation records the selected Git ref. Refreshing a
pinned marketplace therefore stays on that release. Moving to another Knowmade
release is deliberate: remove the installed plugin/marketplace and add the
repository again with the new release tag. The install commands live in one
place, [`README.md`](README.md) ("Install" and "Upgrade").

Development instructions may still refer to a local checkout or `master`, but
they must be labeled as development paths rather than the supported release
installation.

## Upstream Merge Semantics

Upstream integration and downstream release are separate events.

**The rule.** An upstream merge records the absorbed baseline in
`KNOWMADE_VS_LLM_WIKI.md` and does not change the Knowmade version. The two
trailing components of the version (`0.4.2.25.1`: the leading `0.4.2` is
Knowmade's own release line, the trailing `25.1` is the upstream baseline) are
set only in an authorized release commit, to the upstream baseline the tree has
absorbed at that point. Manifests, install instructions and the release tag
therefore never name a baseline that is not shipped. `CLAUDE.md` (Upstream
Update Process), `BACKLOG.md` and `.claude/release-checklist.md` point here
instead of restating the rule.

When upstream advances, merge/adapt it promptly. The merge records what the
source tree is compatible with; it does **not** ship the merge to users because
installed releases remain pinned to their authorized tags. After the merge and
tests, shipping that new baseline is a separate decision: select/confirm the
distribution version, prepare tagged install docs, create the version tag, and
publish the matching GitHub release under the authorization gate. There is no
standing upstream-merge exception that implicitly releases it.

## Change Classification

Every compatibility-sensitive change should be classified before shipping:

| Change | Corpus rule set | CLI/protocol | Distribution |
|---|---|---|---|
| Documentation/refactor only | unchanged | unchanged | package bump only if intentionally released |
| New stricter lint/structure rule | bump rule set | usually unchanged | release only when authorized |
| New optional CLI command/flag | usually unchanged | additive change | release only when authorized |
| Removed/renamed CLI behavior | unchanged unless corpus rules also change | compatibility migration required | release only when authorized |
| Upstream merge | bump only if corpus judgment changes | inspect delta | none: record the baseline in `KNOWMADE_VS_LLM_WIKI.md`; not a release |
| Packaging/install change | unchanged unless rules also change | unchanged unless entry points change | distribution surface changes |

A single change may touch more than one row/surface. Review must name all affected
surfaces rather than relying on the package version as a proxy.

## Release Compatibility Record

Compatibility-sensitive changes are recorded here per change set, newest first,
classified by the three surfaces above. An entry stays "unreleased" until the
release commit tags it; the version is then added to the heading.

### v0.8.0.25.1 — Sprint I: Phase 3 brownfield gaps and the Codex plugin rename

**Deterministic CLI and protocol surface — additive: `privacy scan --check
cross-boundary` (`#172 (privacy scan --check cross-boundary: a public root must
not link, cite or source into a -PRIVATE sibling)`, closes `KM-MIG-003`).** New
module `privacy_boundary` (the resolver), `privacy scan --check` gains the
choice `cross-boundary`, and `privacy scan` with no `--check` now runs the
content scan and `cross-boundary` together (`--check tracked-private` is
unchanged and still runs alone). No flag, option or JSON field is removed; a
`notes` array is added to the `--json` payload only when a check was skipped.
A Markdown file of a public root (outside every private location) whose
inline link, reference definition (`[id]: target`), autolink, `href=`/`src=`
attribute, wiki-link (by path, or by a slug only a private file answers to),
`source:`/`sources:`/`origin:` value, inventory path cell or own symlink
resolves (after `os.path.realpath`, existing or not) into a private location
that the file does not live in is a critical `cross-boundary` finding with a
`privacy-<sha16>` id (path, line and the reference as written) and the shared
allow-file semantics; so is a reference to `goals.md` from a file under
`output/`. A private location is a `*-PRIVATE` directory or the directory of a
private registry topic (a `<name>-PRIVATE` slug, wherever its path points, so
the registry-only case is covered for slug links too); files inside one are
never judged, and a private root (a private location in its real path) is not
checked and says so. **Behaviour change to expect: because the default check
set includes the new kind, a public root with such a reference now exits 1
under the default `--fail-on critical`, where it exited 0 before.** An
unreadable hub registry exits 2.

`checkpoint seal` resolves the Markdown links, wiki-links (a slug counts when
only a private topic of the enclosing hub answers to it and no file of the
bundle does) and source paths of a bundle with the same resolver, so a bundle
that refers into a private sibling is `blocked` (exit 2) until the finding is
allowlisted. The bundle's own location in a private directory is not a
crossing. An unreadable enclosing hub registry makes `seal` exit 2 before it
writes anything. `checkpoint verify` does not recompute `cross-boundary`
findings: they depend on where the bundle sits (relative paths, the enclosing
hub's registry and machine overlay), which a recipient cannot reproduce. It
carries the sealed ones over from `privacy-report.json`, still bound to the
sealed content by the file hashes and the report id, so a sealed bundle with an
override verifies after it is copied elsewhere. Checkpoint
privacy scanner version 4 becomes 5: `verify` reports a bundle sealed by
scanner 4 `invalid` with a re-seal message, the rule every earlier scanner bump
followed. Corpus rule set unchanged.

**`#170 (adopt scan/apply --subtree, KM-MIG-012)`, deterministic CLI and
protocol surface, additive.** `adopt scan|apply` and `llm-wiki-adopt` gain a
repeatable `--subtree <dir>`: the walk is limited to those project-relative
directories, `source_path` and `item_id` are unchanged, and every existing
skip/nested-git/corpus/wiki-root rule still applies below them. `adopt apply
--batch-id <existing> --subtree` merges into that batch under the batch lock
instead of refusing: rows already in the manifest keep their state and fields,
new rows are appended as `planned` (staged in copy/move mode), the manifest
and `state.json` are replaced atomically, `state.json` gains an optional
`subtrees` list (union; absent on a whole-project batch) and `events.jsonl` an
`adopt-extend` event. `adopt status` prints the covered subtrees and its
`--json` gains `subtrees`; the apply `--json` gains `subtrees`, `extended`,
`added` and `already_present` when `--subtree` is used. A missing,
project-escaping or wiki-root `--subtree`, a mode that differs from the batch,
and unfinished staging exit 2 with a message, as does a `--subtree` that is, or
lies below, a skip-listed, `--exclude`d, nested-repository or Knowmade-checkout
directory (a whole-project scan never walks into those). `--batch-id` containing
a path separator or `..` is refused (exit 2) for new and extended batches. An
extension journals every move to `events.jsonl` and commits its new rows with
`staging: pending` before the first original moves; `adopt repair` and the
`adopt claim` guard now treat `staging: pending` like `interrupted` (a pending
batch nobody holds the lock for is a crashed one), and staging records a move-mode
original that already sits in `staged/` as moved instead of an error. Batch schema
`knowmade/adoption-batch/v1` is unchanged (readers ignore the new key); without
`--subtree` every command behaves as before, including the refusal to re-apply
an existing batch id. No severity change; corpus rule set unchanged.

**Token budget gate: absolute tier ceilings and a ratchet-down rebaseline
(`#169`).** Test and tooling only; corpus rule set, deterministic `llm-wiki`
CLI/protocol surface, packaging and agent spec unchanged, no severity change.
`tests/budgets/token-budgets.json` moves to `schema_version` 2 (a schema 1 file
is rejected with a message): gated metrics carry an absolute design ceiling,
the lazily loaded aggregates and launcher sizes are reported and never fail,
and `scripts/benchmark-token-efficiency static` gains `--rebaseline` (rewrites
baselines, never raises a `max`, exit 2 on a breach) and prints per-metric
headroom with `--check`. Policy: `benchmarks/README.md`, Layer 1.

| Change | Classification | Ladder |
|---|---|---|
| Budget file schema 1 to 2; `static --rebaseline`; headroom lines on stderr of `static --check` | Test only (`scripts/benchmark-token-efficiency` is a development tool, not a shipped entry point) | None |

**#168, Codex plugin renamed `wiki@knowmade` → `knowmade@knowmade` — breaking
packaging change, no deprecation window.** Owner decision of 2026-10-18,
completing `#124 (Codex: wiki@knowmade and wiki@llm-wiki both resolve as
wiki:wiki)`. Codex derives the skill id from the plugin name alone, so the old
name shared `wiki:wiki`, `@wiki` and `$wiki-query` with upstream's
`wiki@llm-wiki`. Corpus rule set unchanged; deterministic CLI/protocol surface
unchanged (no `llm-wiki` namespace, flag, exit code or payload moves;
`references/cli.md` is untouched). Classified "Packaging/install change", and
breaking on that surface:

| Before (up to v0.7.0.25.1) | After |
|---|---|
| plugin `wiki@knowmade` (`.codex-plugin/plugin.json` and marketplace entry `name: wiki`) | `knowmade@knowmade` |
| skill `plugins/llm-wiki/skills/wiki/`, id `wiki:wiki`, mention `@wiki` | `skills/knowmade/`, `knowmade:knowmade`, `@knowmade` |
| explicit-only `plugins/llm-wiki/skills/wiki-query/`, `$wiki-query` | `skills/knowmade-query/`, `$knowmade-query` |
| cache entry `~/.codex/plugins/cache/knowmade/wiki/<version>/` | `.../cache/knowmade/knowmade/<version>/` |

Unchanged: the mirror directory `plugins/llm-wiki/`, the marketplace name
`knowmade`, the Claude plugin `wiki@knowmade` and its `/wiki:*` commands, the
OpenCode `wiki-manager` and `wiki-query` skills, the portable
`profiles/query-lite/` profile, and the token-budget metric names.

A rename has no compatible intermediate state in Codex (one plugin cannot
answer to two names), so there is no deprecation window; the migration is
automatic where Knowmade controls the install and two commands elsewhere:

- **Development checkout:** `scripts/bootstrap-codex-plugin.sh` removes an
  installed `wiki@knowmade` (`codex plugin remove wiki@knowmade`, which deletes
  its config table and cache entry although the marketplace no longer lists
  it) before it adds `knowmade@knowmade`, and warns if the old key is still
  enabled afterwards. `scripts/verify-codex-plugin.sh` resolves
  `knowmade:knowmade`, proves `$knowmade-query` explicit-only, and warns about
  a leftover enabled `wiki@knowmade`.
- **Pinned release install:** before the usual repin from `README.md`
  ("Upgrade"), run `codex plugin remove wiki@knowmade`, then
  `codex plugin add knowmade@knowmade` after re-adding the marketplace at the
  new tag. Prompts, scripts and notes that say `@wiki` or `$wiki-query` change
  to `@knowmade` and `$knowmade-query`.

The #124 coexistence guard keeps its rule with the new name: the bootstrap
refuses, without `--allow-coexisting`, only when another enabled plugin named
`knowmade` (from another marketplace) would share the skill id; upstream's
`wiki@llm-wiki` no longer triggers it and can stay enabled beside Knowmade. The
generated hook wrapper's cache fallback globs `cache/knowmade/knowmade/`. Tests:
`tests/test-codex-runtime.sh` installs beside an upstream stand-in without a
refusal or ambiguity warning, refuses and then verifies beside a same-named
`knowmade@knowmade-fork`, migrates a staged pre-rename `wiki@knowmade` install
of an updated checkout, and checks verify's leftover warning;
`tests/test-plugin-validate.sh` pins one identity across manifest, marketplace,
`scripts/lib/codex-config.sh` and the skill directories;
`tests/test-codex-sync.sh` checks the hook wrapper's plugin directory.

**Migration fixtures, `#171 (migration fixtures: an upstream llm-wiki v0.25.1
wiki and an old Knowmade wiki upgrade to the current layout without evidence
loss)`, KM-MIG-001/002. Corpus rule set, deterministic CLI/protocol and
packaging surfaces unchanged; agent spec additive.** `references/migration.md`
gains the "Deterministic Upgrade Recipe": nine existing `llm-wiki` commands
(`rules check`, read-only `lint`, `schema adopt`, `project migrate-why`,
`lint --preflight`, `rules declare --set 3`, `lint --fix-only
create,index,registry,gitignore,log`, then `rules check` and `lint` again). No
new verb or fix class was needed: on both fixtures the one gap is the
undeclared rule set, which `rules declare` (or the C22 `create` repair) closes.
New lane step `tests/test-migration-fixtures.sh` runs that block verbatim on
`tests/fixtures/migration/upstream-0.25.1/` (upstream `95a042c`) and
`tests/fixtures/migration/knowmade-0.1.0.22/` (tag `v0.1.0.22`, the earliest
release whose golden wiki differs from today's). No severity changes.

#### Same-version checker sha (`claude/sprint-i-checker-sha`)

`#167 (No knowmade_checker_sha value lints clean under both the checkout script
and the packaged build)`. Corpus rule set unchanged (`rules/3`); packaging
unchanged (`_build.py` keeps its fields; the release process is untouched).

**Deterministic CLI and protocol: a severity change for one finding.** The C22
engine-contract finding "Wiki declares checker X but this lint is running Y" is
lowered from `warning` to `info` when, and only when, `knowmade_version` equals
the running version and the declared sha is equivalent to the running build (see
"Which sha to pin" above). Every other case keeps its severity, including
`--strict-engine`. This is a relaxation of an existing `warning`, not a new
check, so it enters no ladder step.

- `lint --json` `engine.forms.commit` gains the value `equivalent` (besides
  `undeclared`, `unknown`, `match`, `drift`); `engine.state` is `match` then,
  and `engine.drifted` omits `commit`. `engine` gains the optional key
  `commit_equivalent` (`build`, `ancestor`, `descendant` or `unverifiable`),
  present only for an equivalent sha. `engine.behind` is also reported for an
  equivalent ancestor. `pin status` rows gain an optional `note` and print it.
- `engine info --json` gains `build_commit` (the `_build.py` commit) and
  `checkout_commit` (git `HEAD`, null outside a checkout) beside the unchanged
  `commit`; the text output prints both lines when they differ. No flag changed.
- `pin verify --engine` (the `scripts/ci-lint` gate) follows `engine.state`,
  so it now exits 0, not 2, for an equivalent sha. In a packaged copy that is
  any 7-40 hex sha once `knowmade_version` equals the running version. Exit
  codes are otherwise unchanged. A CI that needs the exact commit
  should declare the sha without `knowmade_version`.
- `engine` gains the optional key `git_failed` (true when a checkout's git
  could not be run and a same-version sha was therefore not accepted).
- Known limit: in a checkout, "an ancestor" is not bounded by the version's
  first commit, so an ancestor from an earlier release is also `info` while the
  declared version matches the running one.

| Change | Classification | Ladder |
|---|---|---|
| C22 checker-sha finding is `info` at the running `knowmade_version` for the packaged build identity, the checkout HEAD or a commit on the same history (#167) | Severity lowered for one finding (CLI-protocol); fewer warnings, no new strictness | None |

### v0.7.0.25.1 — Sprint H of the 2026-10 audit

Control-plane refactoring round 2, items R8, R10 and R11 of `#105 (control-plane
refactoring round 2)`, and `#84 (adopt claim should refuse or warn on an
interrupted move batch)`.

**R8, R10, R11, all three surfaces unchanged (package layout only).** `adoption`
is merged into `adopt_cli`, which now owns both parser fronts: the
`llm-wiki adopt` namespace and the standalone `llm-wiki-adopt` scanner
(`adopt_cli.scan_main`, driven by the same `scripts/llm-wiki-adopt` launcher).
`adoption_legacy` is renamed `adoption_plan`; `adoption_batch` is unchanged.
No flag, help string, exit code or JSON payload of the existing commands
changes, and `references/cli.md` differs only by the `repair` and
`--allow-interrupted` additions below. The `--yes` help of `llm-wiki-adopt`
now reads as the shared scan option does. `source_lifecycle` is reformatted to
one statement per line (AST-identical; the by-path loader of knowmade#117 is
untouched), and the `app_server` changelog narrative moves to
`docs/history/app-server-changelog.md`. The control-plane modules are internal
(package-only since Sprint G), so the rename needs no deprecation window.

**#84, deterministic CLI surface, additive plus one stricter exit.**
`adopt claim` (and `llm-wiki-adopt-batch claim`) on a batch whose `state.json`
records `staging: interrupted` now exits 2 with one message on stderr naming
`adopt verify` and `adopt repair`, and claims nothing; before, it handed out
`planned` rows whose originals were never moved. `--allow-interrupted`
restores the old behaviour. New `adopt repair <batch> [--yes] [--project-root
DIR]` re-stages the `planned` ingest rows whose originals still sit at their
source paths (a row whose original is gone becomes an `error` row, as `apply`
would have left it), journals each move to `events.jsonl`, marks
`staging: complete` and appends a `repair` event; a move batch needs `--yes`,
any other batch is reported as nothing to repair. Batches with
`staging: pending` or `complete` behave as before. Corpus rule set unchanged.

Index writes and frontmatter fences, `#99 (index regeneration drops BOM/CRLF,
is not atomic, and runs before article frontmatter repair)` and
`#103 (frontmatter fence detection quirks; schema traceback on a missing root)`.

**Index writer, corpus and CLI surfaces — unchanged contract, bug fix.** The
wholesale index writes (`indexing.regenerate_directory_index`, the in-place
repair of a curated index, `specialists.write_specialist_index`) go through
`markdown.write_text_preserving`, the writer every in-place repair already
used: atomic (temp file in the same directory, then rename), an existing file
keeps its BOM and line endings, a new file is LF without a BOM, as the
engine already promises for every rewrite (the newline-and-BOM rule under
Release Compatibility Record). No output of a clean run changes.

**Run order, corpus rule set unchanged.** `lint.WIKI_STEPS` gains the step
`index rows` (`fix_class` `index`, check C3) right after `index consistency`,
so it runs after every frontmatter repair. The structure step still creates a
missing `_index.md` before the article frontmatter is repaired, so that first
version can hold empty summary and tag cells; `index rows` then refreshes
just the rows that differ from the article, and one `--fix` run indexes an
article with the summary and tags it was just given. Only a generated index
(one carrying the `Generated by local llm-wiki lint` marker) is compared; a
curated index is never touched. Non-fix runs gain one finding:
`index-row-stale` (C3), added through `rule_lint.issue_versioned` at
`CURRENT_RULE_SET` (rules/3), so it is `info` in rules/3 and absent below it,
with the usual ladder (warning in rules/4, critical in rules/5). Existing steps
keep their names and `fix_class`; `lint --json` is byte-identical to the previous
engine on the golden wiki and on every defect fixture.

**Frontmatter fence, corpus surface — bug fix within rules/3, no ladder change.**
`markdown.frontmatter_bounds` closes a block only at a line that is exactly
`---` (trailing spaces and tabs allowed, `\r` for CRLF). Before, any
`\n---` closed it, so `----` and `---x` ended the block mid-line and moved the
start of the body, and `---` straight after the opening fence was reported as
`unterminated YAML frontmatter` (critical). Now `----` and `---x` do not close
(a block with no real closing fence is unterminated, as before) and the empty
block `---\n---` is a valid empty frontmatter. The old behaviour was a misparse,
not a rule, so this is not classified as a new or stricter rule and keeps the
verdict of every correctly fenced file; the only verdict changes are an empty
block (critical to valid) and a file whose only "closing fence" was `----` or
`---x` (parsed wrongly to unterminated). Messages are unchanged.

**`schema` on a root that is not an initialized wiki, CLI surface.**
`schema status`, `schema adopt` and `schema migrate` check that the root has a
`config.md`. Without one they print `not an initialized wiki: <root>` and exit
2 (the usage-error code `links` and `privacy` use) instead of a
`FileNotFoundError` traceback (adopt, migrate; exit 1) or exit 0 with
`{"state": "missing"}` (status). Under `--json` the output is an object with
`ok: false`, `error`, `root` and `command`. A real wiki is unaffected.

**R9, session capture: the Sprint G/H hub-selection and fence fixes reach the
session script; deterministic CLI surface unchanged.** `scripts/sync-session-vendor.py`
now generates every vendored block of `scripts/llm-wiki-session` (`roots`,
`fsio`, `markdown`, `redaction`) from the module source, so a fix lands once.
Two behaviours of the session script follow the control plane: hub
resolution honours `LLM_WIKI_HUB` after `--hub` and ahead of the configured
`hub_path` (blank is unset; cwd discovery is not vendored), and digest
frontmatter is found by the exact-fence rule of `markdown.frontmatter_bounds`
above (so `promote` reads a hand-edited digest as `lint` would). No
`llm-wiki` namespace, flag, exit code or payload changes and
`references/cli.md` is untouched. Inside the package the frontmatter field
readers become `frontmatter.field_value` (raw text, `folded=` for `>`/`|`
blocks) and `frontmatter.scalar_field` (parsed scalars); `lint --json` is
byte-identical on the golden wiki, every defect and every rule-set fixture.
Corpus rule set unchanged.

**`source extract` honours the declared charset (`#162`), deterministic CLI
surface, bug fix.** `source extract` decoded every text original as UTF-8, so a
`windows-1251` page came out as U+FFFD. It now reads a BOM first (UTF-8,
UTF-16), then for HTML a `<meta charset>` or `http-equiv` content-type
declaration in the first 4 KB (label resolved through `codecs.lookup`; an
unknown or non-text label is skipped), else UTF-8. The derived text changes only
for files that declared a non-UTF-8 charset or carry a BOM (a UTF-8 BOM is no
longer kept in the text); every other file extracts to the same bytes. The
codec used is recorded as the additive `source_encoding` key on the derived
artifact, within `llm-wiki/source-pointer/v1` (readers check only the required
keys). No flag, exit code or other payload changes; corpus rule set unchanged.

### v0.7.0.25.1 — Sprint H — packaging fixes

Remainders of `#124 (Codex: wiki@knowmade and wiki@llm-wiki both resolve as
wiki:wiki)`, `#125 (OpenCode URL install delivers SKILL.md without its
references or CLI; symlink breaks on Windows)` and `#86 (Codex runtime smoke
test: cache rename still fails on Windows when a scanner holds the entry for
more than ~10s)`. Corpus rule set unchanged and deterministic CLI/protocol
surface unchanged (no namespace, flag, exit code or payload of `llm-wiki`
moves; `references/cli.md` is untouched); all three changes are on the
distribution and runtime packaging surface and are classified as "Packaging/install
change" under Change Classification. Nothing is released by this record.

**#124, Codex hook wrapper — narrowed, no deprecation window.** The self-healing
fallback in the generated `plugins/llm-wiki/hooks/hooks.json` used to glob the
Knowmade marketplace cache (`cache/knowmade/wiki/*/hooks/`) and, when that was
empty, any marketplace (`cache/*/wiki/*/hooks/`). With upstream `wiki@llm-wiki`
installed beside Knowmade the second glob could run upstream's helper from the
Knowmade hook. It now globs only the marketplace named in
`.agents/plugins/marketplace.json`; with no Knowmade cache entry the hook exits
0 without capturing anything. The hook still runs `--if-enabled`, so capture
stays off until `llm-wiki-session enable` (owner decision 3a). This narrows
behaviour that only ever worked by accident across marketplaces and was never
documented, so it takes effect with the next release without a deprecation
window. `tests/test-codex-sync.sh` and `tests/test-session-capture.sh` assert
that the stamped command globs exactly the Knowmade marketplace. The coexistence
rules from the first #124 change (bootstrap refuses without `--allow-coexisting`,
verify matches the Knowmade cache path) are unchanged. Renaming the Codex plugin
from `wiki` to `knowmade` is not part of this record; it stays a separate
compatibility-classified owner decision (see the Sprint H hand-back brief).

**#125, OpenCode mirror layout — links replaced by copies.**
`plugins/llm-wiki-opencode/skills/wiki-manager/references` is now a real
directory of the same files the Codex mirror copies, produced by
`scripts/.sync-opencode-plugin-legacy.sh` without per-file rewrites; the
tracked symlink (mode 120000) is gone and the sync replaces an old link or its
Windows placeholder file instead of writing through it. Paths a consumer
follows (`skills/wiki-manager/references/*.md`, `bin/llm-wiki`) are unchanged,
so an `instructions` entry pointing at `SKILL.md` keeps working; the checkout
and archive of the mirror grow by the references (the static token-efficiency
gate measures entry files and is unchanged). `tests/test-opencode-sync.sh`
fails on a symlink, a placeholder file, a stale copy or any tracked symlink
under the mirror; `tests/test-plugin-validate.sh` checks the copied files.
OpenCode and Pi still need the whole checkout from the distribution repository
(documented once in `README.md`); a single fetched `SKILL.md` never delivered
`references/` or `bin/` and still does not. Tags up to `v0.6.0.25.1` keep the
old layout, which the README notes in one sentence for Windows users with
`core.symlinks=false`.

**#86, Codex bootstrap retry — configurable budget, one new exit status.**
`scripts/bootstrap-codex-plugin.sh` retries `codex plugin add` for up to
`KNOWMADE_CODEX_CACHE_RETRY_SECONDS` (default 30, `0` disables) of waiting, with
waits of 1, 2, 4, then 5 s, instead of five attempts over 10 s, and keeps the
staging sweep. When the budget runs out it names the held file and exits 75
(`EX_TEMPFAIL`); every other failure keeps Codex's own status. The undocumented
`CODEX_INSTALL_ATTEMPTS` override is removed. The bootstrap is a development
install path (release installs pin a tag), so this is additive for its callers:
only an exhausted budget changes status, from Codex's non-zero to 75.
`tests/test-codex-runtime.sh` holds every staged file for 12 s, proves an
exhausted 1 s budget exits 75 and names the file, and reports a real external
hold that outlasts the budget in the plain installs as `SKIP:` naming the file
(`KNOWMADE_STRICT=1` fails it). No manifest, marketplace or install-instruction
version changes.

**Hub registry overlay, nested topic wikis and hub statistics (knowmade#114)
— additive CLI, one new machine-state file.** New read-only `hub index
--stats` (`knowmade/hub-stats/v1`, exit 0) and `hub registry check
[--portable]` (`knowmade/hub-registry-check/v1`; `--portable` exits 1 on a
tracked `wikis.json` path that is not `<HUB>`-relative or an overlay git does
not ignore, otherwise 0). The optional, gitignored `HUB/wikis.local.json`
(`{"wikis": {"<slug>": {"path": ...}}}`) replaces the path of an active
tracked entry on this machine for `--wiki`, `archive`, the hub index and
`lint --all`; without it every path resolves as before, and a malformed one is
an error. `/wikis.local.json` joins `hub.STATE_IGNORE_RULES`, so `lint --fix`
appends it to a hub `.gitignore` and C21 reports a tracked or unignored
overlay on the existing `rules/3` ladder (info). A registry path or
`topics/<slug>` without `_index.md` whose `.wiki/_index.md` exists now names
that nested `.wiki`: `--wiki` resolves it, `archive topic|restore` move the
whole topic directory, `archive list` rows gain `root` and list initialized
unregistered topics as `(missing from registry)`, and `lint --all` lints such
a root under the topic's slug without `--include-nested-wiki` (the flag still
adds a `.wiki` inside a topic that is a wiki itself, as `<slug>/.wiki`), so
such a topic, formerly skipped as `no _index.md`, now counts toward the exit
code. No other key, exit code or finding changes. Corpus rule set unchanged.
Packaging: regenerated mirrors only.

Agent spec: `#81 (context diet, round two)` and `#83 (deterministic scripts the
agent spec still does by hand)`. The upstream baseline is unchanged (v0.25.1).

**Agent-spec surface — reference files moved, loads made lazy (#81).** No
behaviour is added or removed; facts move so each lives in one file and is
read at the step that needs it.

| Change | Classification | Ladder |
|---|---|---|
| `references/research-infrastructure.md` is removed and split by phase: `research-agents.md` (Phase 2 prompt templates), `research-credibility.md` (Phase 2b), `research-staging.md` (Phase 3 inbox staging), `research-rounds.md` (progress and gap scoring, `--min-time`), `research-sessions.md` (session registry and provenance files), `research-plan.md` (`--plan` schema). `research-protocol.md` and `research-modes.md` name the file per step and drop their duplicate copies of the agent template, credibility rubric, progress formula and session schema | Agent spec change; a consumer that loads `research-infrastructure.md` by name must load the phase files | None |
| The 18 Core Principles move from `SKILL.md` (1-10, 14, 18) and `skill-workflows.md` § Core Principles (11-13, 15-17) into `references/principles.md`; `SKILL.md` keeps a five-line summary under the same heading. The Codex and OpenCode `SKILL.md` dual-linking rewrites are dropped (references are shared verbatim and say "agent") | Agent spec change | None |
| `/wiki:lint` is cut to the CLI-first minimum and no longer names `references/cli.md` (`lint --help` has the flags); `/wiki:collect` no longer reads any research reference | Agent spec change | None |
| `tests/budgets/token-budgets.json` ceilings lowered to the measured values | Test only | None |

**Deterministic CLI and protocol surface — additive (#83).** Two verbs; no
existing flag, output or exit code changes.

| Change | Classification | Ladder |
|---|---|---|
| `llm-wiki project migrate-why [path\|scope] [--dry-run\|--apply] [--json]`, new namespace `project`: the lossless C8c `_project.md` -> `WHY.md` migration (schema `knowmade/project-migrate-why/v1`). Dry run by default; `--apply` writes `WHY.md`, deletes `_project.md` and appends one `log.md` entry; a folder with `WHY.md` is skipped; a manifest with nothing to preserve is refused. Exit 0, or 2 for a missing root or a hub | New optional command | None |
| `llm-wiki freshness score [scope] [--threshold N] [--all] [--include-snapshots] [--json]`: librarian.md's composite 0-100 score (schema `knowmade/freshness-score/v1`), threshold from `freshness_threshold` (inherited from `HUB/config.md`, default 70). Read-only; lint still never reads the threshold | New optional subcommand | None |

**Corpus rule set — unchanged (`rules/3`); former agent passes enter the
ladder.** Each finding below is new, enters at `info` through
`rule_lint.issue_versioned` with `introduced_in = 3` (silent in `rules/2` and
`rules/1`, `warning` from `rules/4`), and ends its message with its kind. No
verdict changes under `rules/3`.

| Change | Classification | Ladder |
|---|---|---|
| `project-legacy-manifest` (C8): a `_project.md` in an active project folder | New finding | `info` in `rules/3` |
| `project-stale` (C8b): a project member citing a raw source ingested after its `updated:` | New finding | `info` in `rules/3` |
| `output-loose-binary`, `output-loose-directory`, `project-candidate` (C9), new lint step `output candidates` | New findings | `info` in `rules/3` |
| `inventory-migration-candidate` (C16), `dataset-migration-candidate` (C17), new step `migration candidates` | New findings | `info` in `rules/3` |
| `see-also-one-way` (C4), `orphan-article` (C6), new step `article cross-links` | New findings | `info` in `rules/3` |
| `index-stats-drift` (C3), new step `index statistics`; inventory `views/` are not counted as records | New finding | `info` in `rules/3` |
| `tag-near-duplicate` (C5): tags equal but for case, separators or a plural s | New finding | `info` in `rules/3` |
| `output/projects/.archive` is no longer linted as a project (it drew a missing-`WHY.md` and a slug warning) | Fix: two false warnings removed | None |

Packaging: regenerated Codex and OpenCode mirrors and `references/cli.md` only.

**Consumer CI surface of `lint` and `dataset verify` (knowmade#119) — additive;
corpus rule set unchanged, CLI surface additive, packaging unchanged.**

- *Deterministic CLI and protocol surface.* New `lint` flags
  `--github-annotations`, `--max-warnings N` and `--profile NAME[,NAME]`
  (`citations`, `datasets`); plain `lint` output, JSON and exit codes are
  unchanged without them. `--max-warnings` adds an exit-1 condition independent
  of `--fail-on`; `--github-annotations` prints after the report (stderr under
  `--json`); both are rejected, with exit 2, under `--all`. Findings may carry
  an optional `line` (JSON key present only when set). New namespace
  `dataset verify` (exit 0/1/2 as `privacy scan`); new JSON `findings[]` kinds
  `dataset-missing`, `dataset-size`, `dataset-checksum`, `dataset-manifest`.
  `scripts/ci-lint` renders the same annotation format.
- *Corpus rule set — new checks on the ladder.* `C26` (citations profile) and
  `C27` (datasets profile) are introduced in rules/3 through
  `rule_lint.issue_versioned`: `info` in rules/3, `warning` in rules/4,
  `critical` after, absent in rules/1 and rules/2 corpora. Both are off unless
  the profile is named, so no existing run changes. `config.md` keys
  `citations_long_words` and `citations_signal_words` are read only by C26.
  `references/datasets.md` now specifies the `locations` base directory (the
  wiki root) and the `sha256:<hex>` checksum form; no existing manifest field
  or enum changes.

### v0.6.0.25.1 — Sprint G of the 2026-10 audit

Control-plane refactoring round 2, items R1, R2 and R3 of `#105 (control-plane
refactoring round 2)`, and `#82 (stable lint check ids)`.

**R1 and R2, deterministic CLI and protocol surface — unchanged.** Every namespace, flag,
help string, exit code and JSON payload is the same; `references/cli.md`
regenerates byte for byte from the new parser module. The parser is assembled
in `knowmade_control/cli.py`, whose `NAMESPACES` list is the one registration
point; `hub` registers `archive`, `schema` and `identity` through one function
each. `identity` resolves its wiki through `roots` directly instead of through
`legacy_cli`.

**R1 and R2, distribution and runtime packaging — launchers run the package.** The
control plane runs only as the `knowmade_control` package: `scripts/llm-wiki`
and the packaged `bin/llm-wiki` copies import it from their own directory,
`llm-wiki-adopt` and `llm-wiki-adopt-batch` import it the same way, and
`python -m knowmade_control.cli` (or `.legacy_cli`) runs the same parser. The
script-mode import fallbacks are deleted from every module except
`source_lifecycle`, whose by-path loader external consumers use (knowmade#117).
Running a module file as a bare script (`python3
scripts/knowmade_control/lint.py`, `legacy_cli.py`, ...) was never a supported
entry point: no install instruction, command spec or release note named it,
and only the schema test's direct-script oracle exercised it, so there is
nothing to deprecate. `legacy_cli` keeps `build_parser`, `main` and the 22
names tests still import through it; every other name it used to re-export
is gone from it and remains importable from its owning module. The unused
`adoption_batch.append_event` and `engine.engine_root` are removed. Corpus
rule set unchanged.

**R3, lint split — all three surfaces unchanged.** `lint.py` keeps the parser
and the run order (`lint.lint_steps`); the checks move verbatim into
`lint_docs`, `lint_links`, `lint_hub` and `lint_report`, and the lint data
model (`LintContext`, `Issue`, `Document`) moves from `common` to
`lint_report`. `lint --json`, the text report and the `--fix` result are
byte-identical on the golden wiki and every defect fixture, and
`references/cli.md` is unchanged. `legacy_cli` still re-exports its 22 names,
now from the owning modules.

**Lint check ids (knowmade#82) — CLI/protocol additive.** Every
`lint --json` issue gains `check`, the id of the `references/linting.md` rules
row that found it (`C1`..`C21`, `C4b`, and the newly assigned `C22` engine and
rule-set contract and `C23` specialist library). No other key, value, order or
exit code changes, and the text report is unchanged (its finding line has no
stable column for an id). Consumers that matched findings by message wording
should match by `check` and `path`. Corpus rule set unchanged: no finding
changes severity or verdict, so no rule-set bump. Packaging: regenerated
mirrors only.

**Deterministic CLI and protocol surface — additive: `privacy scan`
(knowmade#112, privacy scan over tracked files).** New namespace `llm-wiki
privacy` with one verb, `scan`, and `checkpoint seal --allowlist FILE`. No
existing flag, output or exit code changes. `privacy scan` runs the checkpoint
scanner's pattern pack (`checkpoints.checkpoint_pattern_specs`, the shared table
in `redaction.py`), so it follows `CHECKPOINT_SCANNER_VERSION` and produces the
same `privacy-<id>` finding ids; extra packs come from `--patterns`. Exit codes:
0 nothing at or above `--fail-on`, 1 something is, 2 usage or configuration
error. A listed file the scan cannot read (missing, oversized, undecodable
name) is an `unscanned` warning finding, never a silent skip. The allow file (`privacy-<id>` lines and `path::kind::match` regexes) is
shared by both commands; in `seal`, an allow-file entry that matches nothing in
the bundle is ignored, where `--allow-finding` still rejects a stale id. The
`private-tracked` kind (`--check tracked-private`, and the plain function
`privacy.tracked_private_paths`) is recorded under Documented ladder exceptions
below. Corpus rule set unchanged.

**Engine identity and pins (knowmade#115) — additive CLI.** New namespaces
`engine info` and `pin status|set|resolve|verify`. `rules check` additionally
accepts `--local`, `--wiki`, `--hub` and the scope flags `lint` takes (`--path`
is no longer required), prints the engine contract after the rule-set lines and
adds an `engine` key to its JSON; its exit status is still the rule-set
verdict. `rules --json check` now prints JSON (knowmade#101: the namespace and
subcommand `--json` flags no longer share an argparse dest). A hub may declare
the engine pin in an optional `HUB/config.md`; a topic that declares no pin key
inherits it, and the engine state gains `pin_source` and `pin_config`. A topic
that declared no pin under a hub that declares one is now judged against the
hub's pin instead of reporting `undeclared`, so `lint` can newly warn about
drift there; that is the point of the declaration and needs no rule set.
`engine._engine_checkout` is public as `engine.engine_checkout`. Corpus rule
set unchanged.

**Hub selection and hub index (knowmade#114) — additive CLI.** New namespace
`hub index [--check|--write]` and `hub where`. Hub selection gains the
`LLM_WIKI_HUB` environment variable (after `--hub`, before `config.json`) and an
opt-in `"hub_discovery": "cwd"` in `config.json`; with neither set, every
command selects the hub it selected before. `archive topic|restore`,
`hub index --write` and `pin set` accept `--require-cwd-hub`.
`hub.write_hub_index` returns whether it changed the file and no longer
rewrites an index that is already current. `index_drift(hub)` is the read-only
check for lint's `hub-index-drift`; wiring it into lint is new lint behaviour
and goes through `rule_lint.issue_versioned`. Not in this slice: `--stats`,
`wikis.local.json` and nested `topics/<slug>/.wiki` recognition.

**Deterministic CLI and protocol surface — additive: `links check` and `lint
--all` (knowmade#113, whole-tree link check and `lint --all`).** New namespace
`llm-wiki links` with one verb, `check`, and new flags `--all`,
`--include-nested-wiki`, `--skip-private` and `--report-dir` on `lint`. No
existing flag, output, exit code or JSON key changes, and lint's C4 scope
(`wiki/` and `inventory/`) is unchanged. `links check` reports `broken-link`,
`unresolved-wikilink` and (`--warn-absolute`) `absolute-link`, all warnings,
with the lint link reader and resolvers; exit codes 0, 1 (a finding at or above
`--fail-on`) and 2 (usage, configuration or git error, never reported clean).
`lint --all` runs the single-root lint per hub root, prints an aggregate
`{hub, roots, counts}` report, exits with the worst root, rejects `--fix` with
exit 2 and writes only inside `--report-dir`. Corpus rule set unchanged: no
rule or severity is added.

**Freshness windows and snapshots (knowmade#108) — additive corpus keys and
CLI.** `config.md` may set `freshness_hot_days`, `freshness_warm_days` and
`freshness_cold_days` (a topic inherits each unset key from `HUB/config.md`),
and an article may declare `freshness: snapshot`. New read-only namespace
`freshness report [--due N] [--include-snapshots] [--json]`. Without the new
keys lint reports what it reported before. A longer window and a snapshot
only relax C14 (a stale snapshot is info). An invalid window, an unknown
`freshness:` value and an article stale only under a window shorter than the
default are new strictness and go through `rule_lint.issue_versioned` from
rules/3, so a rules/3 corpus sees them as info. `freshness_threshold` stays
the librarian's score threshold; lint never reads it. The check moved from
`lint_docs` to `knowmade_control/freshness.py`; the lint step keeps its name.
`engine.topic_hub` is the shared "which hub is this a topic of" helper.

**Lint fix classes (knowmade#111, lint --fix is all-or-nothing) — CLI/protocol
additive.** New `lint` flags `--preflight` (alias `--plan`), `--fix-only`,
`--no-quarantine`, `--no-log` and `--refuse-on`; the repair classes are listed
once in `references/linting.md` § Fix classes. Every `lint --json` issue gains
`fix_class` beside `check` (empty when not fixable); a preflight report adds
`plan[]` and a refusal adds `refused[]`. New exit code 2 only for a
`--refuse-on` refusal (or `--refuse-on` without `--fix`/`--preflight`). A
preflight exits 0 unless `--fail-on` is given, so `--fail-on` no longer has a
parser default; without `--preflight` the effective default is still
`suggestion`. Plain `lint` and `lint --fix` write and report as before, with
three corrections to `fixable`: a C3 missing index entry or dead index link
found without `--fix` is now `fixable: true` (`--fix` repairs it), and the C1
missing master `_index.md` is now `fixable: false` (no repair writes it).
Corpus rule set unchanged: no severity or verdict changes. Packaging:
regenerated mirrors only.

**Hub lint: three checks wired (knowmade#114, knowmade#115, knowmade#112) —
CLI/protocol additive, one privacy exception.** `lint --json` on a hub gains two
check ids. `C24` (hub index drift, `hub_index.index_drift`) is `info` in
`rules/3` through `rule_lint.issue_versioned` with `introduced_in = 3`, so it is
silent below, `warning` from `rules/4` and `critical` from `rules/5`; the
`Last updated` date alone is never drift, and `hub index --write` is the
repair (no lint fixer). `C25` (tracked private files, kind `private-tracked`) is
`critical` in every supported rule set under the privacy exception recorded
under Documented ladder exceptions; a hub whose git index tracks a path under a
`-PRIVATE` directory now exits 1 under `--fail-on critical`, and the hub
`config.md` key `private_anchors` (comma-separated string or YAML list,
default none) lists the basenames it tolerates. When git cannot list the files
the check reports one `warning` instead of passing. Neither check runs on a
topic root, and no existing finding changes severity. The C22 engine-drift
finding of a topic that inherits its pin now carries the hub's `config.md` as
`path` (it was the topic's own file, which declares nothing); the message,
severity and exit code are unchanged. Corpus rule set unchanged. Packaging:
regenerated mirrors only.

**Adoption keeps `.wiki/` out of git (knowmade#95, adopt apply leaves `.wiki/`
un-ignored) — additive flag, one new `.gitignore` write, two state keys
removed.** `llm-wiki adopt apply` (and `llm-wiki-adopt --apply`) gains
`--no-gitignore`, and its `--json` result gains a `gitignore` object
(`action`: `appended`, `already-ignored`, `opted-out`, `not-git` or
`outside-project`; `rule`). When the wiki directory lies inside the project and
the project is in a git work tree whose rules (`git check-ignore`, so an outer
`.gitignore` or the global excludes count) do not already ignore it, the
default `.wiki/` rule (`/<path>/` for a custom `--wiki-root`) is appended to the
project's `.gitignore` through the hub ignore helper, under one comment,
idempotently, in the file's newline style; the file is created when absent.
`--no-gitignore` writes nothing and prints a warning with the next step.
Outside git, or with a wiki outside the project, nothing is written. Same
privacy rationale as the hub rules in C19/C21 and `lint --fix`: git would
otherwise commit staged copies of adopted material, so the append is the
default and an opt-out exists, as with `llm-wiki-session enable`. Classification:
CLI additive; the `.gitignore` append is a new side effect that only adds a
rule git would otherwise not have and is skipped when the path is already
ignored, so it is a ladder bypass for privacy, not a graded change. Persisted
text no longer names the machine: `log.md` says "planned from `<project
name>`", `PLAN.md` lists the project name and the wiki root relative to the
project, and batch `state.json` drops `project_root` and `wiki_root` (absolute)
for `project_name` and `project_root_relative` next to the existing
`wiki_root_relative`. Removal of the two keys is deliberate: no shipped reader
needs them (`resolve_wiki_root` uses an existing `--wiki-root`, then
`wiki_root_relative`, then the `<wiki>/inbox/.imports` layout, then a recorded
`wiki_root` if an older batch has one), and a batch copied out of its layout now
needs `--wiki-root`. The stdout and `--json` plan output still print absolute
paths; they are not stored. Batches written earlier keep their absolute paths
until the project is cleaned by hand. Corpus rule set unchanged.

**Source lifecycle for adapters (knowmade#118) — additive CLI and pointer
keys.** New `source` verbs `resolve`, `paths`, `import-pointer`,
`attach-derived` and `map`; `receive` adds `--note` and repeatable `--alias`;
`verify` adds `--pointer-file`; every `source` verb accepts `--json` (the
output was and is JSON). A v1 pointer may carry `retrieved_at`, `note`,
`aliases` and `imported_from`, and a derived artifact `tool`, each only when
set, so pointers without them are byte-identical to before. They stay within
`llm-wiki/source-pointer/v1` because every v1 reader, including the engines
pinned by consumers, checks only the required keys. Behaviour changes, all
on surfaces that failed or lost data before: re-receiving a known source
fills provenance its record lacks and adds aliases (it refuses to replace a
recorded value, where it used to ignore the flags); `decide` on an existing
pointer that differs only in those optional keys rewrites it from the record,
keeping its `recorded_at`, instead of failing; `extract` keeps artifacts
attached by `attach-derived`; a file in the pointers directory whose `schema`
is not `llm-wiki/source-pointer/...` is listed under a new `foreign` key of
`verify` and is no longer a failed row. Lint reports such a file as
`source-pointer-foreign` (C20) through `rule_lint.issue_versioned` from
rules/3, where it used to be a critical "Invalid source lifecycle pointer";
that is a relaxation, so no rule-set change. Packaging: regenerated mirrors
only.

**Consumer CI entry point (knowmade#119) — packaging additive.** New
`scripts/ci-lint`, shipped in the distribution tree with the rest of
`scripts/`. It is a standalone standard-library script, not a `llm-wiki`
namespace, and drives only existing CLI surface: `pin verify --engine` (it
falls back to `lint --strict-engine` alone on engines that predate `pin`) and
`lint --strict-engine --fail-on LEVEL --json`. It accepts only a release pin
(`knowmade_version`), because the distribution repository has tags and no
source commits. Its exit codes: 0 pass, 1 lint failure or `--max-warnings`
exceeded, 2 engine unreachable or not the pinned one. `--allow-skip` turns an
unreachable engine into `SKIP: <reason>` with exit 0, never into a pass.
GitHub annotations are rendered from the existing `lint --json` keys; no
engine flag, payload key or rule changed.

**Adapter namespace split (R6 of knowmade#105, control-plane refactoring
round 2) — package layout only.** `adapters.py` is split into
`adapter_manifest` (manifest, request and response validation) and
`adapter_registry` (the registry file); `adapters` keeps process execution and
the namespace. CLI, protocol, `--json` output, the registry format and the
adapter protocol `llm-wiki-adapter/v1` are unchanged; the mirrors carry the new

**Checkpoint namespace split (R7 of knowmade#105, control-plane refactoring
round 2) — package layout only.** `checkpoints.py` is split into
`checkpoint_scan` (the pattern pack over `redaction.py`, finding ids, the
scanner version) and `checkpoint_bundle` (validate, seal, verify);
`checkpoints` keeps the namespace and re-exports the moved names for one
release. `privacy` now imports `checkpoint_scan`, which removes the
privacy↔checkpoints import cycle. CLI, `--json` output, the privacy-report
format and the scanner version (4) are unchanged; the mirrors carry the new
files.

**Archive scope flags after the subcommand (knowmade#102, `--archived-only` is not a CLI flag) — CLI
additive, one stricter usage error.** `archive list|topic|restore` accept `--hub`, `--private` and
`--public` after the subcommand as well (`archive list --hub X`), as `schema` and `identity` do; the
copies are hidden from `--help`, so `references/cli.md` gains only a sentence. Giving `--private` and
`--public` together, one before and one after the subcommand (which argparse cannot see as a
conflict), exits with a usage error. The command specs and `AGENTS.md` no longer claim a CLI
`--archived-only` flag: it is the `/wiki:lint` command's loop of `lint --wiki <slug>
--include-archived` over every archived slug. Corpus rule set and packaging unchanged (regenerated
mirrors only).

### v0.6.0.25.1 — git environment isolation (knowmade#135)

**Deterministic CLI and protocol surface — bug fix, no flag change.** Every git
subprocess the control plane runs against a wiki or hub (`hub.is_inside_git_work_tree`,
`hub.git_ignores_path`, the C21 `git ls-files` probe, `engine` commit lookups)
now passes `fsio.git_environment()`: `os.environ` without `GIT_DIR`,
`GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_COMMON_DIR`, `GIT_PREFIX`,
`GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES` and `GIT_NAMESPACE`.
Before, a CLI launched from a git hook or `git rebase --exec` inherited the
invoking repository's `GIT_DIR`, so a hub anywhere on disk answered "inside a
work tree" for that repository and the private-ignore guard refused `archive`
on a hub that was not in git at all. `scripts/run-checks` unsets the same
variables for every step. Corpus rule set and packaging unchanged.

**Security fix, no flag change: no repository-configured fsmonitor.** The git
reads run inside a hub or wiki checkout (`hub where`'s `git status`, the
`ls-files` and `check-ignore` probes of `hub`, lint and `privacy scan`) pass
`fsio.GIT_READ_OPTIONS` (`-c core.fsmonitor=false`). Before, a checkout whose
`.git/config` named a `core.fsmonitor` program, for example a cloned or planted
hub found by `hub where`, ran that program on every such read. `hub where`'s
`git status` also runs in `fsio.git_filter_environment`, whose
`GIT_CONFIG_COUNT` entries empty every configured `filter.<driver>` command,
so a stat-dirty file is compared as raw bytes rather than through a
repository-named `clean` or `process` program; a hub that relies on a filter
(Git LFS) may then show as dirty. It no longer enters submodules
(`--ignore-submodules=all`), and when the filter names cannot be read back
exactly it reports no git state instead of running `git status`. Output and
exit codes are otherwise unchanged.

### v0.5.1.25.1 — Sprint F of the 2026-10 audit

#### Lint and the engine contract (`claude/sprint-f-lint`)

Lint correctness for hub consumers and the engine contract: the false positives
the 2026-10-16 consumer audit measured on a real hub, plus the `config.md`
reader. The upstream baseline is unchanged (v0.25.1).

**Corpus rule set — unchanged (`rules/3`).** Every change below removes or
downgrades a finding, reports an existing condition more accurately, or is a
new finding that enters at `info` through `rule_lint.issue_versioned`
(introduced in `rules/3`, so `warning` in the next set). One finding is a
documented ladder bypass (the first row of the table). No corpus needs to
re-declare; `rules/3`, `rules/2` and `rules/1` stay supported (N-2).

**Deterministic CLI and protocol — additive.** No flag changed, so
`references/cli.md` is unchanged.

- `lint --json` `engine` gains optional keys: `declared_upstream` (`repo`, `sha`;
  present when `llm_wiki_checker_repo`/`_sha` are declared), `behind` (commits;
  present only when the declared checker commit is an ancestor of the running
  one and git can count), `declared_version_key` (`knowmade_version` or
  `llm_wiki_version`) and `upstream_baseline`. `engine.forms` keeps exactly
  `commit`, `version` and `rules`.
- `config.md` gains the optional `knowmade_version` key and the optional
  `lint_allow_root` list.

| Change | Classification | Ladder |
|---|---|---|
| An existing `config.md` with missing, unterminated or unreadable frontmatter is a `critical` finding (before: the rule-set and engine declarations were silently ignored, #97) | Stricter check on the file that carries the rule-set declaration; it cannot be graded by any rule set, exactly like an out-of-window declaration | Exceptional critical (`exceptional_critical`, `target_rule_set` = current), no bump: a corpus whose declarations are unreadable is not judged by the rules it declared. Fix: add or close the `---` frontmatter fence |
| `llm_wiki_version` also matches the running upstream baseline (`0.5.0.25.1` is based on `0.25.1`); `knowmade_version` is the unambiguous key and wins when present (#106) | Relaxation: a corpus following the upstream convention could never clear the warning | None |
| Engine pins are read from the `config.md` frontmatter only, by the same reader as `knowmade_rule_set`; a declaration-looking body line no longer counts (#97) | Detection gap closed in an existing `warning`; fewer false drifts | None |
| A drifted `knowmade_checker_sha` that is an ancestor says "declared engine is an ancestor, N commits behind" (#106) | Message only | None |
| Wiki-links: `[[../topics/x]]` resolves by path then basename; `[[_index]]` and targets in `inventory/`, `raw/`, `output/`, `datasets/` are `info` (#107) | Relaxation of the wiki-link `warning`; a target that exists nowhere stays a `warning` | None |
| `AGENTS.md`, `CLAUDE.md`, `PRIVATE_BOUNDARY.md` are allowed at a topic root; `scripts`, `docs`, `tools`, `.githooks` are allowed at a repository root; `lint_allow_root` widens the root level per corpus (root-level plain names only, never `raw`/`wiki`/`inventory`, never `*-PRIVATE`) (#109) | Relaxation of the unknown-file `warning`; `--fix` no longer moves these | None |
| `.DS_Store`, `Thumbs.db`, `desktop.ini` and `._*` are ignored everywhere and never moved by `--fix` (#104) | Relaxation of the unknown-file `warning` | None |
| Hub registry: a relative path that does not exist is `info` (`registry-path-missing`); a relative path that leaves the hub is reported as "escapes the hub" (was worded "absolute") with the same severities as before; an entry that resolves to a README-only `*-PRIVATE` directory is `info` "private placeholder" (#116) | New findings at `info` via `issue_versioned` (`warning` in the next rule set); the wording fix changes no severity | Ladder step 1 for the new findings |
| A README/`.gitkeep`-only `*-PRIVATE` directory is not linted as a wiki: one `info` ("private placeholder, not checked out here") replaces the structure criticals, with and without `--fix` (#116) | Relaxation: the criticals carried no information | None |
| `markdown.parse_scalar` unescapes double-quoted scalars (`\\`, `\"`, `\n`, `\t`) and `''` in single-quoted ones; `topic_title` reads the `title` frontmatter field instead of a regex (#96) | Fix: `yaml_quote` output now round-trips, so generated indexes no longer show escape junk; an unknown backslash sequence stays literal | None |

**Deprecation notes.** None of these tightens a verdict under a declared rule
set, except the first row.

1. **Unreadable `config.md` (ladder bypass: the declaration cannot be graded).**
   Callers that linted a corpus with a frontmatter-less or unterminated
   `config.md` and saw it pass now get one `critical` and a non-zero exit under
   `--fail-on critical`. Add or close the `---` fence; `lint --fix` cannot do it
   for you and says so. The declaration was never honoured before, so the
   corpus was being judged by the current rule set regardless.
2. **New registry findings (ladder step 1).** `registry-path-missing` and the
   private-placeholder entry are `info`; gates on `--fail-on warning` or
   `--fail-on critical` do not see them.

**Distribution and runtime packaging.** The generated Codex and OpenCode
mirrors and `knowmade_control/_build.py` copies are regenerated by the sync
scripts; no manifest, marketplace or install-instruction version changes.

#### Shared JSON I/O and redaction (`claude/sprint-f-shared`)

Shared JSON I/O and redaction (`claude/sprint-f-shared`): #91 (shared JSON
reader), #94 (checkpoint scanner formats, shared redaction module), #88
(session redactor), #92 (retract `--stdin` newline), #93 (verify and
adapter validators on malformed input). The upstream baseline is unchanged.

**Corpus rule set — unchanged (`rules/3`).** No lint rule changed.

**Deterministic CLI and protocol — fixes, one stricter resolution rule.**

- One JSON object reader (`fsio.read_json_object`) for `wikis.json`, the hub
  `config.json`, the adapter and specialist registries, checkpoint and
  adapter JSON inputs and source-lifecycle records: a UTF-8 BOM is accepted
  everywhere, and an unreadable, non-UTF-8, malformed or non-object file is
  one message naming the file (exit 1), never a traceback.
- Stricter: when `wikis.json` exists but cannot be read or parsed,
  `--wiki <slug>` now fails instead of falling back to an initialized
  `topics/<slug>`. Only a slug missing from a readable registry still falls
  back. A BOM or ANSI registry could previously send a command to a
  different root than the registry declared.
- Machine-local JSON receipts and the adapter registry are written as UTF-8
  without `\uXXXX` escapes (`fsio.write_json_atomic`); readers are unaffected.
- `retract --stdin` drops exactly one trailing LF or CRLF from the value,
  and its report carries `value_length_bytes` ("Value length: N bytes").
- `checkpoint verify` returns `{"status": "invalid"}` with exit 2 for every
  malformed manifest or report; an invalid manifest now yields that one
  error instead of the full list of follow-on checks.
- Checkpoint privacy scanner version 4: new token formats (`github_pat_`,
  `ASIA`, `sk_live_`/`sk_test_`/`rk_`, `glpat-`, `npm_`), UNC shares,
  Unicode `-PRIVATE` slugs, and whole private-key blocks. Bundles sealed by
  scanner 3 must be re-sealed: `verify` reports them `invalid` with a
  re-seal message, the rule every earlier scanner bump followed.

**Session helper.** Hook events and feedback previews are redacted with the
same table (modern API keys, URL userinfo, prose credentials); a feedback
prompt that matches any secret pattern is stored only as
`[REDACTED <category>]`. Promotion into a public topic no longer scrubs a
filesystem-root cwd.

**Distribution and runtime packaging.** New module
`knowmade_control/redaction.py` in every packaged copy. The sync scripts
stamp its body into `llm-wiki-session` (`scripts/sync-session-vendor.py`).
No manifest, marketplace or install-instruction version changes.

#### Hub index and source lifecycle (`claude/sprint-f-hub-source`)

Hub index and source lifecycle fixes from the round-2 consumer audit: #110
(archive topic/restore overwrite a curated hub `_index.md`) and #117
(source_lifecycle file-path imports, permissive config parsing and verify). The
upstream baseline is unchanged (v0.25.1).

**Corpus rule set — unchanged (`rules/3`).** No lint rule was added or changed
in this entry's scope. The unknown-`source_store`-key finding is exposed as
`source_lifecycle.config_warnings()` and a stderr note on every `source`
command; lint reports it as an `info` finding in the "Decisions" entry below.

**Deterministic CLI and protocol.**
- `archive topic` and `archive restore` rewrite only the generated topic table
  of the hub `_index.md`: the table between `<!-- knowmade:topics:start -->` and
  `<!-- knowmade:topics:end -->` when those markers exist, else the table whose
  header's first column is `Wiki` (or `Slug`). Header, extra columns, the rows
  of still-active topics and rows for slugs outside the registry stay byte for
  byte, as do every other section, the BOM and the newline style; rows of
  archived topics are removed and new rows are built per header column. An
  index with no table gets a marked one appended; a missing index gets the
  template, whose counts and `Last updated:` line sit inside
  `knowmade:stats` markers and are the only counts refreshed (#110).
  `topic_title` reads `title` through `frontmatter.py`, not a regex over the
  whole file.
- `source_lifecycle.py` loads by file path (`spec_from_file_location`) without
  touching `sys.path` (#117).
- `source_store` scalars take a trailing `# comment`; `persistent` accepts
  `true/false`, `yes/no`, `on/off` in any case, and any other value is a
  `LifecycleError` (a critical lint finding) where it was silently truthy.
  `default_decision` must be `review-required`, `local` or `git`. Unknown keys
  are noted on stderr by every `source` command (#117).
- `source decide --retain` is optional: it falls back to
  `source_store.default_decision` when that is `local` or `git`, and otherwise
  fails as before (#117, `default_decision` was never read).
- `source verify` adds `--require-originals` (exit 1 when a `retention: local`
  original is missing; exclusive with `--portable`) and always reports
  `originals_available`, `originals_unavailable`, `missing_originals` and a
  per-row `retention`. Plain `verify` still tolerates a missing original (#117).
- `source receive` adds `--canonical-url <http(s) url>` and `--retrieved-at
  <iso>`, recorded as `canonical_url` and `retrieved_at` in the local record of
  a new source. The pointer schema is unchanged; carrying `retrieved_at` into
  the pointer stays with #118 (record capture provenance and expose
  resolve/paths/import commands for adapters).

**Distribution and runtime packaging.** The generated Codex and OpenCode
mirrors and the bundled control-plane copy are regenerated; no manifest,
marketplace or install-instruction version changes.

#### Privacy guards and state ignore rules (`claude/sprint-f-privacy`)

Developed since `v0.5.0.25.1` on `claude/sprint-f-privacy`: the privacy part of
Sprint F (ROADMAP.md). The upstream baseline is unchanged (v0.25.1).

**Corpus rule set — unchanged (`rules/3`); one new check enters the ladder.**

- **C21 `tracked-machine-state`** (#98, engine-written state directories are
  not ignored). In a git work tree, lint reports machine-local state that git
  tracks, or that no ignore rule covers: `/.sessions/`,
  `/.skills/registry.json`, `**/.audit/`, `**/.librarian/`,
  `**/.session-events.jsonl`, `**/.session-checkpoint.json`, `**/.obsidian/`,
  `**/.wiki-local/`. It goes through `rule_lint.issue_versioned` with
  `introduced_in = 3`: `info` for `rules/3` corpora and hubs (no verdict
  changes, so no rule-set bump), silent for `rules/2` and `rules/1`, `warning`
  from `rules/4` and `critical` from `rules/5`. No exception is claimed: the
  machine state is a privacy risk, but `--fix` (hubs) and the writers below now
  add the ignore rules, so the normal ladder is enough.
- The hub private-topic ignore check (C19, a documented ladder exception
  below) now judges every hub inside a git work tree, not only a repository
  root, and probes the live `topics/<slug>-PRIVATE/` as well as the archived
  location (#87, private topics committable in a nested hub). A hub nested in a
  dotfiles or project repository without an ignore rule now gets the existing
  `critical`; this closes a detection gap in an existing check.
- New hub `warning` (#89, source store defaults outside `-PRIVATE`):
  `topics/.wiki-local/` or `topics/sources/` left by the old shared default.
  A documented ladder exception, below.
- The unknown-file check (C12) accepts a wiki's own source-store directories
  (`.wiki-local/`, `sources/` when `source_store` resolves there) at its root.
  This only relaxes a check.

**Deterministic CLI and protocol — behaviour changes and their classification.**

| Change | Classification | Ladder |
|---|---|---|
| `archive topic` / `archive restore` of a `-PRIVATE` topic refuse in any git work tree (git's `rev-parse --is-inside-work-tree` from the hub, falling back to a `.git` in any ancestor when git cannot answer), not only at a repository root; `git check-ignore` is always asked from the hub directory (#87) | Exceptional (privacy): the old guard let a nested hub archive private topics into committable paths. Applied immediately | Note 1 |
| `source receive/extract/decide` on a hub topic (`<hub>/topics/<slug>` or `topics/.archive/<slug>`) resolve every `source_store` path the config does not set inside the topic (`.wiki-local/...`, `sources/...`) instead of `topics/` (#89). The project-local `.wiki` defaults (`../` = project root) are unchanged | Exceptional (privacy): private originals and text landed in shared, un-ignored `topics/` siblings. Applied immediately | Note 2 |
| `source receive/extract/decide` refuse a `-PRIVATE` wiki whose unset store paths would resolve outside its directory (exit 2, `source lifecycle error: refusing to write sources of private wiki ...` on stderr, the module's existing error contract); explicit `source_store` keys are honoured (#89) | Exceptional (privacy), new refusal | Note 2 |
| `lint --fix` on a hub in a git work tree appends the missing machine-state rules (above) to the hub `.gitignore` under one comment, idempotently and in the file's newline style; `llm-wiki-session enable` appends `/.sessions/` the same way; `specialist init` appends the machine-state block and its `--json` report gains `gitignore_appended` (#98) | Additive (new writes to `.gitignore` only when git would otherwise commit the state; a new JSON key) | None |
| The hub init template in `commands/wiki.md` lists the machine-state rules beside `topics/**/*-PRIVATE/` (#98) | Agent spec change | None |
| `wikis.json` rewrites (`archive`, `restore`, `lint --fix`) keep non-ASCII text as UTF-8 instead of `\uXXXX`, keep the file's indent unit (spaces count or tab), key order, newline style and BOM (#100) | Output-format fix for a tracked file; the JSON value is unchanged | None |

**Deprecation notes.**

1. **Nested hubs and private archive (ladder bypass: privacy).** A hub inside a
   larger repository now needs `topics/**/*-PRIVATE/` in an ignore file git
   honours before `archive`/`restore` move a private topic. Run
   `llm-wiki lint --fix <hub>`; it writes the rule into the hub's own
   `.gitignore`. There is no override.
2. **Source-store location for hub topics (ladder bypass: privacy).** Records,
   pointers and originals that an earlier engine wrote to `topics/.wiki-local/`
   and `topics/sources/` are no longer found by `extract`, `decide` or `verify`
   for that topic. Move them into the topic (`<topic>/.wiki-local/`,
   `<topic>/sources/`; the record's `original_path` is absolute and must be
   updated with the move), or keep the old layout for a public topic by
   setting the keys explicitly (`local_originals: ../.wiki-local/originals`,
   and so on). Hub lint warns while those directories exist.

**Distribution and runtime packaging.** No manifest, marketplace or
install-instruction version changes. `scripts/llm-wiki-session` vendors the
`/.sessions/` rule (`SESSIONS_IGNORE_RULE`); `hub.STATE_IGNORE_RULES` is the
control plane's copy of the full block. The generated mirrors carry the
changed modules.

#### Decisions: capture default, private registry rows (`claude/sprint-f-decisions`)

Two owner decisions from the Sprint F audit round: #76 (Codex session hooks
default ON, decision 3a) and #90 (private topic slugs, titles and descriptions
in tracked hub files, decision 4a), plus two lint follow-ups. The upstream
baseline is unchanged (v0.25.1).

**Corpus rule set — unchanged (`rules/3`).** Every lint change below is a new
finding that enters at `info` through `rule_lint.issue_versioned` (introduced in
`rules/3`, so `warning` in the next set; silent for `rules/2` and `rules/1`),
or reports an existing condition as a finding instead of a traceback. No
corpus needs to re-declare.

**Deterministic CLI and protocol — behaviour change (session capture).**

| Change | Classification | Ladder |
|---|---|---|
| `llm-wiki-session` capture is opt-in: with no `<hub>/.sessions/config.json`, `load_config` returns `enabled: False`, so every `hook --if-enabled` (all six Codex hooks, including `SessionStart` rehydration) exits 0 and writes nothing, not even a `.sessions/` directory. An existing config keeps its own `enabled` value; `enable` writes the full default config with `enabled: true` and appends `/.sessions/` to the hub `.gitignore`; `disable` writes `enabled: false` (#76) | Behaviour change on the deterministic CLI/protocol surface: capture no longer starts implicitly. Privacy decision, so it is not delayed behind a deprecation window | Ladder bypass (privacy) |

**Deprecation notes.**

1. **Session capture no longer starts implicitly (ladder bypass: privacy).**
   Before, a hub without `.sessions/config.json` recorded every Codex prompt
   and tool call from the first trusted session. Now it records nothing until
   `llm-wiki-session enable` (or `@wiki session enable`) writes the config.
   Users who relied on the old default run `enable` once per hub; hubs that
   already have a config (from `enable` or `disable`) behave exactly as before.
   The Claude plugin is unchanged: it ships no hooks.

**Hub registry and index — decision 4a for #90.** `wikis.json` and the hub
`_index.md` are committed with the hub, so they carry a private topic's slug and
`path` (and `id`) but nothing from its content.

| Change | Classification | Ladder |
|---|---|---|
| `write_hub_index` (called by `archive topic` and `archive restore`) renders a `-PRIVATE` row as the slug link only: Title, Description and Summary cells are empty and the private `config.md` is never read. An existing private row has those three cells emptied; its other cells, the rest of the table and the file's BOM and newline style stay byte for byte (#90) | Output change to a tracked file: privacy fix | Ladder bypass (privacy) |
| `archive topic` / `archive restore` of a `-PRIVATE` topic no longer set `description` in the registry from the topic's title; an existing value is left alone and linted (#90) | Output change to a tracked file: privacy fix | Ladder bypass (privacy) |
| `lint` hub registry: a `*-PRIVATE` entry with a `description` is `private-registry-description`, through `rule_lint.issue_versioned` with `introduced_in = 3` (C19, #90) | New finding | `info` in `rules/3`, silent in `rules/2` and `rules/1`, `warning` from `rules/4` |
| The specialist index (`.skills/_index.md`) names enabled topics by slug only, as before; a regression test now pins that a private topic's title never appears in it (#90) | Test only | None |

**Lint follow-ups.**

| Change | Classification | Ladder |
|---|---|---|
| `check_hub_registry` reads `wikis.json` through `fsio.read_json_object`: a BOM is accepted (as before) and an unreadable, non-UTF-8, malformed or non-object registry is one `critical` finding on `wikis.json` with the shared message (before: a non-UTF-8 file ended lint with a traceback) (#91) | Fix: a traceback becomes the finding the old code already gave for malformed JSON | None |
| `check_source_lifecycle_pointers` emits `source_lifecycle.config_warnings()` as `source-store-unknown-key` on `config.md`, through `rule_lint.issue_versioned` with `introduced_in = 3` (C20, #117) | New finding | `info` in `rules/3`, silent in `rules/2` and `rules/1`, `warning` from `rules/4` |

This completes the lint wiring the hub-source sub-section above left open: the
corpus rule set is still `rules/3` and no verdict changes under it.

Two further deprecation notes:

2. **Private rows in a hub `_index.md` lose their text (ladder bypass:
   privacy).** The next `archive topic` or `archive restore` empties the
   Title/Description/Summary cells of every `-PRIVATE` row, including text a
   hub owner wrote by hand. Keep such notes in the private topic. Descriptions
   already in `wikis.json` are not removed for you: `lint` reports each one as
   `private-registry-description`; delete the key.
3. **New info findings (ladder step 1).** `private-registry-description` and
   `source-store-unknown-key` are `info`; gates on `--fail-on warning` or
   `--fail-on critical` do not see them.

**Distribution and runtime packaging.** `scripts/llm-wiki-session` and the
bundled control-plane copies are regenerated by the sync scripts; no manifest,
marketplace or install-instruction version changes. No flag changed, so
`references/cli.md` is unchanged.

### v0.5.0.25.1 — Sprint D of the 2026-10 audit

Developed since `v0.4.2.25.1`, alongside Sprint C (below): PR #78 "CLI-first
agent spec and context diet", under refactoring epic #67 (refactoring plan).
The upstream baseline is unchanged (v0.25.1). Recorded retroactively during
Sprint E; shipped with Sprints B, C and E in `v0.5.0.25.1`.

**Corpus rule set — unchanged (`rules/3`).** No check was added or changed.

**Deterministic CLI and protocol — unchanged.** No flag, output or state
machine changed. `references/cli.md` is generated from the argparse tree by
`scripts/generate-cli-reference.py`, and the lane fails when it is stale.

**Agent behaviour (the canonical `claude-plugin/` specs).** Command specs call
the deterministic CLI first and keep agent effort for the semantic passes. The
fuzzy-router table is one runtime-neutral file, `references/router.md`, read by
`/wiki` and by the Codex skill. Hub resolution is one template in
`references/hub-resolution.md`, stamped into each command by
`scripts/sync-command-prelude.py`. `SKILL.md` keeps a short core and loads the
split references (`skill-workflows.md`, `wiki-structure.md`, `wiki-articles.md`,
`wiki-format.md`, the `ingestion*` and `research-*` files) on demand.
Session-distillate promotion is exempt from the inbox-first rule.

**Distribution and runtime packaging.** The Claude plugin ships
`bin/llm-wiki-session` (a script only: it registers no hooks). The layout of
the Codex and OpenCode mirrors is unchanged. No manifest, marketplace or
install-instruction version changes.

### v0.5.0.25.1 — Sprint C of the 2026-10 audit

Developed since `v0.4.2.25.1`: refactoring epic #67 (refactoring plan) and
roadmap epic #68 (roadmap). The upstream baseline is unchanged (v0.25.1).

**Corpus rule set — unchanged (`rules/3`).** No field, severity or check was
added. The lint changes below correct how existing checks read links and report
drift; they do not introduce a stricter rule, so nothing climbs the ladder and
no corpus needs to re-declare. `rules/3`, `rules/2` and `rules/1` stay
supported (N-2).

**Deterministic CLI and protocol — additive changes.**

- `retract --allow-short` and `retract --max-file-bytes N` (#33, retract short
  values and memory).
- `identity assign --id` is honoured: it assigns that id, or refuses one that
  conflicts with the declared id (#39, config.md tracebacks).
- `rules check --path` reports `status: missing` (exit 2) for a path that is not
  a directory; the payload gains `path_exists` and `config_present` keys (#39).
  An existing directory without `config.md` stays `undeclared`.
- `adopt status --json` lists open `claims`, each with a `worker_status`
  (`active`, `stale` or `unknown`) (#40, adoption batch state machine).
- Adoption batch `state.json` gains `wiki_root_relative`, `staging`
  (`pending`, `interrupted` or complete) and `stage_error`. The wiki root is
  resolved from an existing `--wiki-root`, then `wiki_root_relative`, then the
  `<wiki>/inbox/.imports` layout, and only then the recorded absolute path
  (#36, adopt move before manifest; #40). Readers must ignore unknown keys, and
  old `state.json` files without them keep working.
- `source receive` returns `already_known` and `original_restored`; receiving an
  already-known source keeps its record instead of resetting it (#37, source
  receive and `verify --all`). `source verify --all` reports a corrupt pointer
  as a failed row instead of a traceback.
- `lint --json` `engine` gains `identity_source` (`build` or `manifest`), naming
  where the engine's version and rule set came from (R1.3, `_build.py`).
- The session hook gains an `index` subcommand (rebuilds the derived indexes
  under `.sessions/indexes`) and writes safe state file names (#43, session hook
  lost events). Indexes are no longer rebuilt on every event.
- `llm-wiki --help` lists `rules`, `adopt` and `source`; they are real argparse
  subparsers, and `llm-wiki-adopt` / `llm-wiki-adopt-batch` remain as thin
  shims over the same entry points (#64, `--help` namespaces).
- A file that already exists keeps its newline style and BOM when the engine
  rewrites it; new files are LF without BOM, as in `v0.4.1.25.1`. Readers
  accept a BOM and CRLF everywhere (#28, CRLF/BOM writers and readers).

**Deterministic CLI and protocol — behaviour changes and their classification.**

| Change | Classification | Ladder |
|---|---|---|
| `retract` refuses a value under 8 bytes unless `--allow-short`; encoded variants under 12 characters are never generated (#33) | Safety fix: a short value rewrites unrelated content across the whole scope. Exceptional (data loss), applied immediately | Deprecation note 1 |
| `adopt --force-unlock` refuses a live same-host holder; it still reclaims a stale or unidentifiable lock (#40) | Exceptional (unsafe destructive behaviour): the old flag deleted the lock of a running worker. Applied immediately | Deprecation note 2 |
| `lint` `engine.state` and `engine.drifted` no longer say `drift` for rule-set drift alone; the single `critical` finding carries it (#38, rule-set drift reported twice) | Reporting change on an existing machine-readable field; `engine.forms.rules`, `rule_set_status` and `effective_rule_set` are unchanged | Deprecation note 3 |
| Titled links to missing files are now reported (`[t](x.md "title")` was skipped) (#34, index and link normalisation) | Detection gap closed in an existing `warning` check; rule set unchanged, so no ladder step | Deprecation note 4 |
| `lint --fix` repairs a curated `_index.md` in place; it regenerates one wholesale only if it carries the generated marker or is a stub of headings (#34) | Fix that stops data loss; the generated-index output is unchanged | None (relaxes a destructive default) |
| `archive` / `restore` write the registry after the move and undo the move if the write fails (#48, CLI atomicity); `checkpoint seal` validates `--allow-finding` before rewriting the manifest | Ordering fix, no flag or output change | None |
| Checkpoint privacy scanner | Unchanged (scanner version 3) | None |
| Session ids drop `:` and `@`; an altered id gets a `-<sha8>` suffix and pre-fix state files migrate; a corrupt `.sessions/config.json` disables the hook with one warning (#43) | Bug fix for Windows state files; unaltered ids keep their names | None |
| Adapter entrypoints written with backslashes anchor to the adapter root; the adapter subprocess keeps non-secret Windows variables; `/c/...` and backslash registry paths read as drive paths on Windows only (#47, Windows adapters) | Windows-only correction; POSIX paths untouched | None |
| `adopt --exclude` matches case-sensitively on every OS; empty files are no longer duplicates of each other (#40) | Row ids are now identical per platform | None |

**Deprecation notes under the severity and deprecation ladder.** None of these
changes a corpus verdict under a declared rule set, so none walks the `info`,
`warning`, `critical` progression. Notes 1 and 2 are documented bypasses of the
ladder (the exceptional-critical clause); notes 3 and 4 narrow or correct
existing output. Each tells callers how to adapt.

1. **`retract` short values (ladder bypass: data loss).** Scripts that passed a
   value of fewer than 8 bytes now exit with an error that names the risk. Pass
   `--allow-short` to keep the old behaviour for a value you have verified is
   unique in the scope, or lengthen the value. The flag is permanent and the
   refusal is not scheduled to soften; scripts should add the flag now.
2. **`adopt --force-unlock` (ladder bypass: unsafe destructive behaviour).** A
   live holder on this host is never unlocked. Wait for the worker or stop it
   (`adopt status` shows `worker_status`), then retry; `--force-unlock` remains
   the way to reclaim a stale lock (dead pid) or one whose holder cannot be
   identified. There is no override for a live holder.
3. **`engine.state` / `engine.drifted` (reporting move).** Rule-set drift is
   reported once, as the critical lint finding. Consumers that tested
   `engine.state == "drift"` or `"rules" in engine.drifted` to detect it should
   read the finding, or `engine.forms.rules` / `rule_set_status`, which are
   unchanged. `state` and `drifted` still carry version and commit drift. The
   old behaviour is removed rather than deprecated: it reported one condition
   twice.
4. **Titled links to missing files (detection gap).** A corpus that passed
   because `[t](x.md "title")` was unreadable can now carry a `warning`
   (`Markdown link points to missing file` or `Index links to missing file`).
   Repair the link or the file; `lint --fix` removes dead index entries.
   `--fail-on warning` gates will see them; `--fail-on critical` gates do not.

**Distribution and runtime packaging.**

- Every packaged control plane carries the generated `knowmade_control/_build.py`
  (R1.3): in `scripts/knowmade_control/`, `claude-plugin/bin/`,
  `plugins/llm-wiki/bin/` and `plugins/llm-wiki-opencode/bin/` (see the build
  identity description above).
- The layout of `claude-plugin/bin/` is unchanged (`llm-wiki`,
  `llm-wiki-adopt`, `llm-wiki-adopt-batch`, `knowmade_control/`); only modules
  are added. The control plane is now `common`, `lint`, `indexing`, `hub`,
  `retract`, `specialists`, `adapters`, `checkpoints`, `frontmatter`, `fsio`,
  `markdown`, `roots`, `schema`, `engine`, `app_server`, `rules`, `rule_lint`,
  `identity`, `adoption`, `adoption_batch`, `adoption_legacy`, `adopt_cli`,
  `source_lifecycle`, `stdio` and `_build`. `legacy_cli.py` is a shim: it
  assembles the one parser from each module's `register_parser` and `run` and
  re-exports the old public names, so `scripts/llm-wiki` and the runpy-based
  tests keep working (R1.4, `KM-CLI-001`). The launcher no longer overwrites
  module globals (R1.1, no monkey-patching).
- `scripts/lib/codex-config.sh` is the one reader of Codex's `config.toml` for
  the bootstrap and verify scripts, and the bootstrap tolerates the Windows
  `os error 5` on a repeated install (#45, Codex bootstrap). This is the
  development path; release installs still pin a tag.
- The Codex benchmark records the delta of the cumulative `tokenUsage.total`
  across a turn, not the last `tokenUsage.last` block (#46, token benchmark).
  Reports recorded before this change lack `token_accounting` and are not
  comparable; no baseline is checked in.
- No manifest, marketplace or install-instruction version changes. Shipping this
  record is a separate release decision under the Release Process in `CLAUDE.md`.

## Rule-Set Implementation

What implements the corpus rule-set contract (`KM-CMP-002` through
`KM-CMP-006`). The policy above is normative; this section is the record of how
the code honours it.

- **Declaration.** A corpus declares its rule set with `knowmade_rule_set` in
  `config.md`. The current baseline is `rules/3`; the running engine supports
  `rules/3`, `rules/2` and `rules/1` (N-2). Package/plugin versions and checker
  SHAs remain separate compatibility forms (`llm_wiki_version`,
  `knowmade_checker_sha`; see `DESIGN.md`).
- **Commands.** `llm-wiki rules` exposes the current and supported sets,
  `rules check` compares a wiki against the engine, and `rules declare` writes
  a supported declaration. Brownfield adoption declares the current set on
  apply; `lint --fix` declares it for an otherwise undeclared
  initialized/legacy wiki.
- **Verdicts.** A current declaration is silent. N-1 and N-2 are accepted with
  one suggestion that names the explicit upgrade path. Too-old, future,
  invalid, or otherwise unsupported declarations are critical findings.
- **Drift is reported exactly once**, by `rule_lint.check_rule_set_contract`
  (called from `lint.check_engine_contract`). It emits the finding through
  `rule_lint.issue_versioned` (the ladder helper) with the exceptional-critical
  branch: a declaration outside the N-2 window cannot be graded by its own rule
  set, so the running engine states the finding and no supported set can lower
  it. `engine.contract_state` (`knowmade_control/engine.py`) still carries the
  rules form (`forms.rules`, `rule_set_status`, `effective_rule_set`) but keeps
  it out of `drifted` and `state`, which drive only the legacy commit/version
  warning.
- **Missing paths and configs.** `llm-wiki rules check --path <p>` reports
  `status: missing` (exit 2, with `path_exists: false`) when `<p>` is not a
  directory. An existing directory without `config.md` stays `undeclared` with
  `config_present: false`. A `config.md` that is missing or has no YAML
  frontmatter never produces a traceback: `adopt apply` refuses before writing
  anything, `identity assign` exits with a message (and refuses an `--id` that
  conflicts with the declared one), and `lint --fix` reports a warning that the
  declaration could not be written. Plain `lint` reports the unusable
  `config.md` itself as a critical finding, and the engine fields
  (`llm_wiki_version`, `knowmade_checker_sha`, ...) are read from the same
  frontmatter block as the rule set (`rules.config_fields`).
- **New strictness.** Existing lint checks form the bootstrap baseline. New
  stricter checks must use `knowmade_control.rule_lint.issue_versioned`: info in
  their introducing rule set, warning in the next, critical in the following
  set. Immediate critical severity is reserved for the documented
  security/privacy/data-loss exceptions below.
- **Fixture lane.** `tests/fixtures/rule-sets/manifest.json` parameterizes the
  golden corpus across every supported rule set; `tests/test-rule-sets.sh` is
  part of the local push gate.
- **Other controls.** `KM-CMP-007` (tagged releases with tag-pinned installs)
  and `KM-CMP-008` (upstream merges decoupled from release triggers) are
  release process rather than code: see Release Semantics and Upstream Merge
  Semantics above, and `CLAUDE.md` (Release Process).

### Documented ladder exceptions

Immediate critical, no rule-set bump. Each is recorded here with the reason the
normal ladder was bypassed, as the exception clause above requires.

- **Unreadable `config.md`** (knowmade#97; `lint.check_engine_contract`). An
  existing `config.md` without usable frontmatter is reported as critical in
  every supported rule set, through `rule_lint.issue_versioned` with
  `exceptional_critical` and the current rule set as target. Reason: the file
  that carries `knowmade_rule_set` cannot be read, so no declared rule set can
  grade it; before, the corpus silently fell back to "undeclared".
- **Hub private-topic ignore gap** (knowmade#29, `archive topic` escaping the
  gitignore boundary; `lint.check_hub_private_ignore`). A git-tracked hub whose
  ignore rules do not cover `topics/.archive/<slug>-PRIVATE/` is reported as
  critical in every supported rule set. Reason: `archive topic --private` would
  turn an ignored private wiki into a committable one, a privacy failure under
  the exception clause. The check fires when the hub is inside a git work tree
  (its root or nested in a larger repository, knowmade#87) and probes the live
  and the archived location; `lint --fix` appends `topics/**/*-PRIVATE/` in
  place, and hub roots carry no `knowmade_rule_set`, so no corpus declaration
  changes meaning.
- **Hub source-store directories beside the topics** (knowmade#89,
  `lint.check_hub_topic_store_siblings`). `topics/.wiki-local/` or
  `topics/sources/` is reported as `warning` in every supported rule set.
  Reason: an older `source_store` default wrote private originals and text
  there, outside every `-PRIVATE` ignore rule, a privacy failure under the
  exception clause; `warning` rather than `critical` because the directories
  may hold only public sources and nothing is repaired automatically. Hub
  roots carry no `knowmade_rule_set`, so no corpus declaration changes
  meaning.
- **Tracked paths under a `-PRIVATE` directory** (knowmade#112, privacy scan;
  `privacy.tracked_private_paths`, kind `private-tracked`). A hub whose git
  index holds a path under any `*-PRIVATE/` directory, other than the anchor
  basenames the hub lists in `private_anchors` (default none), is reported as
  `critical` in every supported rule set by hub lint (C25, wired in Sprint G)
  and by `llm-wiki privacy scan --check tracked-private`. Reason: a tracked private file is already committable
  and, once pushed, public; the `.gitignore` check (`check_hub_private_ignore`)
  cannot see a file added with `git add -f` or tracked before the ignore rule
  existed, a privacy failure under the exception clause. It fires only when the
  hub is inside a git work tree (its root or nested in a larger repository) and
  never repairs anything. Hub roots carry no `knowmade_rule_set`, so no corpus
  declaration changes meaning.
