---
name: knowmade
description: >
  Manage Knowmade practical knowledge bases in OpenCode: dispatch hub intake,
  ingest/import through topic inboxes, shape/promote Ideas, review portfolios,
  track private goals, inventory/datasets, archives, compile/query/lint/audit,
  research/plan, manage sessions, feedback, private adapters, and personal
  specialists, and generate outputs.
  Activates when the user mentions wiki workflows, knowledge-base management,
  ingestion, collection ingestion, import wiki, collect, catalog, curate,
  find all, idea, turn idea into project, portfolio, business ideas, projects, inventory, source queue,
  candidate list, watch list, backlog, dataset, large data, data registry,
  dataset manifest, compilation, querying, linting, audit, research, librarian,
  scan quality, article quality, content review, output drift, provenance,
  archive wiki, archive topic, restore wiki, private adapter, adapter registry, skill-factory,
  checkpoints,
  personal specialist, specialist skill, specialist reviewer, expert lens,
  adapter route, adapter doctor, adapter run, read, review, or edit a hosted external resource such as a document,
  session capture, capture context, rehydrate,
  resume from session, lessons learned, implementation plan, or uses
  wiki-related shorthand in a repo with .wiki/, ~/wiki/, or a
  configured hub path.
---

# Knowmade Manager

You manage a Knowmade practical knowledge base. Source material is staged in a
topic `inbox/`, or first in `HUB/inbox/` when it is not classified yet. After
extraction and provenance capture, processed immutable artifacts are written to
`raw/` and incrementally compiled into interconnected markdown articles. OpenCode
is both compiler and query engine; Obsidian is an optional viewer.

## OpenCode Integration Notes

This skill is loaded as an instruction file. OpenCode does not have Claude-style `/knowmade:*` slash commands or Codex-style `@knowmade` invocations. Treat those examples as shorthand for the equivalent natural-language request. For example, `/knowmade:compile` means the user is asking you to compile the wiki.

OpenCode's built-in tools (`read`, `write`, `edit`, `glob`, `grep`, `bash`, `webfetch`, `websearch`) map directly to the tools this skill requires. Web search requires `OPENCODE_ENABLE_EXA=1` in the environment.

**Permissions**: OpenCode sandboxes file access to the project directory. The configured Knowmade hub is external. Add `external_directory` permissions in `opencode.json` for the hub and `{ "~/.config/llm-wiki/**": "allow" }`; for the preferred default, allow `~/knowmade/**`. If your hub uses another absolute path (for example iCloud Drive), add that path instead. Alternatively, use `--local` mode to keep everything in `.wiki/` inside the project.

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

When this skill activates outside of an explicit wiki-related request:

1. Resolve the hub path (see Hub Path section above), then check if `HUB/_index.md` or `.wiki/_index.md` exists
2. Read the master `_index.md` to assess if the wiki might cover the user's question
3. If relevant content exists → read the relevant articles and answer with citations
4. If no relevant content → answer normally, optionally suggest: "This could be added to your wiki — just ask me to ingest it."
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

Route free-text requests with [references/router.md](references/router.md),
the single routing table; do not route from memory. Index: workflow, command,
then the file in `references/` to read before acting. Flow summaries and the
Cross-Workflow Inventory Awareness rules: `skill-workflows.md` § Workflow Flows.

- Ingestion, `wiki ingest`: `ingestion.md` (+ `source-lifecycle.md` only when original bytes are kept).
- Hub Inbox Dispatch: `hub-dispatch.md`, `knowmade-profile.md`.
- Collection ingestion, `wiki ingest-collection`: `ingestion-collections.md`.
- Knowmade profile (identity, goals, public/private, migration): `knowmade-profile.md`.
- Private data boundary (read freely, write-guard): `private-boundary.md`.
- Private adapters, `wiki adapter`: `adapters.md`.
- Personal specialists, `wiki specialist`: `specialists.md`.
- Inventory, `wiki inventory`: `inventory.md`.
- Ideas, `wiki idea`: `ideas.md`. Portfolio, `wiki portfolio`: `portfolio.md`.
- Collect, `wiki collect`: `inventory.md`.
- Dataset registry, `wiki dataset`: `datasets.md`.
- Archive, `wiki archive`: `archive.md`.
- Compilation, `wiki compile`: `compilation.md`.
- Query, `wiki query` and Search: index first, then Grep; no reference file (`indexing.md` when an index is stale).
- Linting, `wiki lint`: `linting.md`.
- Librarian, `wiki librarian`: `librarian.md`. Audit, `wiki audit`: `audit.md`.
- Research, `wiki research`: `research-protocol.md`, `research-modes.md`; they name the phase file (`research-agents`, `-credibility`, `-staging`, `-rounds`, `-sessions`, `-plan`) each step reads.
- Sessions, `wiki session`: `sessions.md`. Feedback Curator, `wiki feedback`: `feedback.md`.
- Output, `wiki output`: `output-protocol.md`. Lessons Learned, `wiki ll`: `ll-protocol.md`.
- Plan, `wiki plan`: `plan-protocol.md`. Assess, `wiki assess`: `assess-protocol.md`. Refresh, `wiki refresh`: `refresh-protocol.md`.
- Retract, `wiki retract`: `retract-protocol.md`.
- Federation registry, `llm-wiki registry` (known roots, locate an id), and cross-root references, `llm-wiki ref` (`knowmade://` URIs): `federation.md`.

## Links: File Paths and URLs

Use full absolute paths and `[short text](url)` links in saved-output messages, flush-left, one per line. Full rules: `skill-workflows.md` § Links: File Paths and URLs.

## Activity Log

Every wiki operation appends to `log.md` in the wiki root. Format: `## [YYYY-MM-DD] operation | Description`. See [references/wiki-format.md](references/wiki-format.md) § log.md Format for full format. Never edit or delete existing log entries except during an explicit user-directed retraction with `--remove-from-logs`.

## Operational Sections

Moved verbatim to [references/skill-workflows.md](references/skill-workflows.md); apply them as before. **Structural Guardian**: after any write operation, and on activation when the wiki has not been linted in 7+ days, run the quick structure check (silent when clean; never block the user's request). Also there: Confidence Scoring, Compilation Nudge (suggest compile at 5+ uncompiled sources), Concurrency, Session Management.
