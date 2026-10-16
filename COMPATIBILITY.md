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

#### Corpus divergence from upstream

Knowmade's corpus rules equal those of the absorbed upstream baseline except
where this list says otherwise. A divergence is recorded here, classified, and
mirrored as one line in `KNOWMADE_VS_LLM_WIKI.md`.

- **Five-point `confidence` scale** (knowmade#121). Lint accepts `high`,
  `medium-high`, `medium`, `medium-low` and `low` (`CONFIDENCE_VALUES` in
  `scripts/knowmade_control/lint.py`); upstream accepts `high`, `medium`, `low`
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
