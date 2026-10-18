# Skill Workflows

Detail moved verbatim out of `SKILL.md` so the always-loaded skill stays small. `SKILL.md`
keeps the identity, hub and privacy pointers, a principle summary, ambient behavior, and a
one-line-per-workflow index; everything below is the prose that index points at. The
numbered principles are in `principles.md`. `HUB/` means the resolved hub path
(`hub-resolution.md`).

## Wiki Location

**Topic sub-wikis are the default.** HUB is a hub — content lives in `HUB/topics/<name>/`. Each topic gets isolated indexes, sources, and articles. This keeps queries focused and prevents unrelated topics from polluting each other's search space.

Public and private versions are sibling topic roots in the same hub:
`topics/<slug>/` and `topics/<slug>-PRIVATE/`. The suffix is exact and uppercase.
Private topics may contain personal goals, constraints, sources, sessions, and
outputs. Public topics must not cite private paths or material without an
explicit publication/redaction pass.

For collection families that will grow across subjects, prefer kind-first topic
slugs such as `memes-bitcoin`, `memes-ethereum`, `tools-bitcoin`, or
`examples-seedqr`. Use subject-first slugs when the subject is the primary
research area and the collection is only one artifact within that topic.

Wiki location order (`--local`, `--wiki <name>`, `.wiki/` in the current
directory, else HUB) is defined in
[references/hub-resolution.md](hub-resolution.md).

When a command targets the hub and the hub has no content, suggest creating a topic sub-wiki instead.

See [references/wiki-structure.md](wiki-structure.md) for the complete directory layout; file formats are in [references/wiki-format.md](wiki-format.md) and [references/wiki-articles.md](wiki-articles.md).

## Workflow Flows

### Ingestion
See [references/ingestion.md](ingestion.md).
Flow: Source (URL/file/text/tweet) → stage in topic `inbox/` → fetch/read →
extract metadata and provenance → write processed artifact to `raw/{type}/` →
move the intake item to `inbox/.processed/` → update indexes → suggest compile
if many sources are uncompiled.

### Hub Inbox Dispatch
See [references/hub-dispatch.md](hub-dispatch.md) and
[references/knowmade-profile.md](knowmade-profile.md).
Flow: human dumps unclassified material into `HUB/inbox/` → dispatcher reads
active topic scopes and public/private siblings → classify by subject and
privacy → copy or move into one or more topic `inbox/` directories, or leave in
`.needs-human/` → append a hub `dispatch` log entry → topic workers ingest.

### Knowmade Profile
See [references/knowmade-profile.md](knowmade-profile.md).
Use it before changing project identity, goal-directed behavior,
public/private boundaries, inbox-first intake, or migration rules.

### Collection Ingestion
See [references/ingestion-collections.md](ingestion-collections.md).
Flow: structured upstream collection (Git repo, BIP-style proposal set, MediaWiki dump/API) → upstream item inventory → write a `raw/repos/` manifest plus immutable child sources → rebuild raw indexes → optionally compile synthesized clusters. Use `/wiki:ingest-collection` for bulk imports; never recursively crawl HTML.

### Private Adapters
See [references/adapters.md](adapters.md).
Flow: explicitly register an existing local adapter checkout → declare read and
write roots → verify the manifest hash and `describe` handshake → execute a v1
JSON request through the bundled deterministic CLI → verify artifact paths and
hashes → leave all outputs external → optionally review only `wiki-safe`
candidates and promote the smallest useful evidence through normal wiki writes.
If the guide offers an agent-started interactive authorization helper, launch
it yourself and present only its short local handoff link rather than a long
terminal command or provider URL. Never clone, install, update, publish, or
auto-promote an adapter.

### Personal Specialist Skills
See [references/specialists.md](specialists.md).
Flow: maintain user-owned instruction-only `SKILL.md` methods under
`HUB/.skills/` → validate structure and safety boundaries → explicitly enable
stable names per active topic → select zero to three by task features, normally
one → give selected methods the same bounded evidence packet → verify and
synthesize by evidence strength → record name, version, and content hash. Use
`/wiki:specialist suggest` for an index-first candidate scan and
`/wiki:specialist apply` for a bounded review. Do not treat titles such as CFO,
doctor, MBA, or PhD as credentials or capability upgrades.

### Inventory
See [references/inventory.md](inventory.md).
Flow: Run an inventory fit check → track durable wiki-adjacent things (items, Ideas, ingest candidates, entities, corpora, questions, tasks, watch items) as markdown records under `inventory/` → answer list requests from indexes/frontmatter as compact chat tables or bullets → optionally save derived views under `inventory/views/` → optionally convert legacy queue-like outputs through explicit dry-run-first migration. Inventory migration is additive and human-gated. Be explicit when something is too small for inventory, too large and should be a dataset/collection, or outside wiki scope.

### Ideas
See [references/ideas.md](ideas.md).
Flow: capture in `inventory/ideas/` → research/shape → require approval →
freeze `BRIEF.md` and promote. The Idea keeps lineage; the Project owns delivery.

### Portfolio
See [references/portfolio.md](portfolio.md).
Flow: read active Idea indexes and Project `WHY.md` files → cross-link only
explicit lineage → render separate read-only tables without copying records.

### Collect
See [references/inventory.md](inventory.md).
Flow: Scope a bounded catalog request → infer scale and media policy → search
for candidate artifacts, examples, resources, entities, tools, media, or memes
→ fetch only promising context pages → deduplicate aliases/reposts/rehosts →
record `found_in_context` provenance and media metadata → save a
`type: collection` output under `output/collect-<slug>-YYYY-MM-DD.md` →
optionally create inventory records when the list is small and durable enough,
or one corpus record when it is medium/large. Collect outputs are useful to the
LLM as a staging layer before promotion into `raw/`, `wiki/`, `inventory`, or
`datasets`; they do not replace raw sources for factual claims. Download and
hash bounded public binary media into `output/assets/collect-<slug>/` by
default for media-bearing collections, never store binaries in `raw/`, and use
defensive download settings: timeouts, file-size caps, content-type checks, and
IPv4 retry (`curl -4`) when media hosts hang on IPv6. Do not pretend that "all"
means exhaustive beyond the stated strategy and limit.

### Dataset Registry
See [references/datasets.md](datasets.md).
Flow: Keep large or external datasets out of the wiki while indexing them through `datasets/<slug>/MANIFEST.md` → store locations, schema notes, small samples, profiles, and query recipes → answer list requests from `datasets/_index.md` plus manifest frontmatter only → optionally convert legacy dataset outputs through explicit dry-run-first migration. Dataset migration is additive and never copies the underlying data into the wiki.

### Archive
See [references/archive.md](archive.md).
Flow: Move whole topic wikis from `HUB/topics/<slug>/` to
`HUB/topics/.archive/<slug>/` → mark `wikis.json` with `status: archived` →
hide them from normal query/compile/research/collect/output/maintenance context →
restore by moving the folder back and setting `status: active`. Do not archive
individual raw sources or compiled articles in v1.

### Compilation
See [references/compilation.md](compilation.md).
Flow: Survey uncompiled sources → plan articles → classify (concept/topic/reference) → write/update articles with cross-references → update all indexes.

### Query
Flow: Read `_index.md` → identify relevant articles by summary/tag → read articles → follow See Also links → Grep for additional matches → synthesize answer with citations → note gaps → peek active sibling wikis. Supports `--resume` to reload context after a session break — reads session files, recent log entries, wiki stats, and last-updated articles to produce a "where you left off" briefing. Deep queries may peek archived sibling indexes in a separate Archived Matches section; full archived reads require explicit user intent.

### Linting
See [references/linting.md](linting.md).
Flow: run `llm-wiki lint --json` (structure, indexes, links, provenance, placement, registry) → agent passes for coverage, freshness and the rows marked `agent` → report → optionally `--fix`. Default lint keeps active material healthy and reports archived topics as skipped. Use `--wiki <slug> --include-archived` per archived slug for explicit archived structural maintenance (the command's `--archived-only` does that for all of them; it is not a CLI flag).

### Librarian
See [references/librarian.md](librarian.md).
Flow: Scan the active topic's compiled `wiki/` layer → score staleness, quality,
source-chain integrity, and link health → write `.librarian/scan-results.json`
and `.librarian/REPORT.md` → recommend ranked next actions. Optional
`--passes conventions` (or legacy `schema`) may write a proposal output for
topic-guide improvements. Topic guides are default for new topic wikis; older
wikis can adopt one with `llm-wiki schema adopt` or `llm-wiki lint --fix`. Librarian
must not rewrite `schema.md` without explicit user acceptance.
Librarian is the article-health advisor; it does not replace lint's structural
checks or audit's broader trust review.

### Audit
See [references/audit.md](audit.md).
Flow: Run or reuse the librarian pass → inspect artifact dependency chains across `output/`, `wiki/`, and `raw/` → escalate with fresh source checks and targeted research until trust verdicts converge → write `.audit/REPORT.md`.

### Search
Flow: Scan indexes for summary/tag matches → Grep full-text → rank results → present.

### Cross-Workflow Inventory Awareness
Inventory is first-class operational state, not a silo. Other workflows should
notice it without treating it as factual evidence:

- Ingest and ingest-collection: if the user wants to track before ingesting, or
  a source is too large/ambiguous, suggest an inventory record. When an
  inventory candidate is ingested, link the resulting raw or collection
  manifest from the record.
- Dataset: link dataset manifests to inventory records when the user cares
  about next actions, priority, acceptance state, or why the corpus matters.
- Compile and query: use inventory to surface gaps, candidates, and next
  actions, but cite raw/wiki sources for factual claims.
- Collect: write the catalog as an output first, then create per-item inventory
  records only for small durable lists; use one corpus record for medium/large,
  unstable, or media-heavy collections.
- Research, audit, librarian, refresh, plan, and output: propose inventory
  records for durable follow-ups, stale items, source queues, or watch lists,
  but show a sample before creating a larger backlog.

### Sessions
See [references/sessions.md](sessions.md).
Flow: Opt in with `session enable` → harness hooks append redacted events under
`HUB/.sessions/queue/` → session state and markdown digests update at tool-count,
compaction, stop/session-end, or manual checkpoints → `session rehydrate` returns
a compact context block → `session promote` explicitly copies the distilled
digest into a topic `raw/notes/` note. Automated capture is allowed; automated
promotion is not.

### Feedback Curator
See [references/feedback.md](feedback.md).
Flow: trusted user-prompt hooks or `feedback capture` classify high-signal user
corrections, preferences, approvals, and plan-acceptance turns → append redacted
candidates under `HUB/.sessions/feedback/` → `feedback list/show` review them →
`feedback promote` explicitly writes selected candidates into topic `raw/notes/`.
Ignore generic acknowledgements unless manually captured.

### Output
Protocol: [`output-protocol.md`](output-protocol.md). Flow: Gather relevant articles → generate artifact (summary/report/slides/etc) → save to `output/` → update indexes.

### Lessons Learned (ll)
Protocol: [`ll-protocol.md`](ll-protocol.md). Flow: Scan session for error→fix patterns, corrections, discoveries → extract structured lessons → write to `raw/notes/` with `type: lessons-learned` → optionally update relevant articles → optionally suggest CLAUDE.md rules.

## Links: File Paths and URLs

Terminal links break when they wrap to a second line. Rules for all wiki operations:

1. **Full absolute paths** — expand `~`, HUB, and all relative segments. Relative paths are not clickable.
2. **Markdown link syntax for URLs** — use `[short text](url)`, never bare long URLs that wrap and break.
3. **No indentation before links** — indentation eats terminal width. Put links flush-left on their own line.
4. **One link per line** — don't embed a long path mid-sentence. Break it out:
   ```
   Saved to:
   /Users/name/wiki/topics/my-topic/output/report-2026-04-08.md
   ```

Applies to ingest, compile, collect, research, output, assess.

## Confidence Scoring

Wiki articles include a `confidence` field in frontmatter: `high`, `medium`, or `low`.

- **high**: Multiple peer-reviewed sources agree, well-established knowledge
- **medium**: Single source, or sources partially agree, or recent findings not yet replicated
- **low**: Anecdotal, single non-peer-reviewed source, or sources disagree

When answering queries, note confidence levels. When linting, flag `low` confidence articles for review.

## Compilation Nudge

Track uncompiled sources by comparing `raw/_index.md` ingestion dates against the last compile date in `_index.md`. If 5+ uncompiled sources exist after an ingestion, suggest: "You have N uncompiled sources. Run `/wiki:compile` to integrate them."

## Structural Guardian

Automatically run a quick structural check when any of these triggers occur:

### Triggers
- **After any write operation** (ingest, compile, collect, research, output, inventory, dataset, archive) — verify what was just written
- **When the skill activates** and the wiki hasn't been linted in 7+ days (check "Last lint" in `_index.md`)
- **When content is found in the wrong place** — articles in the global hub instead of a topic sub-wiki
- **When a user mentions wiki problems** — "wiki is broken", "empty", "missing", "wrong"
- **When no wiki exists** (first-run) — switch to guided onboarding flow instead of showing a command list. Walk the user through topic selection → init → first action suggestion. See `commands/wiki.md` § "If no wiki exists".

### Quick Structure Check (lightweight, runs inline — not a full lint)

1. **Hub integrity**: The hub (HUB) should contain `wikis.json`, `_index.md`, `log.md`, `topics/`, optional `.sessions/`, optional `inbox/` for global intake, and optional `.skills/`. Validate `.skills/` against the instruction-only package and active-topic allowlist contract. If `raw/`, `wiki/`, `inventory/`, `datasets/`, `output/`, or `config.md` exist at the hub level → **warn, do not delete**. These may hold user data from an older wiki layout. Suggest `/wiki:lint --fix`, which will move contents to the appropriate topic wiki, repair archive registry drift, or quarantine to a topic `inbox/.unknown/` per C11/C12/C16/C17/C19 in `references/linting.md`.

2. **Index freshness**: For the active topic wiki, compare actual file counts in `raw/`, `wiki/`, `inventory/`, and `datasets/` subdirectories against the rows in their `_index.md`. Ignore maintenance/report areas such as `.librarian/` and `.audit/`. If mismatched → auto-fix by regenerating the affected directory index from frontmatter and removing dead entries.

3. **Orphan detection**: Check if any `.md` files exist in wiki directories but are not listed in any `_index.md`. If found → add them to the index.

4. **Missing directories and legacy metadata**: Verify core topic wiki subdirectories exist (`raw/articles/`, `raw/papers/`, `wiki/concepts/`, `wiki/references/`, `output/`, etc.). If missing → create them with empty `_index.md`. Treat `inventory/` and `datasets/` as lazy optional layers: repair their indexes if they already exist, but do not create completely absent optional trees unless the current inventory or dataset workflow needs them. For older compiled articles, `lint --fix` may infer `category`, `summary`, dates, and `volatility` from the file location and existing body/frontmatter, and may rewrite fuzzy raw-source refs to exact `raw/...md` paths when the match is unambiguous.

5. **wikis.json sync**: Check that all topic sub-wikis under `HUB/topics/` are registered in `wikis.json`. Store hub-owned topic paths as portable relative paths (`topics/<slug>`), not `/Users/<name>/...` absolute paths. If a directory exists but isn't registered → add it. If a registered path is stale but `HUB/topics/<name>` exists → repair the path. If registered but no matching directory exists → remove the entry.

   Archived topic sub-wikis under `HUB/topics/.archive/` should be registered
   with `path: topics/.archive/<slug>` and `status: archived`. Do not include
   them in active status/query/compile defaults.

6. **Log existence**: Verify `log.md` exists in the active wiki and at the hub. If missing → create it.

### Behavior

- **Silent when clean** — don't report anything if everything checks out
- **Auto-fix trivial issues** — missing indexes, unregistered wikis, orphan files. Just fix and note in log.
- **Warn on structural problems** — content in wrong place, missing directories, stale indexes. Tell the user what's wrong and suggest `/wiki:lint --fix`.
- **Never block the user's request** — run the check, fix what you can, report issues, then continue with what the user actually asked for.

## Concurrency

Multiple agent sessions can safely read and write to the same wiki simultaneously. No locks are needed.

- **Indexes** are derived from the actual files on disk. If two sessions write articles at the same time, the next read rebuilds the index from whatever files exist. Both rebuilds converge to the same correct result.
- **log.md** is append-only with small atomic writes. Concurrent appends are safe. The explicit privacy-retraction exception rewrites the file atomically and should not run concurrently with other wiki writes.
- **Article/source files** are written independently. Two sessions creating different files never conflict. Two sessions editing the same file is unlikely and handled by last-write-wins (acceptable for a wiki — the content is always rebuildable from raw sources).

See [references/indexing.md](indexing.md) for the Derived Index Protocol.

## Session Management

### Research Session Registry

When a `--min-time` research or thesis session is active, the wiki root contains a `.research-session.json` or `.thesis-session.json` file.

Durable provenance should also live in the wiki root:

- `.session-events.jsonl` — append-only event log for replayable history
- `.session-checkpoint.json` — latest compact summary for resume briefings and audits

The session registry files are ephemeral crash-recovery state. The event log and
checkpoint are the durable provenance trail.

**Structural Guardian behavior**:
- If a session file exists with `status: "in_progress"` and `start_time` > 7 days ago → warn: "Stale research session found. Clean up with `/wiki:research` or delete manually."
- Session files are ephemeral — never included in structural health checks or index counts
- Session files should NOT be committed to git
- `.session-events.jsonl` and `.session-checkpoint.json` should normally be preserved after completion so `/wiki:audit` can classify provenance as `replayable` instead of `partial`

### Harness Session Capture

Automated Codex/Claude/OpenCode/Gemini session capture uses `HUB/.sessions/`
(or `.wiki/.sessions/` for local wikis). This is a hidden operational layer for
redacted hook events, state JSON, derived indexes, and markdown session digests.
It is not topic evidence until explicitly promoted.

See `references/sessions.md` for the storage layout, config modes, hook adapter
contract, rehydration behavior, and promotion rules.
