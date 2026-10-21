---
description: "Ingest source material into an active wiki using Knowmade's inbox-first flow. Accepts URLs, file paths, PDFs, freeform text, or processes the inbox. Supports X/Twitter status URLs through a fetch fallback chain."
argument-hint: "<url|filepath|\"text\"> [--type articles|papers|repos|notes|data] [--title \"Title\"] [--inbox] [--keep] [--wiki <name>] [--local] [--auto-classify] [--new-topic <name>] [--project <slug>] [--include-archived]"
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(ls:*), Bash(wc:*), Bash(date:*), Bash(mv:*), Bash(mkdir:*), Bash(basename:*), Bash(file:*), Bash(curl:*), Bash(mktemp:*), Bash(rm:*), Bash(pdftotext:*), Bash(python3:*), Bash(scripts/llm-wiki:*), Bash(${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki:*), WebFetch, WebSearch
---

## Your task

<!-- hub-prelude:start -->
**Resolve the wiki.** A few file reads, not a search: never `find`, Explore, or scan the filesystem. Full protocol and edge cases: `skills/knowmade/references/hub-resolution.md`.
1. HUB: `$LLM_WIKI_HUB` or read `$HOME/.config/llm-wiki/config.json` and use `hub_path` (expand only a leading `~`); `resolved_path` is a fallback cache; with no config use `$HOME/wiki`. If the hub is stat-able but reads fail with `Operation not permitted`, stop and report the launcher permission denial.
2. Wiki, first match: `--local` → `.wiki/`; `--wiki <name>` → `HUB/wikis.json` entry (`wikis.local.json` path first; stale path → `HUB/topics/<name>`); `.wiki/` in CWD; else HUB. Reject archived entries (`status: archived`, `topics/.archive/`) unless the command takes `--include-archived`.
3. `<slug>` and `<slug>-PRIVATE` are sibling topics in one hub: read private freely, write under the Write-Guard in `skills/knowmade/references/knowmade-profile.md` § Private Data Boundary.
<!-- hub-prelude:end -->
4. Read `<wiki>/_index.md` to verify. If missing and `--new-topic <name>` is set → create the topic wiki (branch below). If missing and no `--new-topic` → stop with "No wiki found. Use `--new-topic <name>` to create one, or run `/knowmade:wiki init` first."

Read the ingestion protocol at `skills/knowmade/references/ingestion.md` (source types, URL, file, PDF and text ingestion, inbox processing, slugs, index updates), and from the Knowmade profile only § Private Data Boundary: Read Freely, Write-Guard and § Inbox-First Source Lifecycle (including its exception). Then ingest source material. New source material must be staged in or discovered from `inbox/`; `raw/` receives the processed extraction artifact after the agent has parsed the source.

### Writing `raw/` (CLI)

Write each processed raw artifact with `llm-wiki source note --type <type>
--title "..." --source <url|path|MANUAL> --summary "..." --tags a,b` (optional flags:
`--slug`, `--project`, `--extra`, `--help`), the body on stdin or `--body-file`, plus
the wiki scope flags. Locate the CLI as `"${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki"` or, in a source checkout,
`scripts/llm-wiki`. It derives the slug (`-2`, `-3` on a collision), writes
the frontmatter lint requires, refuses private material in a public wiki (exit
2, nothing written), then rebuilds the indexes and appends the `ingest` log
entry. The master `_index.md` statistics and the move to `inbox/.processed/`
stay with you.

### Parse $ARGUMENTS

- **--new-topic <name>**: Create a new topic wiki and ingest the source into it. Works from any directory (branch below).
- **--inbox**: Process all files in the wiki's `inbox/` directory
- **--keep**: When processing inbox, keep originals (move to .processed/ instead of deleting)
- **--type**: Force source type (articles, papers, repos, notes, data). Default: auto-detect.
- **--title**: Override extracted title
- **--auto-classify**: For single items, classify into the best-matching topic wiki instead of requiring `--wiki`. Always active for `--inbox` when no `--wiki` is set.
- **--project <slug>**: Tag the processed raw artifact with `project: <slug>` (branch below).
- **--include-archived**: Explicitly allow ingestion into an archived target wiki (branch below). Not used for auto-classification.
- **Source**: Everything else — URL (starts with http), file path (contains / or .), or quoted freeform text

### If --inbox

Follow `skills/knowmade/references/ingestion.md` § Inbox Processing: scan `inbox/` (excluding `.processed/` and hidden files), process each file by its type, move it to `inbox/.processed/` (copy with `--keep`), update all indexes and report a summary. If 5+ items were processed, suggest compilation.

### If URL source

Stage the URL in `inbox/` as a `.url` or small markdown capture before extraction (a visible intake trace even when the user did not drop a file), then follow `skills/knowmade/references/ingestion.md` § URL Ingestion: X/Twitter status URLs (`x.com/*/status/*`, `twitter.com/*/status/*`) through the fallback chain, GitHub URLs, PDF URLs (`.pdf` or PDF content type), and all other URLs through WebFetch, each with its default type.

### If file path source

If the file is not already inside `inbox/`, copy or move it there before extraction; never place user-supplied files directly in `raw/`. Then follow `skills/knowmade/references/ingestion.md` § File Ingestion and § PDF Ingestion (type auto-detected from extension and content; structured data is described by schema plus sample, or handed off to `/knowmade:ingest-collection --adapter csv-messages` for per-message markdown).

### If quoted text source

Stage the text as a markdown capture in `inbox/`, then follow `skills/knowmade/references/ingestion.md` § Freeform Text Ingestion.

### If no --wiki or --local was given and the wiki resolved to the hub

Route the content to a topic wiki after fetching it (title, summary and tags are then known); skip this entirely when `--wiki` or `--local` was given. The single-item choice and the `--inbox` batch classification table are in `skills/knowmade/references/ingest-setup.md` § Topic wiki routing.

### If the request is a bounded corpus, a track/queue/decide-later request, or a large dataset

Stop and hand off to `/knowmade:ingest-collection`, `/knowmade:inventory` or `/knowmade:dataset` as the signals in `skills/knowmade/references/ingest-setup.md` § Handoffs say; never reduce a collection to its README or squeeze large data into `raw/data/`.

### If --include-archived is given or the target wiki is archived

Stop and ask the user to restore it with `/knowmade:archive restore <name>` or rerun with `--include-archived`; with the flag, write only inside that archived topic path and keep it archived (`skills/knowmade/references/ingest-setup.md` § Archive awareness).

### If --new-topic <name>

Derive a slug from the name (lowercase, hyphens, no special characters, at most 40 characters; "machine learning" → `machine-learning`) and run `"${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki" init --topic <slug> --description "<name>"` (`scripts/llm-wiki` in a source checkout) (it registers the portable path `topics/<slug>`; pass `--hub <path>` when no hub is configured, and ask the user where to create it rather than assuming `~/wiki`). If the CLI cannot run, follow `skills/knowmade/references/wiki-setup.md` § Init manual fallback. Target the new wiki for this ingestion.

### If --project <slug>

Verify the project exists and fail early otherwise; the tagging rule, the failure message and the no-ambient-focus note are in `skills/knowmade/references/ingest-setup.md` § Project scope.

### If the original bytes are to be kept (PDFs, binaries, or the wiki declares a source_store)

Call `llm-wiki source receive`, `extract`, `decide --retain local|git`, then `verify` (with the wiki scope flags) before writing `raw/`, as `skills/knowmade/references/source-lifecycle.md` says. If the CLI fails, report its message and stop; do not retain originals by hand.

### If the llm-wiki CLI cannot run

Write the raw file by hand per `skills/knowmade/references/wiki-format.md` (raw source, log and index formats; slug per the ingestion protocol), update `raw/{type}/_index.md`, `raw/_index.md` and the master `_index.md` best-effort per `skills/knowmade/references/indexing.md` § Derived Index Protocol (the next read rebuilds a skipped index), and append `## [YYYY-MM-DD] ingest | Title (raw/type/slug.md)` to `log.md`.

### For all sources

1. Ensure there is an intake item under `inbox/` unless this command is already
   processing `--inbox`
2. Write the processed extraction artifact to `raw/{type}/` with `source note` (slug `YYYY-MM-DD-descriptive-slug.md`, indexes rebuilt and `ingest` log entry appended by the CLI)
3. Update the master `_index.md` by hand (statistics, Recent Changes; `skills/knowmade/references/indexing.md` § Master Index Statistics)
4. Move or copy the intake item to `inbox/.processed/` and preserve the link between the inbox item and raw artifact when cheap
5. Report: what was staged, what processed artifact was saved, and detected tags
6. Check active inventory records by title/source when cheap. If a matching record exists, report the suggested linkage and status change (`sources:` add raw path, maybe status `ingested`) instead of silently leaving the tracking state stale
7. Check uncompiled source count. If 5+, suggest `/knowmade:compile`
