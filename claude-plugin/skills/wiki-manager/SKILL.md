---
name: wiki-manager
description: >
  Manage Knowmade practical knowledge bases: initialize, dispatch a global hub
  inbox, ingest/import through inbox-first intake, compile/query,
research/audit, collect catalogs, track inventory/datasets/Ideas, review
Idea/Project portfolios, manage public/private topic pairs, goals,
archives/sessions/feedback, private adapters, Project Knowledge Checkpoints
Export, and generate outputs.
  Activates for /wiki commands; wiki or knowledge-base work; ingest, dispatch,
  hub inbox, collect, catalog, compile, query, audit, librarian, inventory,
  idea, portfolio, business ideas, projects, datasets, private goals, archives,
  sessions, feedback, checkpoints, private adapters, adapter routing, explicit
  wiki skill-factory requests, personal specialist skills, expert review,
  specialist selection, provenance, external resource actions (including
  requests to read, review, or edit hosted documents), or questions in a
  directory with .wiki/ or a configured hub.
tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Bash
  - WebFetch
  - WebSearch
---

# Knowmade Manager

You manage a Knowmade practical knowledge base. Source material is staged in a
topic `inbox/`, or first in `HUB/inbox/` when it is not classified yet. After
extraction and provenance capture, processed immutable artifacts are written to
`raw/` and incrementally compiled into interconnected markdown articles. The
active agent is both compiler and query engine; Obsidian is an optional viewer.

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
protocol: fallbacks, the macOS permission diagnostic, registry paths, archived
entries and wiki-requirement variants all live there.

The preferred Knowmade config looks like:

```json
{ "hub_path": "~/knowmade" }
```

All references to `~/wiki/` below mean HUB.

## Wiki Location

**Topic sub-wikis are the default.** HUB is a hub: content lives in `HUB/topics/<name>/`. Public and private versions are sibling topic roots, `topics/<slug>/` and `topics/<slug>-PRIVATE/` (suffix exact, uppercase). Wiki location order (`--local`, `--wiki <name>`, `.wiki/` in the current directory, else HUB) is defined in [references/hub-resolution.md](references/hub-resolution.md). Layout: [references/wiki-structure.md](references/wiki-structure.md); file formats: [references/wiki-format.md](references/wiki-format.md) and [references/wiki-articles.md](references/wiki-articles.md). The full section (isolation rationale, private topic contents, collection slug naming, empty-hub rule) is in [references/skill-workflows.md](references/skill-workflows.md) § Wiki Location.

## Core Principles

1. **Indexes are a derived cache.** The `.md` files and their YAML frontmatter are the source of truth. `_index.md` files are a cached view rebuilt on read when stale. Always read indexes first for navigation — but before trusting one, stale-check it (file count vs row count). See [references/indexing.md](references/indexing.md) for the Derived Index Protocol.

2. **Raw is immutable.** Normal workflows never modify ingested sources. Explicit retraction runs first via hidden-input `scripts/llm-wiki retract`; content, archives, and sessions cannot veto it.

3. **Inbox first.** New source material enters a topic `inbox/`; unclassified
mixed-topic material may enter `HUB/inbox/` and must be dispatched before
ingestion. `raw/` is the processed evidence layer, not the human drop zone.
Session distillates (`wiki ll`, session and feedback promotion) are the one
exception and write `raw/notes/` directly (`knowmade-profile.md`).

4. **Articles are synthesized, not copied.** A wiki article draws from multiple sources, contextualizes, and connects to other concepts. Think textbook, not clipboard.

5. **Dual-linking for Obsidian + agents.** Cross-references use both `[[wikilink]]` (for Obsidian graph view) and standard markdown `[text](path)` (for agent navigation) on the same line: `[[slug|Name]] ([Name](../category/slug.md))`. Bidirectional when it makes sense.

6. **Frontmatter is structured data.** Every `.md` file has YAML frontmatter with title, summary, tags, dates. This makes the wiki searchable without full-text scans.

7. **Incremental over wholesale.** Compilation processes only new sources by default. Full recompilation is expensive and explicit (`--full`).

8. **Honest gaps.** When answering questions, if the wiki doesn't have the answer, say so. Never hallucinate. Suggest what to ingest to fill the gap.

9. **Multi-wiki awareness.** When querying, answer from the primary wiki first. Then peek at sibling wiki indexes (via `HUB/wikis.json`) for relevant overlap, `-PRIVATE` siblings included (write-guard: `references/knowmade-profile.md` § Private Data Boundary). Flag connections but never merge content across wikis.

10. **Chunk large writes.** Never create files longer than ~200 lines in a single Write call — the API stream idles during large generations, causing timeout errors. Write the skeleton (frontmatter + headers + first section) first, then use sequential Edit calls to append remaining sections. For plans, articles, and raw notes: write one section per tool call.

11. **Archive is quiet preservation.** (full text: references/skill-workflows.md § Core Principles)

12. **Session capture is operational memory.** (full text: references/skill-workflows.md § Core Principles)

13. **Feedback is candidate memory.** (full text: references/skill-workflows.md § Core Principles)

14. **Private goals guide private work.** In private topic wikis, goals,
desires, needs, constraints, and current decisions are the North Star for
research, Ideas, portfolio choices, planning, output, audit, assessment, and
implementation support. Keep a short goal brief in context. Goals guide
relevance but are not factual evidence and must not leak into public topics.
Reading a `-PRIVATE` sibling is always allowed; the write-guard in
`references/knowmade-profile.md` § Private Data Boundary: Read Freely,
Write-Guard governs what reaches public files.

15. **One human inbox, many topic workers.** (full text: references/skill-workflows.md § Core Principles)

16. **Private adapters are a content-free external execution plane.** (full text: references/skill-workflows.md § Core Principles)

17. **Specialists are bounded methods, not credentials.** (full text: references/skill-workflows.md § Core Principles)

18. **Project checkpoints need comprehensive coverage and a privacy seal.** See
[references/checkpoints.md](references/checkpoints.md).

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

When this skill activates outside an explicit wiki invocation:

1. Resolve the hub path (see Hub Path section above), then check if `HUB/_index.md` or `.wiki/_index.md` exists
2. Read the master `_index.md` to assess if the wiki might cover the user's question
3. If relevant content exists → read the relevant articles and answer with citations
4. If no relevant content → answer normally, optionally suggest: "This could be added to your wiki with `/wiki:ingest`"
5. When peeking at sibling wikis, only read their `_index.md` — do not read full articles unless the user asks. Skip archived sibling wikis by default; in deep mode, archived index matches may be reported separately. A `-PRIVATE` sibling may be peeked; label what it contributed as private and apply `references/knowmade-profile.md` § Private Data Boundary before anything is written.

When giving any boot, resume, or "where you left off" briefing, start with the
active wiki identity: `<wiki-name> booted from <wiki-root-path>`. Prefer the
`config.md` title; for local `.wiki/` projects, fall back to the parent
directory name; for `HUB/topics/<slug>/`, fall back to the slug. Include this
line even when there is nothing in flight to resume.

If the user asks whether they can trust a wiki artifact, requests an audit,
mentions provenance or drift, or asks for content verification beyond a normal
query, use the Audit workflow instead of treating it as plain Q&A.

## Workflows

Route free-text requests with [references/router.md](references/router.md),
the single routing table; do not route from memory. Index: workflow, command,
then the file in `references/` to read before acting. Flow summaries and the
Cross-Workflow Inventory Awareness rules: `skill-workflows.md` § Workflow Flows.

- Ingestion, `wiki ingest`: `ingestion.md` (+ `source-lifecycle.md` only when original bytes are kept).
- Hub Inbox Dispatch: `hub-dispatch.md`, `knowmade-profile.md`.
- Collection ingestion, `wiki ingest-collection`: `ingestion-collections.md`.
- Knowmade profile (identity, goals, public/private, migration): `knowmade-profile.md`.
- Private adapters, `wiki adapter`: `adapters.md`.
- Personal specialists, `wiki specialist`: `specialists.md`.
- Inventory, `wiki inventory`: `inventory.md`.
- Ideas, `wiki idea`: `ideas.md`. Portfolio, `wiki portfolio`: `portfolio.md`.
- Collect, `wiki collect`: `inventory.md`, `research-infrastructure.md`.
- Dataset registry, `wiki dataset`: `datasets.md`.
- Archive, `wiki archive`: `archive.md`.
- Compilation, `wiki compile`: `compilation.md`.
- Query, `wiki query` and Search: index first, then Grep; no reference file (`indexing.md` when an index is stale).
- Linting, `wiki lint`: `linting.md`.
- Librarian, `wiki librarian`: `librarian.md`. Audit, `wiki audit`: `audit.md`.
- Research, `wiki research`: `research-protocol.md`, `research-modes.md`, `research-infrastructure.md`.
- Sessions, `wiki session`: `sessions.md`. Feedback Curator, `wiki feedback`: `feedback.md`.
- Output, `wiki output`: `output-protocol.md`. Lessons Learned, `wiki ll`: `ll-protocol.md`.
- Plan, `wiki plan`: `plan-protocol.md`. Assess, `wiki assess`: `assess-protocol.md`. Refresh, `wiki refresh`: `refresh-protocol.md`.
- Retract, `wiki retract`: `retract-protocol.md`.

## Links: File Paths and URLs

Use full absolute paths and `[short text](url)` links in saved-output messages, flush-left, one per line. Full rules: `skill-workflows.md` § Links: File Paths and URLs.

## Activity Log

Every wiki operation appends to `log.md` in the wiki root. Format: `## [YYYY-MM-DD] operation | Description`. See [references/wiki-format.md](references/wiki-format.md) § log.md Format for full format. Never edit or delete existing log entries except during an explicit user-directed retraction with `--remove-from-logs`.

## Operational Sections

Moved verbatim to [references/skill-workflows.md](references/skill-workflows.md); apply them as before. **Structural Guardian**: after any write operation, and on activation when the wiki has not been linted in 7+ days, run the quick structure check (silent when clean; never block the user's request). Also there: Confidence Scoring, Compilation Nudge (suggest compile at 5+ uncompiled sources), Concurrency, Session Management.
