# Core Principles

The eighteen numbered principles every Knowmade workflow applies. `SKILL.md` keeps a
short summary and points here; read this file before the first write of a
session, and whenever a workflow cites a principle by number.

## Core Principles

1. **Indexes are a derived cache.** The `.md` files and their YAML frontmatter are the source of truth. `_index.md` files are a cached view rebuilt on read when stale. Always read indexes first for navigation — but before trusting one, stale-check it (file count vs row count). See [indexing.md](indexing.md) for the Derived Index Protocol.

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

11. **Archive is quiet preservation.** Archived topic wikis live under
`HUB/topics/.archive/<slug>/` and are hidden from normal semantic workflows.
They remain structurally maintainable through explicit archive/lint operations.
Deep queries may surface archived index matches separately, but archived content
must not influence new synthesis unless the user explicitly includes it.

12. **Session capture is operational memory.** Harness session digests live in
`HUB/.sessions/` or `.wiki/.sessions/`, not in topic `raw/` by default.
Automated hooks may capture redacted checkpoints, but promotion into topic wikis
is explicit and user-directed.

13. **Feedback is candidate memory.** User corrections, preferences, approvals,
and plan-acceptance signals may be captured as redacted candidates under
`HUB/.sessions/feedback/`, but generic acknowledgements are ignored and durable
wiki promotion remains explicit.

14. **Private goals guide private work.** In private topic wikis, goals,
desires, needs, constraints, and current decisions are the North Star for
research, Ideas, portfolio choices, planning, output, audit, assessment, and
implementation support. Keep a short goal brief in context. Goals guide
relevance but are not factual evidence and must not leak into public topics.
Reading a `-PRIVATE` sibling is always allowed; the write-guard in
`references/knowmade-profile.md` § Private Data Boundary: Read Freely,
Write-Guard governs what reaches public files.

15. **One human inbox, many topic workers.** `HUB/inbox/` is a shallow staging
queue. A dispatcher classifies items by subject and privacy, routes them to one
or more topic inboxes, and only then lets topic-specific workers ingest and
compile them.

16. **Private adapters are a content-free external execution plane.** Their
machine-local registry lives under `~/.config/llm-wiki/`, not in the hub.
Adapter repositories contain tool code only; real inputs and all runtime
outputs remain in separately controlled external data planes. Only reviewed
`wiki-safe` candidates may enter the wiki through normal provenance and
compilation workflows. See
[adapters.md](adapters.md).

17. **Specialists are bounded methods, not credentials.** Personal,
instruction-only specialist packages live locally under `HUB/.skills/` and are
enabled per active topic. Loading one never grants tools, write access, professional
authority, or permission to spawn agents. Select the minimum useful method,
preserve disagreement, and record its version/hash. See
[specialists.md](specialists.md).

18. **Project checkpoints need comprehensive coverage and a privacy seal.** See
[checkpoints.md](checkpoints.md).
