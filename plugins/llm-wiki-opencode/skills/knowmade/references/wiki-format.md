# Knowmade Wiki File Formats

Index, log, raw-source, naming and tag formats, moved verbatim out of
`wiki-structure.md`: what ingestion needs to write a source and update its indexes.
Directory layout and the `wikis.json` registry stay in [`wiki-structure.md`](wiki-structure.md);
config, topic guide, article, provenance-resolution, freshness, linking and output
formats are in [`wiki-articles.md`](wiki-articles.md). `HUB/` means the resolved hub path.

## _index.md Format

Every existing wiki-managed directory has an `_index.md`. This is the agent's
primary navigation aid. Optional directories do not need placeholder indexes
before they exist.

```markdown
# [Directory Name] Index

> [One-line description of what this directory contains]

Last updated: YYYY-MM-DD

## Contents

| File | Summary | Tags | Updated |
|------|---------|------|---------|
| [filename.md](filename.md) | One-sentence summary | tag1, tag2 | YYYY-MM-DD |

## Categories

- **category-name**: file1.md, file2.md

## Recent Changes

- YYYY-MM-DD: Description of change
```

### Master _index.md (root level)

Additionally includes:

```markdown
## Statistics

- Sources: N raw documents
- Articles: N compiled wiki articles
- Inventory records: N tracked items
- Datasets: N manifests
- Outputs: N generated artifacts
- Archived topics: N (hub index only)
- Last compiled: YYYY-MM-DD
- Last lint: YYYY-MM-DD

## Quick Navigation

- [All Sources](raw/_index.md)
- [Inventory](inventory/_index.md) — include only when `inventory/` exists
- [Datasets](datasets/_index.md) — include only when `datasets/` exists
- [Concepts](wiki/concepts/_index.md)
- [Topics](wiki/topics/_index.md)
- [References](wiki/references/_index.md)
- [Outputs](output/_index.md)
```

## log.md Format

Append-only chronological activity log. Every wiki operation appends an entry. Never edit or delete existing entries during normal workflows. The sole exception is an explicit user-directed privacy retraction with `scripts/llm-wiki retract --remove-from-logs`, which atomically removes complete matching entries while preserving unrelated history. **Otherwise always open for append, never read-modify-write** — this makes concurrent writes safe (lines from multiple sessions interleave without corruption). Format is grep-friendly:

```markdown
# Wiki Activity Log

## [2026-04-04] init | Wiki initialized
## [2026-04-04] ingest | Attention Is All You Need (raw/papers/2026-04-04-attention-is-all-you-need.md)
## [2026-04-04] ingest | Illustrated Transformer (raw/articles/2026-04-04-illustrated-transformer.md)
## [2026-04-04] ingest-collection | bitcoin-bips via git: 389 new, 0 skipped, 389 total candidates
## [2026-04-04] compile | 2 sources → 3 new articles, 1 updated (transformer-architecture, self-attention, sequence-modeling + updated attention-mechanisms)
## [2026-04-04] query | "How does self-attention work?" → answered from 2 articles
## [2026-04-05] lint | 12 checks, 0 critical, 2 warnings, 3 suggestions, 1 auto-fixed
## [2026-04-05] research | "transformer variants" → 5 sources ingested, 4 articles compiled
## [2026-04-05] output | summary on transformer-architecture → output/summary-transformer-architecture-2026-04-05.md
```

Each entry: `## [YYYY-MM-DD] operation | Description`

Write an entry with `llm-wiki log append --wiki <slug> --kind <operation> "<description>"` (`--local` for a project `.wiki/`, `--date` defaults to today). It appends one line, creates `log.md` with the header above when it is absent, and exits 2 writing nothing on a multi-line message or, for a public wiki, one carrying a secret, a machine-local path or a `-PRIVATE` path. Append by hand, in this format, only if the CLI is unavailable.

Operations: `init`, `dispatch`, `ingest`, `ingest-collection`, `compile`, `query`, `lint`, `research`, `output`, `refresh`, `librarian`, `audit`, `plan`, `idea`, `project`, `inventory`, `dataset`, `schema`, `archive`, `retract`, `ll`, `assess`

Useful for: `grep "^## \[" log.md | tail -10` to see recent activity.

## Source File Format (raw/)

```markdown
---
title: "Title"
source: "URL, filepath, MANUAL, or provenance label such as session"
type: articles|papers|repos|notes|data
ingested: YYYY-MM-DD
tags: [tag1, tag2]
summary: "2-3 sentence summary"
---

# Title

[Full content]
```

### Optional Collection Provenance

Raw files created by `wiki ingest-collection` may include additional
frontmatter. These keys are canonical and should not be linted as unknown:

```yaml
collection: "<stable collection slug>"
adapter: git|mediawiki-dump|mediawiki-api
upstream_id: "<repo path, page id, or page title>"
upstream_type: git-file|mediawiki-page
revision: "<commit sha, dump revision id, or timestamp>"
sha: "<blob sha or content hash>"
canonical_url: "<stable upstream URL>"
content_format: markdown|mediawiki|wikitext|text
license: "<detected license or unknown>"
authors: [optional names]
categories: [optional upstream categories]
outlinks: [optional upstream links]
fetched: YYYY-MM-DD
```

Collection manifests live in `raw/repos/` with `type: repos` and
`tags: [collection, collection-manifest, <adapter>]`. Child pages/specs usually
live in `raw/articles/` with `type: articles`. The raw layer is still immutable:
if an upstream page changes, ingest the new revision as a new raw source instead
of overwriting the old one.

## File Naming

- **Raw sources**: `YYYY-MM-DD-descriptive-slug.md` (date prefix for chronological order)
- **Wiki articles**: `descriptive-slug.md` (no date — living documents)
- **Inventory records**: `descriptive-slug.md` (no date — durable tracking state)
- **Dataset manifests**: `datasets/descriptive-slug/MANIFEST.md`
- **Output artifacts**: `{type}-{topic-slug}-{YYYY-MM-DD}.md`
- All filenames: lowercase, hyphens for spaces, no special characters, max 60 chars

## Tag Convention

Tags are lowercase, hyphenated. Prefer specific over general:
- Good: `transformer-architecture`, `self-attention`, `natural-language-processing`
- Bad: `ai`, `ml`, `tech`

Normalize across the wiki — no near-duplicates like `ml` vs `machine-learning`.
