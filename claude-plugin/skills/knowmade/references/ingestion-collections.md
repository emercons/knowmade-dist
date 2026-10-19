# Collection Ingestion Protocol

Bounded upstream corpora for `wiki ingest-collection`, moved verbatim out of
`ingestion.md`. The shared inbox-first overview, slug generation and
post-ingestion index updates stay in [`ingestion.md`](ingestion.md).

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
| `git` | GitHub/GitLab/local repos containing specs, proposals, docs | `git clone --depth 1`, `git ls-tree`, raw file reads |
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

Deduplication key: `collection` + `upstream_id` + `revision`/`sha`. If the exact
same upstream item was already ingested, skip it. If the item changed upstream,
write a new raw source; never overwrite the old one.

### Git Collections

Use Git for repositories such as `bitcoin/bips`; do not scrape GitHub HTML.

1. Clone shallowly or use the local repo path.
2. Record HEAD commit SHA and each blob SHA.
3. Include text-like files (`.md`, `.mediawiki`, `.wiki`, `.rst`, `.txt`,
   `.adoc`).
4. Exclude `.git/`, `.github/`, generated assets, binaries, images, archives,
   vendored dependencies, scripts, and test vectors unless explicitly included.
5. For BIP-style repos, prioritize root `bip-####.mediawiki` and `bip-####.md`
   files. Parse proposal headers such as `BIP`, `Layer`, `Title`, `Authors`,
   `Status`, `Type`, `Requires`, `License`, and `Discussion`.

For BIPs, publication in the repo is provenance for the proposal text, not proof
of adoption or consensus. Compilation must preserve that distinction.

### MediaWiki Dumps

Use official dumps when available. They are stable, polite to the upstream site,
and carry revision metadata.

1. Download or read the dump file.
2. Decompress `.bz2` with `bunzip2 -c` or `.gz` with `gunzip -c`.
3. Parse streaming XML; do not load a large dump entirely into memory.
4. Default to namespace `0`. Skip redirects and titles with `:` unless the user
   explicitly includes them.
5. Store page id/title, latest revision id, timestamp, contributor when
   available, and raw wikitext.

### MediaWiki API

Use the API for targeted imports or when dumps are unavailable:

1. Discover `api.php` from the site URL.
2. List pages via `action=query&list=allpages&apnamespace=0&aplimit=max`.
3. Follow continuation tokens.
4. Fetch content in batches with `prop=revisions`, `rvslots=main`, and
   `rvprop=ids|timestamp|user|comment|content`.
5. Optionally fetch categories and links for graph-aware compilation.
6. Respect throttling; never fall back to uncontrolled HTML crawling.

### CSV/JSON Message Archives

Use this adapter for bounded exports where each row/object is a message,
document, post, email, or transcript item. Examples include Cypherpunks-style
mailing-list CSVs and JSON exports with message-like objects.

1. Read local files directly or download URL sources to a temporary file.
2. Support `.csv`, `.tsv`, `.json`, and `.jsonl` using Python stdlib parsers.
   Do not split arbitrary nested JSON unless the user identifies the message
   array path.
3. Infer message fields conservatively:
   - id: `id`, `message_id`, `Message-ID`, `url`, or stable row number.
   - date: `date`, `created_at`, `timestamp`, `sent`, or `time`.
   - author: `author`, `from`, `sender`, `name`, or `handle`.
   - subject/title: `subject`, `title`, or the first non-empty text fragment.
   - body: `body`, `text`, `content`, `message`, `plain`, or `markdown`.
4. On ambiguous schemas, run `--dry-run` first and report detected columns,
   candidate field mapping, row count, and a sample. Ask before writing if the
   body field cannot be identified with high confidence.
5. Write the manifest to `raw/repos/` and each message to `raw/notes/` unless
   the dataset is explicitly a set of articles or formal documents.
6. Preserve row/object provenance in frontmatter: `row_number`, `message_id`,
   `author`, `date`, `subject`, `dataset_sha`, and `source_columns` when known.
7. Deduplicate by stable message id when present; otherwise use
   `dataset_sha + row_number + content hash`.

Message bodies should be markdown documents with a small provenance header
followed by the original message text. Preserve quoting, code blocks, URLs, and
mailing-list headers that may matter for later source criticism.

### Wayback CDX Snapshots

Use this adapter for bounded archived web captures. The CDX API is the
inventory; never recursively crawl live or archived HTML beyond the CDX result
set.

1. Accept either a CDX API URL or an original URL/prefix. For original URLs,
   query `https://web.archive.org/cdx` with JSON output, `fl=timestamp,original,statuscode,mimetype,digest,length`, and a conservative `filter=statuscode:200`.
2. Use `collapse=digest` by default to avoid duplicate captures with identical
   content. Respect `--from`, `--to`, `--include`, `--exclude`, and `--limit`
   if provided.
3. Fetch each selected capture with the `id_` replay form so the archived HTML
   is returned with minimal Wayback UI rewriting:
   `https://web.archive.org/web/<timestamp>id_/<original-url>`.
4. Convert HTML to markdown with a readability pipeline. Prefer a temporary
   Python virtual environment using `readability-lxml` plus `markdownify` or
   `html2text`; if dependency installation is unavailable, use WebFetch with an
   extraction prompt and record the fallback in `extraction_tool`.
5. Write the manifest to `raw/repos/` and each readable snapshot to
   `raw/articles/`. Preserve snapshot provenance in frontmatter:
   `wayback_timestamp`, `wayback_original`, `wayback_digest`, `statuscode`,
   `mimetype`, `length`, `canonical_url`, and `extraction_tool`.
6. Skip captures whose body is empty, binary, or mostly navigation after
   readability extraction. Count and report skips by reason.

For volatile pages, later compilation should treat Wayback captures as evidence
of what an archived page said at a specific timestamp, not as evidence that the
claim remains true.

### Collection Compilation

After collection ingestion, compile selectively:

- Prefer synthesized clusters over one compiled article per page.
- Use reference articles for indexes/timelines/glossaries.
- For BIPs, likely clusters are activation mechanisms, wallet standards, script
  upgrades, peer services, Taproot/Schnorr, mining/RPC, and the BIP process.
- For community wikis, default confidence to `medium` unless corroborated by
  authoritative specs, code, papers, or multiple independent sources.
