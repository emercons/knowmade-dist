---
description: "Bulk-ingest source collections such as Git doc repos, MediaWiki sources, CSV/JSON message archives, and Wayback CDX snapshots through Knowmade's inbox-first flow into processed raw artifacts."
argument-hint: "<repo-url|repo-path|mediawiki-url|dump.xml[.bz2|.gz]|csv-json-path|cdx-url|archived-url> [--adapter auto|git|mediawiki-dump|mediawiki-api|csv-messages|wayback-cdx] [--manifest-only] [--revision <ref>] [--wiki <name>] [--local] [--new-topic <name>] [--limit <N>] [--namespace <id>] [--include <pattern>] [--exclude <pattern>] [--from YYYYMMDD] [--to YYYYMMDD] [--dry-run] [--compile] [--include-archived]"
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(ls:*), Bash(wc:*), Bash(date:*), Bash(mkdir:*), Bash(mv:*), Bash(cp:*), Bash(rm:*), Bash(basename:*), Bash(find:*), Bash(git:*), Bash(curl:*), Bash(python3:*), Bash(bunzip2:*), Bash(gunzip:*), Bash(scripts/llm-wiki:*), Bash(${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki:*)
---

## Your task

Bulk-ingest a collection into the wiki as immutable processed raw artifacts. A collection is a bounded upstream corpus, not a single article: a Git repository full of specs, a BIP repository, a MediaWiki XML dump/API site, a CSV/JSON message archive, or a Wayback CDX snapshot set. Do **not** compile one wiki article per upstream page by default. First stage the intake in `inbox/` (an intake note in `inbox/.processed/` when the corpus is only a pointer that cannot be copied); the inbox trace is required before anything is written to `raw/`. Then preserve processed raw artifacts with provenance, then compile synthesized topic/concept/reference articles.

<!-- hub-prelude:start -->
**Resolve the wiki.** A few file reads, not a search: never `find`, Explore, or scan the filesystem. Full protocol and edge cases: `skills/knowmade/references/hub-resolution.md`.
1. HUB: `$LLM_WIKI_HUB` or read `$HOME/.config/llm-wiki/config.json` and use `hub_path` (expand only a leading `~`); `resolved_path` is a fallback cache; with no config use `$HOME/wiki`. If the hub is stat-able but reads fail with `Operation not permitted`, stop and report the launcher permission denial.
2. Wiki, first match: `--local` → `.wiki/`; `--wiki <name>` → `HUB/wikis.json` entry (`wikis.local.json` path first; stale path → `HUB/topics/<name>`); `.wiki/` in CWD; else HUB. Reject archived entries (`status: archived`, `topics/.archive/`) unless the command takes `--include-archived`.
3. `<slug>` and `<slug>-PRIVATE` are sibling topics in one hub: read private freely, write under the Write-Guard in `skills/knowmade/references/private-boundary.md`.
<!-- hub-prelude:end -->
4. If `<wiki>/_index.md` is missing and `--new-topic <name>` is set, create the topic wiki first (branch below). If no wiki exists and no `--new-topic`, stop and ask for a target wiki.

Read the collection protocol at `skills/knowmade/references/ingestion-collections.md` (manifest and child-source frontmatter, deduplication key, collection compilation), then follow the shared flow below. The per-adapter procedure and the fit check are read only in the branch that needs them. Pass the resolved target (`--wiki <slug>`, `--local` or the wiki path) to every `llm-wiki` call; use the bundled `"${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki"` (`scripts/llm-wiki` in a source checkout) for the deterministic steps named below.

## Parse arguments

- **Source**: repo URL/path, MediaWiki site URL, dump file/URL, CSV/TSV/JSON/JSONL path/URL, CDX API URL, or original URL to query through the Wayback CDX API.
- **--adapter**: `auto` default. Supported: `git`, `mediawiki-dump`, `mediawiki-api`, `csv-messages`, `wayback-cdx`.
- **--limit <N>**: maximum child sources to ingest. Useful for API imports and dry runs.
- **--namespace <id>**: MediaWiki namespace. Default `0`.
- **--include <pattern> / --exclude <pattern>**: filter upstream paths, page titles, message fields, or original snapshot URLs. Treat as shell globs for Git paths and regex for MediaWiki titles, message text, and Wayback original URLs unless the user specifies otherwise.
- **--manifest-only**: Git only. Pin the selected files as path/blob-SHA rows in one immutable manifest and write no child raw copies.
- **--revision <ref>**: Git only. Resolve this ref to a full commit SHA instead of assuming `HEAD`.
- **--from YYYYMMDD / --to YYYYMMDD**: bound Wayback CDX capture timestamps.
- **--dry-run**: list what would be ingested, write nothing.
- **--compile**: after raw ingestion, run the normal compile workflow with collection-aware clustering.
- **--new-topic <name>**: create the topic wiki first (branch below).
- **--include-archived**: explicitly allow ingestion into an archived target wiki (branch below).

## Adapter detection

Use `--adapter` if supplied. Otherwise:

1. Source ending in `.xml`, `.xml.bz2`, or `.xml.gz` → `mediawiki-dump`.
2. Source contains `github.com/`, `gitlab.com/`, ends in `.git`, or is a local directory with `.git/` → `git`.
3. Source ending in `.csv`, `.tsv`, `.json`, or `.jsonl` with message-like fields, or user asks for per-message markdown → `csv-messages`.
4. Source contains `web.archive.org/cdx`, user says "Wayback"/"CDX", or source is an original URL plus snapshot/archive intent → `wayback-cdx`.
5. Source URL contains `/wiki/`, `/w/`, or has a reachable `api.php` endpoint → `mediawiki-api`.
6. If still ambiguous, ask the user to choose `git`, `mediawiki-dump`, `mediawiki-api`, `csv-messages`, or `wayback-cdx`.

Never recursively crawl HTML pages as a collection import. Use structured upstream APIs, repository files, official dumps, dataset rows, or CDX inventories.

## Shared collection flow

1. Derive a stable collection slug from the upstream name, e.g. `bitcoin-bips`, `bitcoin-wiki`.
2. Stage the collection pointer, downloaded dump, or manifest seed in `inbox/` or preserve a clear intake note in `inbox/.processed/`.
3. Fetch an inventory of candidate items with stable upstream IDs and revision markers (the adapter branch below).
4. Apply filters and `--limit`.
5. If more than 500 child sources would be written and the user did not explicitly provide `--limit`, show the count and ask for confirmation before writing; for hundreds of children also run the fit check (branch below).
6. For each child item, skip it if a raw source already has the same `collection`, `upstream_id`, and `revision`/`sha`. If the upstream item changed, write a new immutable raw source; do not overwrite the old one.
7. Write one processed manifest artifact to `raw/repos/` with `tags: [collection, collection-manifest, <adapter>]`.
8. Write processed child artifacts to `raw/articles/` unless the adapter-specific rule says otherwise. `csv-messages` usually writes children to `raw/notes/`. Git `--manifest-only` writes no children.
9. After the batch, run `llm-wiki index rebuild` (it rebuilds every raw index from frontmatter; never hand-edit hundreds of table rows) and `llm-wiki log append --kind ingest-collection "<collection> via <adapter>: N new, M skipped, K total candidates"`. If `index rebuild` exits 1 or 2, report its findings and stop before the log line. Then do items 4 and 5 of `skills/knowmade/references/ingestion.md` § Post-Ingestion Index Updates yourself (master `_index.md` source count and Recent Changes; move the intake item to `inbox/.processed/` with provenance); the CLI does neither.
10. Report the inbox intake trace, manifest path, counts, skipped duplicates, filters, and next compile suggestion.

### If the adapter is `git` (including `--manifest-only` and `--revision`)

Follow `skills/knowmade/references/collection-adapters.md` § Git (clone, candidate files, per-file `upstream_id`/`revision`/`sha`, `--revision`, `--manifest-only` manifest body).

### If the adapter is `mediawiki-dump`

Follow `skills/knowmade/references/collection-adapters.md` § MediaWiki dump.

### If the adapter is `mediawiki-api`

Follow `skills/knowmade/references/collection-adapters.md` § MediaWiki API.

### If the adapter is `csv-messages`

Follow `skills/knowmade/references/collection-adapters.md` § CSV/JSON messages.

### If the adapter is `wayback-cdx`

Follow `skills/knowmade/references/collection-adapters.md` § Wayback CDX.

### If `--compile` is set

After raw ingestion, survey with `llm-wiki compile plan` and run the normal compile workflow (`/knowmade:compile`) with the collection rules in `skills/knowmade/references/ingestion-collections.md` § Collection Compilation (clusters, not one article per page; manifest citation; confidence).

### If `--new-topic <name>`

Derive a slug from the name (lowercase, hyphens, no special characters, at most 40 characters) and run `llm-wiki init --topic <slug> --description "<name>"` (pass `--hub <path>` when no hub is configured; ask where to create it rather than assuming `~/wiki`). If the CLI cannot run, follow `skills/knowmade/references/wiki-setup.md` § Init manual fallback. Collision checks treat archived topics as unavailable unless the user restores or includes them. Target the new wiki for this ingestion.

### If the target wiki is archived or `--include-archived` is given

Collection ingest skips archived topic wikis by default. If `--wiki <name>` resolves to `status: archived` or a path under `topics/.archive/`, stop and ask the user to restore it with `/knowmade:archive restore <name>` or rerun with `--include-archived`. When explicitly included, write only inside that archived topic path and keep it archived. Auto-classification does not use archived topics.

### If the collection may be an inventory candidate, a dataset, or has hundreds of children

Run the fit check in `skills/knowmade/references/collection-setup.md` § Fit check before writing, and link the manifest from a matching inventory record afterwards.

### If the CLI cannot run

Rebuild indexes, the master statistics and the log entry by hand as `skills/knowmade/references/collection-setup.md` § CLI unavailable says.

## Report format

End with:

- Adapter and collection slug.
- Manifest path.
- New/skipped/error counts.
- Filters and namespace used.
- Top 10 child sources by title/path.
- Whether compile was run or the exact suggested next command.
