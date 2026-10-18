---
description: "Ingest source material into an active wiki using Knowmade's inbox-first flow. Accepts URLs, file paths, PDFs, freeform text, or processes the inbox. Supports X/Twitter status URLs through a fetch fallback chain."
argument-hint: "<url|filepath|\"text\"> [--type articles|papers|repos|notes|data] [--title \"Title\"] [--inbox] [--keep] [--wiki <name>] [--local] [--auto-classify] [--new-topic <name>] [--project <slug>] [--include-archived]"
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(ls:*), Bash(wc:*), Bash(date:*), Bash(mv:*), Bash(mkdir:*), Bash(basename:*), Bash(file:*), Bash(curl:*), Bash(mktemp:*), Bash(rm:*), Bash(pdftotext:*), Bash(python3:*), Bash(scripts/llm-wiki:*), Bash(${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki:*), WebFetch, WebSearch
---

## Your task

<!-- hub-prelude:start -->
**Resolve the wiki.** A few file reads, not a search: never `find`, Explore, or scan the filesystem. Full protocol and edge cases: `skills/wiki-manager/references/hub-resolution.md`.
1. HUB: `$LLM_WIKI_HUB` or read `$HOME/.config/llm-wiki/config.json` and use `hub_path` (expand only a leading `~`); `resolved_path` is a fallback cache; with no config use `$HOME/wiki`. If the hub is stat-able but reads fail with `Operation not permitted`, stop and report the launcher permission denial.
2. Wiki, first match: `--local` → `.wiki/`; `--wiki <name>` → `HUB/wikis.json` entry (`wikis.local.json` path first; stale path → `HUB/topics/<name>`); `.wiki/` in CWD; else HUB. Reject archived entries (`status: archived`, `topics/.archive/`) unless the command takes `--include-archived`.
3. `<slug>` and `<slug>-PRIVATE` are sibling topics in one hub: read private freely, write under the Write-Guard in `skills/wiki-manager/references/knowmade-profile.md` § Private Data Boundary.
<!-- hub-prelude:end -->
4. Read `<wiki>/_index.md` to verify. If missing and `--new-topic <name>` is set → create the topic wiki (see below). If missing and no `--new-topic` → stop with "No wiki found. Use `--new-topic <name>` to create one, or run `/wiki init` first."

Read the ingestion protocol at `skills/wiki-manager/references/ingestion.md`, the file formats at `skills/wiki-manager/references/wiki-format.md` (`_index.md`, `log.md`, raw source, naming and tag formats), and from the Knowmade profile at `skills/wiki-manager/references/knowmade-profile.md` only § Private Data Boundary: Read Freely, Write-Guard and § Inbox-First Source Lifecycle (including its exception). Read the directory layout at `skills/wiki-manager/references/wiki-structure.md` only when `--new-topic` creates a wiki, and `skills/wiki-manager/references/source-lifecycle.md` only for the original-source retention path below. Then ingest source material. New source material must be staged in or discovered from `inbox/`; `raw/` receives the processed extraction artifact after the agent has parsed the source.

### Original-source retention (CLI)

When the user wants the original bytes kept (PDFs, binaries, anything outside
the Markdown `raw/` layer), or the wiki declares a `source_store`, call the
deterministic source commands before writing `raw/`: `llm-wiki source receive`,
`extract`, `decide --retain local|git`, then `verify`, each with the wiki scope
flags (`--local`, `--wiki <name>`, `--path <wiki-root>`, `--hub <path>`). Locate
the CLI as `"${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki"` or, in a source checkout,
`scripts/llm-wiki`. The exact invocations are in
`skills/wiki-manager/references/source-lifecycle.md`. If the
CLI fails, report its message and stop; do not retain originals by hand.

### Collection handoff

If the user is trying to import a bounded upstream corpus rather than one source
item, stop and hand off to `/wiki:ingest-collection` with the same wiki flags.
Signals include: "import wiki", "mirror wiki", "bulk ingest", "ingest
collection", "ingest repo", "import repo", "split CSV into messages", "Wayback
snapshots", MediaWiki dumps (`.xml`, `.xml.bz2`, `.xml.gz`), MediaWiki
`api.php`, CDX API URLs, or a GitHub/GitLab repo plus words like "all", "docs",
"BIPs", "wiki", or "collection".

Do not reduce a collection to the repository README or one fetched web page.
Collection imports need a manifest plus one raw child source per upstream
page/spec/proposal.

### Inventory and dataset awareness

If the user asks to "track", "watch", "keep inventory", "candidate", "queue",
or "decide later" instead of ingesting now, stop and hand off to
`/wiki:inventory`. A durable candidate record is better than ingesting a source
the user has not accepted.

If the source is a large dataset, archive, compressed dump, database, or
query-oriented corpus, stop and hand off to `/wiki:dataset` or
`/wiki:ingest-collection` as appropriate. Do not squeeze large data into
`raw/data/` because it is technically readable.

After a successful ingest, check active inventory records by title/source when
cheap. If a matching record exists, report the suggested linkage and status
change (`sources:` add raw path, maybe status `ingested`) instead of silently
leaving the tracking state stale.

### Archive awareness

Do not ingest into archived topic wikis by default. If `--wiki <name>` resolves
to `status: archived` or a path under `topics/.archive/`, stop and ask the user
to restore it with `/wiki:archive restore <name>` or rerun with
`--include-archived`. If the user explicitly includes archived content, write
only inside that archived topic path and keep the topic archived.

### `--new-topic` branch

When `--new-topic` is set, override the standard resolution:
1. Derive a slug from the topic name: lowercase, hyphens, no special chars, max 40 chars
2. If HUB doesn't exist, create it (wikis.json + _index.md + log.md + topics/)
3. Create the new topic wiki at `HUB/topics/<slug>/` following the full init protocol (directory structure, .obsidian/, empty _index.md files, config.md, log.md)
4. Register in `HUB/wikis.json` using a portable relative path (`topics/<slug>`) and update hub `_index.md`
5. Target this new wiki for ingestion

### `--project <slug>` flag

**--project <slug>**: Tag the processed raw artifact with `project: <slug>` frontmatter. The intake item still starts in `inbox/`, and the processed artifact lands in the normal `raw/<type>/` location (sources are shared across projects by design). The project tagging propagates later when sources are compiled into wiki articles. See `references/projects.md`.

If `--project <slug>` is set, verify the project exists at `<wiki-root>/output/projects/<slug>/WHY.md`. If not, fail early: `Project "<slug>" does not exist. Create it first with /wiki:project new <slug> "goal".`

There is no ambient project focus — pass `--project` explicitly when you want project scope. The focus-session mechanism (`.wiki-session.json`) was removed in the v0.2 projects simplification; see `references/projects.md` § "Focus" for the rationale.

### Parse $ARGUMENTS

- **--new-topic <name>**: Create a new topic wiki and ingest the source into it. Works from any directory. Derives the wiki slug from the name (e.g., "knives" → `knives`, "machine learning" → `machine-learning`).
- **--inbox**: Process all files in the wiki's `inbox/` directory
- **--keep**: When processing inbox, keep originals (move to .processed/ instead of deleting)
- **--type**: Force source type (articles, papers, repos, notes, data). Default: auto-detect.
- **--title**: Override extracted title
- **--auto-classify**: For single items, classify into the best-matching topic wiki instead of requiring `--wiki`. Always active for `--inbox` when no `--wiki` is set.
- **--include-archived**: Explicitly allow ingestion into an archived target
  wiki. Not used for auto-classification.
- **Source**: Everything else — URL (starts with http), file path (contains / or .), or quoted freeform text

### If --inbox

Follow the inbox processing protocol from `references/ingestion.md`:

1. Scan `inbox/` for files (exclude `.processed/` and hidden files)
2. If empty, report: "Inbox is empty. Drop files into `<wiki-path>/inbox/` and run again."
3. Process each file according to its type
4. Move processed files to `inbox/.processed/` (unless `--keep`, then copy)
5. Update all indexes
6. Report summary
7. If 5+ items processed, suggest compilation

### If URL source

0. Stage the URL in `inbox/` as a `.url` or small markdown capture before
   extraction. This creates a visible intake trace even when the user did not
   drop a file manually.

1. Detect X.com/Twitter URLs (`x.com/*/status/*` or `twitter.com/*/status/*`):
   - **Grok MCP** (preferred): Check for `mcp__grok__*` tools. If available, use to fetch tweet. (a locally installed Grok MCP server, if any).
   - **FxTwitter**: Rewrite URL → `https://api.fxtwitter.com/user/status/ID`, WebFetch for JSON.
   - **VxTwitter**: Rewrite URL → `https://api.vxtwitter.com/user/status/ID`, WebFetch for JSON.
   - **Direct WebFetch**: Last resort, often blocked by login wall.
   - Full fallback chain details in `references/ingestion.md`.
   - Type: notes (unless overridden)

2. Detect GitHub URLs (`github.com/*`):
   - Use WebFetch with repo-specific extraction prompt
   - Type: repos (unless overridden)

3. Detect PDF URLs (`.pdf` extension or PDF content type):
   - Download to a temporary file
   - Extract to markdown with the PDF file ingestion flow in `references/ingestion.md`
   - Type: papers by default, unless overridden or clearly a legal/regulatory article

4. All other URLs:
   - Use WebFetch to extract article content
   - Auto-detect type from URL patterns (arxiv → papers, etc.)

### If file path source

0. If the file is not already inside `inbox/`, copy or move it into the wiki's
   `inbox/` before extraction. Do not place user-supplied files directly in
   `raw/`.
1. Read the inbox file
2. Auto-detect type from extension and content
3. For `.pdf`, extract to markdown. Try `pdftotext -layout` only if it works;
   otherwise create a temporary Python venv and use a PDF library such as
   `pypdf` or `pymupdf`. If the PDF is image-only and no OCR path is available,
   write a metadata stub with `extraction_status: ocr-needed`.
4. For structured data (.json, .csv), describe schema + sample for a single
   data source. If the user wants per-message markdown, hand off to
   `/wiki:ingest-collection --adapter csv-messages`.

### If quoted text source

1. Stage the text as a markdown capture in `inbox/`
2. Use the text as-is for extraction
3. Type: notes (unless overridden)
4. Derive title from first sentence if --title not provided

### Topic Wiki Routing (when no --wiki specified)

When the wiki resolved to the hub (HUB) and no `--wiki` flag was provided, route content to the right topic wiki AFTER fetching the source content (title, summary, tags are now known).

**Skip this step entirely if** `--wiki` or `--local` was explicitly provided.

#### Single item (no --inbox)

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

#### Batch mode (--inbox)

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

### For all sources

1. Ensure there is an intake item under `inbox/` unless this command is already
   processing `--inbox`
2. Generate slug: `YYYY-MM-DD-descriptive-slug.md`
3. Write the processed extraction artifact to `raw/{type}/` with proper frontmatter:
   ```
   ---
   title: "Title"
   source: "URL or filepath or MANUAL"
   type: articles|papers|repos|notes|data
   ingested: YYYY-MM-DD
   tags: [auto-generated relevant tags]
   summary: "2-3 sentence summary"
   ---
   ```
4. Update `raw/{type}/_index.md`, `raw/_index.md`, and master `_index.md` (best-effort — if skipped or interrupted, the next read will rebuild from file frontmatter. See `references/indexing.md` Derived Index Protocol.)
5. Move or copy the intake item to `inbox/.processed/` and preserve the link between the inbox item and raw artifact when cheap
6. Append to `log.md`: `## [YYYY-MM-DD] ingest | Title (raw/type/slug.md)`
7. Report: what was staged, what processed artifact was saved, and detected tags
8. Check uncompiled source count. If 5+, suggest `/wiki:compile`
