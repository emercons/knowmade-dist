---
name: knowmade
description: >
  Manage Knowmade practical knowledge bases in Codex: dispatch hub intake,
  ingest/import, shape/promote Ideas around private goals, review portfolios,
  track inventory/datasets, archive, compile/query/lint/audit, research/plan,
  manage public/private topic pairs, sessions, private adapters, and personal
  specialists, and generate outputs.
  Activates when the user mentions wiki workflows, knowledge-base management,
  ingestion, collection ingestion, import wiki, collect, catalog, curate,
  find all, idea, turn idea into project, portfolio, business ideas, projects,
  inventory, source queue, hub inbox, dispatch inbox, private goals,
  candidate list, watch list, backlog, dataset, large data, data registry,
  dataset manifest, compilation, querying, linting, audit, research, librarian,
  scan quality, article quality, content review, output drift, provenance,
  archive wiki, archive topic, restore wiki, private adapter, adapter registry, skill-factory,
  checkpoints,
  personal specialist, specialist skill, specialist reviewer, expert lens,
  adapter route, adapter doctor, adapter run, read, review, or edit a hosted external resource such as a document,
  session capture, capture context, rehydrate,
  resume from session, implementation plan, or uses
  /wiki-style shorthand in a repo with .wiki/, ~/wiki/, or a configured hub path.
---

# Knowmade Manager

You manage a Knowmade practical knowledge base. Source material is staged in a
topic `inbox/`, or first in `HUB/inbox/` when it is not classified yet. After
extraction and provenance capture, processed immutable artifacts are written to
`raw/` and incrementally compiled into interconnected markdown articles. Codex
is both compiler and query engine; Obsidian is an optional viewer.

## Codex Plugin Notes

Codex plugins package skills, MCP servers, apps, and metadata. They do not register Claude-style custom `/knowmade:*` commands. Treat any `/wiki`, `/knowmade:*`, or command-flag examples in this skill and its references as shorthand for the same workflow expressed in natural language, or via explicit `@knowmade` invocation.

Knowmade is this downstream project. `llm-wiki` is upstream. Preserve the
upstream topic-wiki, provenance, schema, Ideas, Projects, session, query, and
audit disciplines while applying Knowmade's practical, goal-directed, private
and Codex-compatible workflow. Read
[references/knowmade-profile.md](references/knowmade-profile.md) before identity,
privacy, source-flow, or migration decisions.

## Hub Path

**Resolution**: At the start of every operation, resolve **HUB** from
`~/.config/llm-wiki/config.json` (`hub_path`, with `~/wiki/` only when no config
exists). [references/hub-resolution.md](references/hub-resolution.md) is the one
protocol: fallbacks, the macOS permission diagnostic, registry paths and
archived entries all live there.

The preferred Knowmade config looks like:

```json
{ "hub_path": "~/knowmade" }
```

All references to `~/wiki/` below mean HUB.

## Wiki Location

**Topic sub-wikis are the default.** HUB is a hub: content lives in `HUB/topics/<name>/`. Public and private versions are sibling topic roots, `topics/<slug>/` and `topics/<slug>-PRIVATE/` (suffix exact, uppercase). Wiki location order (`--local`, `--wiki <name>`, `.wiki/` in the current directory, else HUB) is defined in [references/hub-resolution.md](references/hub-resolution.md). Layout: [references/wiki-structure.md](references/wiki-structure.md); file formats: [references/wiki-format.md](references/wiki-format.md) and [references/wiki-articles.md](references/wiki-articles.md). The full section (isolation rationale, private topic contents, collection slug naming, empty-hub rule) is in [references/skill-workflows.md](references/skill-workflows.md) § Wiki Location.

## Core Principles

Eighteen numbered principles govern every workflow: read
[references/principles.md](references/principles.md) before the first write of a
session. In short: indexes are a derived cache, raw is immutable, intake is
inbox-first, articles synthesize and dual-link, gaps are stated honestly, large
writes are chunked, and private goals guide private work under the write-guard.

## Adapter Routing

For an action plus URL, run `adapter route --intent <effect> --resource <url>
--json` before ingestion or generic tool advice, even when the user did not say
"wiki." On a match, read its adapter-owned guide; provider steps live there. A
URL alone is not write authorization. See
[references/adapters.md](references/adapters.md).

Explicit `wiki skill-factory <request>` selects the registered named adapter;
do not invent a URL route. Run show/doctor and read its guide. It never
activates ambiently, and its external candidates remain disabled; installation,
topic enablement, commit, and publication are separate actions.

## Ambient Behavior

The skill also covers ingest/import through inbox-first intake, inventory, datasets, Ideas and Projects portfolios, archives, sessions, feedback, private adapters, personal specialists and external resource actions.

When this skill activates outside of an explicit `@knowmade` invocation or `/wiki`-style shorthand:

1. Resolve the hub path (see Hub Path section above), then check if `HUB/_index.md` or `.wiki/_index.md` exists
2. Read the master `_index.md` to assess if the wiki might cover the user's question
3. If relevant content exists → read the relevant articles and answer with citations
4. If no relevant content → answer normally, optionally suggest: "This could be added to your wiki; ask `@knowmade` to ingest it."
5. When peeking at sibling wikis, only read their `_index.md` — do not read full articles unless the user asks. Skip archived sibling wikis by default; in deep mode, archived index matches may be reported separately. A `-PRIVATE` sibling may be peeked; label what it contributed as private and apply `references/private-boundary.md` before anything is written.

When giving any boot, resume, or "where you left off" briefing, start with the
active wiki identity: `<wiki-name> booted from <wiki-root-path>`. Prefer the
`config.md` title; for local `.wiki/` projects, fall back to the parent
directory name; for `HUB/topics/<slug>/`, fall back to the slug. Include this
line even when there is nothing in flight to resume.

If the user asks whether they can trust a wiki artifact, requests an audit,
mentions provenance or drift, or asks for content verification beyond a normal
query, use the Audit workflow instead of treating it as plain Q&A.

## Workflows

Route free-text requests with `references/router.md`, the single routing
table shared with the Claude `/wiki` router; do not route from memory. Then
choose the smallest matching workflow and load only the reference material you
need for it:

- `ingest` → `references/ingestion.md`; add `references/source-lifecycle.md` only when original bytes are retained
- `ingest-collection` → `references/ingestion-collections.md`
- hub inbox dispatch → `references/hub-dispatch.md` and `references/knowmade-profile.md`
- `adapter` and private-adapter execution → `references/adapters.md`
- `collect` → `references/inventory.md`
- `inventory` → `references/inventory.md`
- `idea` → `references/ideas.md`
- `dataset` → `references/datasets.md`
- `archive` → `references/archive.md`
- `compile` → `references/compilation.md` and `references/indexing.md`
- `query` → read the relevant `_index.md` files first, then only the articles
  needed to answer
- `lint` → `references/linting.md`
- `audit` → `references/audit.md`
- `research` → `references/research-protocol.md` and `references/research-modes.md`; they name the per-phase file (`research-agents`, `-credibility`, `-staging`, `-rounds`, `-sessions`, `-plan`) each step reads
- `plan` → `references/plan-protocol.md`
- `assess` → `references/assess-protocol.md`
- `output` → `references/output-protocol.md`
- `ll` (lessons learned) → `references/ll-protocol.md`
- `refresh` → `references/refresh-protocol.md`
- `retract` → `references/retract-protocol.md`
- `specialist` → `references/specialists.md`
- `portfolio` → `references/portfolio.md`
- `project` → `references/projects.md`
- `checkpoint` → `references/checkpoints.md`
- `librarian` → `references/librarian.md`
- directory layout, `wikis.json` registry, file placement, init → `references/wiki-structure.md`
- index, log, raw-source, naming and tag formats → `references/wiki-format.md`
- config, topic guide, article, source-reference, volatility, linking and output formats → `references/wiki-articles.md`
- hub lookup and path handling → `references/hub-resolution.md`
- `session` (capture, automated hooks, rehydration, promotion) → `references/sessions.md`
- `feedback` (curation, corrections, approvals, candidate promotion) → `references/feedback.md`
- workflow flow summaries, Structural Guardian, confidence scoring, concurrency, session management → `references/skill-workflows.md`

Private adapters are explicitly trusted local executables. Resolve the bundled
`bin/llm-wiki` from the installed plugin root containing this skill; in a source
checkout use `scripts/llm-wiki`. Never clone or update an adapter, store its
registration in the hub, pass unregistered paths, or import outputs
automatically. Verify the manifest/handshake, run a v1 JSON request, keep
`private` and `bulk` artifacts external, and review `wiki-safe` candidates
before any normal wiki write.

Collect requests create bounded catalogs of discoverable things: artifacts,
examples, resources, entities, tools, media, memes, or source candidates. Infer
scale and media policy, record aliases plus `found_in_context` provenance,
deduplicate candidates, write a `type: collection` output at
`output/collect-<slug>-YYYY-MM-DD.md`, then create inventory only when the list
is small and durable enough; otherwise create or suggest one corpus record.
Download and hash bounded public binary media into
`output/assets/collect-<slug>/` by default for media-bearing collections, never
put binaries in `raw/`, and use defensive download settings: timeouts,
file-size caps, content-type checks, and IPv4 retry (`curl -4`) when media
hosts hang on IPv6. Use kind-first topic slugs such as `memes-bitcoin`,
`memes-ethereum`, or `tools-bitcoin` for collection families that can grow
across subjects. Never present "all" as exhaustive beyond the stated strategy
and limit.

Inventory is first-class operational state, not a silo. Ingest, collection, and
collect workflows should suggest inventory when the user wants to track or
decide later.
Dataset manifests should link to inventory records when next actions or
acceptance state matter. Compile and query may surface inventory gaps, but
factual claims still need raw/wiki sources. Collect, research, audit,
librarian, refresh, plan, output, and assess may propose durable follow-ups as
inventory records, but larger pivots should start with a small sample preview.

Ideas live under `inventory/ideas/`. Route fuzzy capture, research, shaping,
and promotion through `references/ideas.md`; require approval, preserve lineage,
and keep delivery truth in the Project.

Keep the first response short and action-oriented. Read deeper references only
after the user intent is clear or a write action is needed.

## Operational Rules

- Use absolute file paths in saved-output messages and markdown links for URLs.
- Append to `log.md` for every wiki write operation; never rewrite old log entries.
- Keep large writes chunked into multiple edits rather than one long generation.
- Read `_index.md` files before broader scans, and treat indexes as derived data.
- Use article `confidence` fields when answering and flag weak sourcing when seen.
- If structure or placement looks wrong, use the `lint --fix` workflow from
  `references/linting.md` instead of inventing a one-off repair path.

