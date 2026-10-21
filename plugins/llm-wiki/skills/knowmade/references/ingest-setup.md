# Ingest Setup Protocols

The rare branches of `/knowmade:ingest`, moved out of the command so they are
read only when taken: handoffs to other commands, archive awareness, the
`--project` scope and topic wiki routing. Read the section for the branch in
hand, not the file top to bottom. The other branches read their own owners:
original-source retention in [`source-lifecycle.md`](source-lifecycle.md), the
`--new-topic` manual init fallback in [`wiki-setup.md`](wiki-setup.md) § Init
manual fallback, and the by-hand writing fallback in
[`wiki-format.md`](wiki-format.md) and [`indexing.md`](indexing.md).

## Handoffs

### Collection handoff

If the user is trying to import a bounded upstream corpus rather than one source
item, stop and hand off to `/knowmade:ingest-collection` with the same wiki flags.
Signals include: "import wiki", "mirror wiki", "bulk ingest", "ingest
collection", "ingest repo", "import repo", "split CSV into messages", "Wayback
snapshots", MediaWiki dumps (`.xml`, `.xml.bz2`, `.xml.gz`), MediaWiki
`api.php`, CDX API URLs, or a GitHub/GitLab repo plus words like "all", "docs",
"BIPs", "wiki", or "collection".

Do not reduce a collection to the repository README or one fetched web page.
Collection imports need a manifest plus one raw child source per upstream
page/spec/proposal.

### Inventory and dataset handoff

If the user asks to "track", "watch", "keep inventory", "candidate", "queue",
or "decide later" instead of ingesting now, stop and hand off to
`/knowmade:inventory`. A durable candidate record is better than ingesting a source
the user has not accepted.

If the source is a large dataset, archive, compressed dump, database, or
query-oriented corpus, stop and hand off to `/knowmade:dataset` or
`/knowmade:ingest-collection` as appropriate. Do not squeeze large data into
`raw/data/` because it is technically readable.

## Archive awareness

Do not ingest into archived topic wikis by default. If `--wiki <name>` resolves
to `status: archived` or a path under `topics/.archive/`, stop and ask the user
to restore it with `/knowmade:archive restore <name>` or rerun with
`--include-archived`. If the user explicitly includes archived content, write
only inside that archived topic path and keep the topic archived.

**--include-archived**: Explicitly allow ingestion into an archived target
wiki. Not used for auto-classification.

## Project scope (`--project <slug>`)

**--project <slug>**: Tag the processed raw artifact with `project: <slug>` frontmatter. The intake item still starts in `inbox/`, and the processed artifact lands in the normal `raw/<type>/` location (sources are shared across projects by design). The project tagging propagates later when sources are compiled into wiki articles. See [`projects.md`](projects.md).

If `--project <slug>` is set, verify the project exists at `<wiki-root>/output/projects/<slug>/WHY.md`. If not, fail early: `Project "<slug>" does not exist. Create it first with /knowmade:project new <slug> "goal".`

There is no ambient project focus — pass `--project` explicitly when you want project scope. The focus-session mechanism (`.wiki-session.json`) was removed in the v0.2 projects simplification; see `projects.md` § "Focus" for the rationale.

## Topic wiki routing

When the wiki resolved to the hub (HUB) and no `--wiki` flag was provided, route content to the right topic wiki AFTER fetching the source content (title, summary, tags are now known).

**Skip this step entirely if** `--wiki` or `--local` was explicitly provided.

### Single item (no --inbox)

If `--auto-classify` is set, or the user didn't specify `--wiki`:

1. Read `HUB/wikis.json` to list topic wikis
2. For each active topic wiki with a `config.md`, read the description/scope (first 5 lines is enough). Skip archived topics.
3. Match the source's title + summary + tags against wiki scopes
4. Present a simple numbered choice:
   ```
   Route to:
   1) ai-security — "AI security, agent safety, prompt injection..."
   2) geo — "GEO & AI Search Optimization..."
   3) New wiki
   4) Skip (leave in hub inbox for later)
   ```
5. User picks a number. Use that wiki as target for the rest of the flow.

If only one topic wiki exists, still ask (don't assume). If zero topic wikis exist, ask whether to create one or ingest to hub raw/.

### Batch mode (--inbox)

When `--inbox` is set and no `--wiki` was provided, classify items as a batch:

1. Scan inbox, fetch/read each item to get title + summary
2. For each item, match against topic wiki scopes
3. Present a classification table:
   ```
   Inbox routing:
   | # | Title | → Wiki | Match |
   |---|-------|--------|-------|
   | 1 | Attention Is All You Need | ai-basics | strong |
   | 2 | Agent Auth IETF Draft | ai-security | strong |
   | 3 | K8s RBAC Guide | (new: cloud-infra) | weak |
   | 4 | Random cooking blog | (skip) | none |
   ```
4. User confirms: `y` (process all), `edit` (reassign items), `abort`
5. Process items grouped by target wiki — all ai-security items together, etc.
6. Items marked "skip" stay in inbox untouched
7. Items routed to a "new" wiki trigger the init flow first, then ingest

**Performance note**: Fetch all items first (parallel if possible), classify in one pass, then process. Don't fetch-classify-ingest one at a time — that's fragile if interrupted mid-batch.
