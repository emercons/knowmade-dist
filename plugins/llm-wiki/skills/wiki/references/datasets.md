# Dataset Registry Reference

The dataset registry lets a wiki act as an interface and index for data that is
too large, mutable, sensitive, or operationally awkward to store directly under
`raw/`. The registry stores manifests, schema notes, small samples, profiles,
query recipes, and provenance. The actual dataset stays external.

Use this layer for archives, database dumps, message corpora, blockchain data,
parquet/duckdb/sqlite stores, object-store prefixes, API-backed datasets, or any
source where "ingest the whole thing into markdown" would make the wiki less
usable.

## Boundary

- `raw/data/`: immutable source notes or small single-source data files that can
  reasonably live in the wiki.
- `datasets/`: metadata and interface layer for data that should remain outside
  the wiki.
- `inventory/`: tracking state for whether a dataset matters and what to do
  next. Inventory records may point to dataset manifests.
- `output/`: generated deliverables. Legacy output artifacts that describe
  datasets may be migrated additively into dataset manifests.

Do not copy large datasets into `datasets/`. Store paths, URLs, checksums,
profiles, samples, and query recipes instead.

Be opinionated about the boundary:

- If the data is small, stable, and useful as markdown, ingest it into
  `raw/data/` instead of creating a dataset manifest.
- If the data is large, mutable, remote, sensitive, binary, compressed, or
  better queried in its native format, use a dataset manifest.
- If the user mostly needs next actions or acceptance state, create or link an
  inventory record: the manifest answers "where/how is the data accessed",
  inventory "why do we care and what happens next."
- If a dataset would become hundreds of inventory records, create one manifest
  plus one corpus inventory record and show that shape before a larger pivot.

## Chat Views

Inspect large data without loading it; `dataset list` is fast and index-driven:

- Read `datasets/_index.md` first; for filters or columns it lacks, read only
  `datasets/*/MANIFEST.md` frontmatter.
- Never open samples, profiles, query notes, or the dataset for a plain list.
- Default output is a compact Markdown table (bullets when long paths or URLs
  would dominate). Cap long lists with `--limit` or a default, then report the
  omitted count and the registry path.

Recommended chat views:

| View | Columns | Use |
|------|---------|-----|
| `summary` | counts by status/storage/schema status, newest manifests | quick status checks |
| `manifests` | dataset, status, storage, formats, size, records, updated | complete compact registry |
| `schema` | dataset, schema status, formats, record count, latest profile | deciding what to profile next |
| `locations` | dataset, storage, access, compact location pointer | finding where the data lives |

For a dataset linked from an inventory record, show its next action only if the
record frontmatter gives it cheaply.

## Directory Layout

The registry is created lazily. A wiki with no `datasets/` directory has no
manifests; read-only commands report that without creating files.

```text
datasets/
├── _index.md
└── <dataset-slug>/
    ├── _index.md
    ├── MANIFEST.md
    ├── samples/          # Lazy: created by dataset sample
    │   ├── _index.md
    │   └── *.md
    ├── profiles/         # Lazy: created by dataset profile
    │   ├── _index.md
    │   └── *.md
    └── queries/          # Lazy: created when query recipes are written
        ├── _index.md
        └── *.md
```

Per-dataset manifest folders are created only when a manifest is added. The
`samples/`, `profiles/`, and `queries/` subfolders are created only when their
first note is written.

## Manifest Format

`datasets/<slug>/MANIFEST.md` is the source of truth:

```markdown
---
title: "Bitcointalk Temporal Graph"
dataset_id: bitcointalk-temporal-graph
status: proposed
storage: external
locations:
  - https://figshare.com/articles/dataset/BitcoinTemporalGraph/26305093
formats: [csv, zip]
size_bytes: null
record_count: null
schema_status: unknown
created: YYYY-MM-DD
updated: YYYY-MM-DD
tags: [bitcoin, bitcointalk, graph-dataset]
summary: "External Bitcointalk graph dataset indexed by the bitcoin wiki."
origin: output/bitcointalk-data-2026-05-03.md
inventory:
  - inventory/corpora/bitcointalk-archive.md
raw_sources:
  - raw/articles/2026-05-03-bitcointalk-data.md
license: unknown
access: public
---

# Bitcointalk Temporal Graph

## Scope

What the dataset covers and what it does not cover.

## Storage Locations

Where the data lives, how stable those locations are, and any access constraints.

## Schema

Known tables, columns, keys, entity relationships, and uncertainty.

## Samples And Profiles

Links to small samples and profile notes in this folder.

## Query Recipes

Links to reproducible ways to inspect the dataset without loading it into the
wiki.

## Caveats

Known gaps, bias, volatility, privacy limits, or operational risks.
```

Required frontmatter fields:

- `title`
- `dataset_id`
- `status`
- `storage`
- `locations`
- `formats`
- `schema_status`
- `created`
- `updated`
- `tags`
- `summary`

Recommended fields:

- `size_bytes` (integer)
- `record_count`
- `origin`
- `inventory`
- `raw_sources`
- `license`
- `access`
- `checksum` (`sha256:<64 hex digits>`)
- `owner`
- `refresh_cadence`

Statuses:

- `proposed`: identified, not yet an accepted interface
- `active`: accepted and currently useful
- `external`: deliberately external, no local copy
- `archived`: kept for history, not maintained
- `unavailable`: location inaccessible or permissions unresolved

Storage modes:

- `local`: local path outside the wiki
- `remote`: remote URL or object-store location
- `external`: third-party page, API, or repository
- `hybrid`: several modes

Locations: a `scheme://` entry is remote, never checked. Any other is a local
path: relative to the wiki root (not `raw/`), absolute as written, `~` not
expanded. `size_bytes` and `checksum` (`sha256:<64 hex>`) apply to each local
file; a directory is checked for existence only. `dataset verify` and
`lint --profile datasets` (C27) report `dataset-missing` (warning),
`dataset-size`, `dataset-checksum` (critical); `unavailable` and `archived`
manifests are skipped.

Schema statuses:

- `unknown`: no schema is known yet
- `inferred`: inferred from sample/profile
- `declared`: upstream provides it
- `validated`: checked against current data

## Index Format

`datasets/_index.md` summarizes manifests:

```markdown
# Dataset Registry Index

> Dataset manifests for large or external data.

Last updated: YYYY-MM-DD

## Statistics

- Datasets: N
- Active: N
- External: N
- Unavailable: N

## Contents

| Dataset | Status | Storage | Formats | Size | Records | Updated |
|---------|--------|---------|---------|------|---------|---------|
| [Bitcointalk Temporal Graph](bitcointalk-temporal-graph/MANIFEST.md) | proposed | external | csv, zip | unknown | unknown | YYYY-MM-DD |
```

Each `datasets/<slug>/_index.md` links to `MANIFEST.md` and any existing
sample/profile/query indexes, which list their notes in the standard table shape.

## Profiles

Profiles are small markdown notes under `datasets/<slug>/profiles/` that capture
observations such as size, format, row counts, headers, table names, partition
layout, or schema certainty. Include the date profiled, the exact location
checked, the commands or query snippets used, bounded observations only, and
privacy/security caveats. Never run an expensive full scan unless the user
explicitly asks and the path is safe to access.

## Samples

Samples are tiny excerpts or sampling recipes under `datasets/<slug>/samples/`,
at most 20 rows by default. For compressed, remote, private, or very large data,
write a recipe instead of fetching. Never store secrets, personal data,
credentials, or large excerpts.

## Query Recipes

Recipes under `datasets/<slug>/queries/` document reproducible access (DuckDB
SQL, sqlite commands, parquet scans, API calls, Python snippets). Prefer
read-only queries; give expected runtime/cost when known.

## Migration Paths

Dataset migration is explicit and additive.

### Discovery

`dataset scan-outputs` looks for output files that are really dataset
descriptions: filenames or titles containing `dataset`, `data`, `corpus`,
`archive`, `dump`, `warehouse`, `lake`, `parquet`, `sqlite`, `duckdb`, `csv`,
`jsonl` or `snapshot`, or body sections with size, rows, schema, license,
storage path, sample or query recipes. It reports suggested `dataset
migrate-output ... --dry-run` commands and must not write manifests.

### Output Migration

`dataset migrate-output <path>` defaults to dry-run. `--apply` creates
`datasets/<slug>/MANIFEST.md` files, leaves the source output in place, and
never copies the dataset into the wiki.

### Inventory Linkage

If an inventory record tracks the same corpus, link both ways: manifest
`inventory: [inventory/corpora/<slug>.md]`; inventory `sources:` or body link to
`datasets/<slug>/MANIFEST.md`.

## Lint Behavior

Lint should treat missing `datasets/` as a migration opportunity, not
corruption:

- Missing `datasets/` on an existing wiki: suggestion, not critical.
- `lint --fix`: may repair indexes for a dataset registry that already exists,
  but should not create a completely absent `datasets/` tree just to populate
  empty placeholders. Missing per-dataset `samples/`, `profiles/`, or
  `queries/` folders are fine until used.
- Output artifacts that look like dataset manifests: suggestion with migration
  commands.
- Lint must never auto-convert outputs, raw files, or inventory records into
  dataset manifests.
