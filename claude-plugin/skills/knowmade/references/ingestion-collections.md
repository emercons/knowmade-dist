# Collection Ingestion Protocol

Bounded upstream corpora for `wiki ingest-collection`, moved verbatim out of
`ingestion.md`. The shared inbox-first overview, slug generation and
post-ingestion index updates stay in [`ingestion.md`](ingestion.md); the
per-adapter procedures (Git, MediaWiki dump and API, CSV/JSON messages, Wayback
CDX) are in [`collection-adapters.md`](collection-adapters.md).

## Collection Ingestion

Collection ingestion is for bounded upstream corpora: Git document repositories,
BIP-style proposal sets, MediaWiki XML dumps/API sites, message archives, and
Wayback CDX snapshot sets. Treat these as **source collections**, not as
compiled wiki content. The ingest step preserves raw sources and provenance;
the compile step later synthesizes useful concept/topic/reference articles.
Large collection inputs should still have an inbox trace: keep the downloaded
dump, manifest seed, or local source path in `inbox/`/`inbox/.processed/` when
practical, while the extracted child artifacts live in `raw/`.

Use `wiki ingest-collection` when the user asks to import, mirror, bulk ingest,
ingest another wiki/repository, split a dataset into per-message sources, or
capture archived snapshots. Do not recursively crawl HTML. Use structured
upstream interfaces:

| Adapter | Use for | Primary access path |
|---------|---------|---------------------|
| `git` | GitHub/GitLab/local repos containing specs, proposals, docs | `git clone`, `git rev-parse`, `git ls-tree`, raw file reads or a manifest-only pin |
| `mediawiki-dump` | Full MediaWiki imports or large snapshots | Official `.xml`, `.xml.bz2`, `.xml.gz` dumps |
| `mediawiki-api` | Targeted MediaWiki imports or dumpless sites | `api.php` with `allpages` + `revisions` |
| `csv-messages` | CSV/TSV/JSON/JSONL message archives such as mailing-list exports | Python stdlib `csv`/`json`, one child source per message row/object |
| `wayback-cdx` | Internet Archive snapshots for known URLs or URL prefixes | CDX API inventory, snapshot fetch, readability-to-markdown extraction |

### Collection Manifest

Every collection import writes a manifest source to `raw/repos/`:

```yaml
---
title: "Collection: <name>"
source: "<upstream URL or path>"
type: repos
ingested: YYYY-MM-DD
tags: [collection, collection-manifest, <adapter>]
summary: "Manifest for a collection ingest of <name>: N child sources captured from <revision>."
collection: "<collection-slug>"
adapter: git|mediawiki-dump|mediawiki-api|csv-messages|wayback-cdx
revision: "<commit sha, dump filename/date, API snapshot timestamp, dataset hash, or CDX query timestamp>"
canonical_url: "<canonical upstream URL>"
license: "<detected license or unknown>"
---
```

The manifest is operational provenance. Lint should not treat
`collection-manifest` sources as coverage failures just because no compiled
article cites them directly.

### Child Sources

Each upstream page/proposal/spec becomes its own immutable raw source, usually
under `raw/articles/`:

```yaml
---
title: "<upstream title>"
source: "<canonical upstream URL or file path>"
type: articles
ingested: YYYY-MM-DD
tags: [collection, <collection-slug>, ...]
summary: "2-3 sentence factual summary."
collection: "<collection-slug>"
adapter: git|mediawiki-dump|mediawiki-api|csv-messages|wayback-cdx
upstream_id: "<path, page id, message id, capture timestamp, or title>"
upstream_type: git-file|mediawiki-page|message-row|wayback-snapshot
revision: "<revision id, timestamp, or commit sha>"
sha: "<blob sha or content hash when available>"
canonical_url: "<per-item URL>"
content_format: markdown|mediawiki|wikitext|text|csv|tsv|json|jsonl|html
license: "<detected license or unknown>"
authors: [optional names]
categories: [optional upstream categories]
outlinks: [optional upstream links]
fetched: YYYY-MM-DD
---
```

Keep the full upstream text in the body, preserving tables, code blocks,
proposal metadata and references as much as possible; do not summarize away
normative requirements in specs. Name each file by `ingestion.md` § Slug
Generation, with words taken from the upstream title or path.

Deduplication key: `collection` + `upstream_id` + `revision`/`sha`. If the exact
same upstream item was already ingested, skip it. If the item changed upstream,
write a new raw source; never overwrite the old one.

### Collection Compilation

After collection ingestion, compile selectively:

- Prefer synthesized clusters over one compiled article per page.
- Use reference articles for indexes/timelines/glossaries.
- For BIPs, likely clusters are activation mechanisms, wallet standards, script
  upgrades, peer services, Taproot/Schnorr, mining/RPC, and the BIP process.
- For community wikis, default confidence to `medium` unless corroborated by
  authoritative specs, code, papers, or multiple independent sources.
- For ordinary collections, use the manifest to understand scope but cite the
  child raw files. For a Git `--manifest-only` manifest, cite the manifest
  itself in `sources:` and use its pinned `<collection>@<commit>:<path>` rows
  for claim-level provenance.
- Thesis files are for claims under test. A BIP being published means it met
  the repository's process criteria; it is provenance for the proposal text and
  does not by itself prove adoption, consensus or desirability. Preserve that
  distinction.
