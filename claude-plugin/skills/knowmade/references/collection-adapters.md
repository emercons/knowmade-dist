# Collection Adapters

The per-adapter protocols of `/knowmade:ingest-collection`, moved out of the
command and out of [`ingestion-collections.md`](ingestion-collections.md) so the
command reads only the adapter it runs. Read the section for the adapter in
hand, not the file top to bottom. The manifest and child-source frontmatter,
the deduplication key and collection compilation stay in
[`ingestion-collections.md`](ingestion-collections.md). `<adapter>` values:
`git`, `mediawiki-dump`, `mediawiki-api`, `csv-messages`, `wayback-cdx`.

## Git

Use Git for repositories such as `bitcoin/bips`; do not scrape GitHub HTML. Use a
shallow clone for ordinary remote ingestion unless the user provides a local path:

```bash
git clone --depth 1 <url> <tmpdir>
git -C <tmpdir> rev-parse HEAD
git -C <tmpdir> ls-tree -r --format='%(objectname) %(path)' HEAD
```

Candidate files:
- Include text-like source files: `.md`, `.mediawiki`, `.wiki`, `.rst`, `.txt`, `.adoc`.
- Exclude `.git/`, `.github/`, generated assets, binaries, images, archives, vendored dependencies, scripts, and test vectors by default (unless explicitly included).
- For BIP-style repositories, prioritize root-level `bip-####.mediawiki` and `bip-####.md` files; treat sibling images/test vectors as referenced assets, not primary raw sources unless the user includes them. Parse proposal headers when present, especially `BIP`, `Layer`, `Title`, `Authors`, `Status`, `Type`, `Requires`, `License`, and `Discussion`.

For each file:
- `upstream_id`: repository-relative path.
- `revision`: the commit SHA (full SHA of `HEAD`, or of `--revision`).
- `sha`: Git blob SHA.
- `canonical_url`: GitHub/GitLab blob URL pinned to the commit when possible.

For `bitcoin/bips`, classify the collection manifest as `raw/repos/` and each BIP proposal as `raw/articles/`.

### `--revision <ref>`

Resolve `<ref>^{commit}` and use that full SHA for all tree/blob operations and
canonical URLs; the default is `HEAD`.

### `--manifest-only`

Keeps Git-owned file bodies in Git instead of duplicating them under
`raw/articles/`. It accepts `--revision <ref>`, applies the same path filters,
and writes one immutable `raw/repos/` manifest and **no child raw copies**.
Require a local repository or make a full clone (never `--depth 1`) so the
selected revision is actually available while validating it:

```bash
git -C <repo> rev-parse '<ref>^{commit}'
git -C <repo> ls-tree -r --full-tree --format='%(objectname) %(path)' <commit>
```

The manifest body is a table:

```markdown
| Path | Blob SHA |
|---|---|
| `CONTEXT.md` | `<full blob sha>` |
```

Set `manifest_only: true` in its frontmatter. A compiled article cites the
manifest in `sources:` and may use `<collection>@<commit>:<path>` for
claim-level locations. A later revision gets a new dated manifest; never edit
the prior manifest.

## MediaWiki dump

Use official XML dumps when available. They are stable, polite to the upstream
site and carry revision metadata, so they beat crawling.

1. Download or read the dump file.
2. Decompress `.bz2` with `bunzip2 -c` or `.gz` with `gunzip -c` when needed.
3. Parse streaming XML with Python stdlib `xml.etree.ElementTree.iterparse`; do not load a large dump entirely into memory.
4. Default to namespace `0`. Skip redirects, talk/user/file/special pages, and pages whose title contains `:` unless `--namespace` or filters explicitly include them.
5. For each page:
   - `upstream_id`: page id if present, else title.
   - `revision`: revision id and timestamp (also contributor when available).
   - `canonical_url`: site URL plus normalized title when derivable.
   - `content_format`: `wikitext`.
   - Preserve the latest revision text in the body.

If the dump has many pages, write in batches and rebuild indexes at the end.

## MediaWiki API

Use the API for targeted imports or when dumps are unavailable.

1. Discover `api.php` from the source URL (`https://example.org/w/api.php`, `https://example.org/wiki/api.php`).
2. Fetch the page inventory with `action=query&list=allpages&apnamespace=<namespace>&aplimit=max&format=json`. Follow `continue` until done or `--limit` is reached.
3. Fetch content in batches with `prop=revisions`, `rvslots=main` and `rvprop=ids|timestamp|user|comment|content`.
4. Optionally fetch `categories` and `links` for provenance and graph-aware compilation.
5. Respect rate limits. If the API throttles, slow down and continue; never fall back to HTML crawling.

## CSV/JSON messages

Use `csv-messages` for bounded exports where each CSV/TSV row, JSON array item
or JSONL line is a message, post, email, transcript item or similar record.
Examples include Cypherpunks-style mailing-list CSVs and message-like JSON exports.

1. Read local files directly or download URL sources to a temporary file.
2. Parse `.csv`, `.tsv`, `.json` and `.jsonl` with Python stdlib. Do not split arbitrary nested JSON unless the user identifies the message array path.
3. Infer fields conservatively:
   - id: `id`, `message_id`, `Message-ID`, `url`, or stable row number.
   - date: `date`, `created_at`, `timestamp`, `sent`, or `time`.
   - author: `author`, `from`, `sender`, `name`, or `handle`.
   - subject/title: `subject`, `title`, or the first non-empty text fragment.
   - body: `body`, `text`, `content`, `message`, `plain`, or `markdown`.
4. In `--dry-run`, and on any ambiguous schema, report row count, detected columns/keys, the inferred mapping and a few representative titles without writing. Ask before writing if the body field cannot be identified with high confidence.
5. Without stable ids, use row number plus content hash as `upstream_id` (`dataset_sha + row_number + content hash` for deduplication).
6. Write the manifest to `raw/repos/` and each message to `raw/notes/` unless the dataset is explicitly a set of articles or formal documents.
7. Preserve per-record provenance in frontmatter where known: `row_number`, `message_id`, `author`, `date`, `subject`, `dataset_sha` and `source_columns`.

Message bodies are markdown documents with a small provenance header followed by
the original message text. Preserve quoting, code blocks, URLs and mailing-list
headers that may matter for later source criticism.

## Wayback CDX

Use `wayback-cdx` for bounded archived web captures. The CDX result set is the
inventory; never recursively crawl live or archived HTML beyond it.

1. Accept either a CDX API URL or an original URL/prefix. For original URLs, query `https://web.archive.org/cdx` with JSON output and `fl=timestamp,original,statuscode,mimetype,digest,length`.
2. Use `filter=statuscode:200` and `collapse=digest` by default to avoid duplicate captures. Apply `--from`, `--to`, `--include`, `--exclude` and `--limit`.
3. Fetch each capture with the `id_` replay form, which returns the archived HTML with minimal Wayback UI rewriting: `https://web.archive.org/web/<timestamp>id_/<original-url>`.
4. Convert HTML to markdown with a readability pipeline. Prefer a temporary Python venv using `readability-lxml` plus `markdownify` or `html2text`. If dependency installation is unavailable, use WebFetch with an extraction prompt and record the fallback in `extraction_tool`.
5. Write the manifest to `raw/repos/` and each readable snapshot to `raw/articles/`. Preserve snapshot provenance in frontmatter: `wayback_timestamp`, `wayback_original`, `wayback_digest`, `statuscode`, `mimetype`, `length`, `canonical_url` and `extraction_tool`.
6. Skip binary, empty, duplicate or mostly-navigation captures; report skip counts by reason.

For volatile pages, later compilation treats a capture as evidence of what an
archived page said at a specific timestamp, not that the claim remains true.
