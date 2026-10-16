# Ingestion Protocol

## Overview

Knowmade ingestion is inbox-first. External material is placed in or staged into
a topic wiki's `inbox/` directory, then the agent processes that inbox item into
a standardized immutable artifact in `raw/`. `raw/` is a backup of what was
extracted; it is not the user-facing place to drop new sources.

Inbox-first governs external material. The one exception is that session
distillates (`wiki ll`, `wiki session promote`, `wiki feedback promote`) write
`raw/notes/` directly, because they come from the hub's own tooling and
already-captured session data and the private write-guard still applies; see
[`knowmade-profile.md`](knowmade-profile.md) § Inbox-First Source Lifecycle,
Exception: session distillates.

When the user is working across multiple unrelated topic wikis, they may drop
unclassified material into `HUB/inbox/` first. That global hub inbox is an
intake queue for dispatch, not a raw source layer. A dispatcher routes each item
to the right topic inbox before normal topic-specific ingestion begins.

For URLs and pasted text, create a small inbox capture first (`.url` or `.md`)
when there is no existing file. For local files, copy or move the user-provided
file into `inbox/` before extraction unless it is already there. Once
processing succeeds, move the inbox item to `inbox/.processed/`.

## Global Hub Inbox Dispatch

Moved to [hub-dispatch.md](hub-dispatch.md) (Human, Dispatcher and Worker
workflows). Read it only for hub inbox dispatch; topic-level ingestion below
does not need it.

## Source Types

| Type | Directory | Auto-detect signals |
|------|-----------|-------------------|
| articles | raw/articles/ | General web URLs, blog posts |
| papers | raw/papers/ | arxiv.org, scholar.google, .pdf URLs/files, academic language |
| repos | raw/repos/ | github.com, gitlab.com URLs |
| notes | raw/notes/ | Freeform text, tweets, no URL |
| data | raw/data/ | small .csv, .json, .tsv URLs or files, dataset references |

If the data is large, mutable, remote, sensitive, or better queried in its
native format, use the dataset registry (`references/datasets.md`) instead of
copying it into `raw/data/`.

If the user wants to remember, rank, watch, or decide later about a source,
create or suggest an inventory record instead of ingesting immediately. Ingest
is for accepted source material; inventory is for durable candidates and next
actions. After ingesting a source that was tracked in inventory, link the raw
path back from the inventory record and report any status update the user should
approve.

## Collection Ingestion

Moved to [ingestion-collections.md](ingestion-collections.md) (collection
manifest, child sources, adapters, collection compilation). Read it for
`wiki ingest-collection`; single-source ingestion does not need it.

## URL Ingestion

0. Stage the URL as an inbox capture, usually `inbox/YYYY-MM-DD-title.url` or
   `inbox/YYYY-MM-DD-title.md` with the URL and request context. Then process
   that inbox item.

1. **Detect X.com / Twitter URLs**: If the URL matches `x.com/*/status/*` or `twitter.com/*/status/*`, follow this fallback chain in order:

   **a) Grok MCP (preferred)**: Check if the `grok` MCP server is available by looking for tools matching `mcp__grok__*` (e.g., `mcp__grok__search`). If available, use it to fetch the tweet/thread content. Extract: author handle, display name, full text, date, media descriptions, thread context.

   **b) FxTwitter proxy**: If Grok MCP is not available, rewrite the URL:
   - `x.com/user/status/123` → `https://api.fxtwitter.com/user/status/123`
   - WebFetch this API URL — it returns JSON with full tweet text, author, media, and thread data.
   - Parse the JSON response for `tweet.text`, `tweet.author`, `tweet.created_at`.

   **c) VxTwitter proxy**: If FxTwitter fails, try:
   - `x.com/user/status/123` → `https://api.vxtwitter.com/user/status/123`
   - Same JSON extraction as FxTwitter.

   **d) Direct WebFetch**: Last resort — WebFetch the original `x.com` URL. This often returns limited content (login walls), but sometimes works for public tweets.

   **e) Manual fallback**: If all above fail, report: "Could not fetch tweet content. Options: install a Grok MCP server for X.com access, or paste the tweet text manually via `wiki ingest \"text\" --title \"@author tweet\"`."

   Type: notes (unless overridden).

2. **Detect PDF URLs**: If the URL ends in `.pdf` or returns a PDF content type,
   download it to a temporary file and follow the PDF file ingestion flow.
   Type: papers by default unless the content is clearly legal/regulatory
   correspondence better treated as articles.

3. **GitHub repo URLs**: Use WebFetch with prompt:

   > "Extract from this GitHub repository: name, description, key technologies, main purpose, README content. Format as markdown."

4. **General URLs**: Use WebFetch to retrieve content. Prompt:

   > "Extract the complete article content from this page. Return: title, author(s) if listed, date published if listed, and the full article text preserving all factual claims, data points, code examples, and technical details. Format as clean markdown."

5. **Failure handling**: If WebFetch fails (auth wall, paywall), report the failure. Suggest: paste content manually via `wiki ingest "text" --title "Title"`.

## File Ingestion

0. If the file is not already under `inbox/`, copy or move it into `inbox/`
   first. Do not write it directly into `raw/`.
1. Read the inbox file directly
2. Markdown → preserve formatting
3. Plain text → wrap in markdown
4. PDF → extract to markdown using the PDF ingestion flow below
5. JSON/CSV/structured data → describe schema + representative sample for
   single-source ingest, or hand off to `ingest-collection --adapter
   csv-messages` when the user wants one source per row/message
6. Images → create a metadata stub noting the image path and any visible content description

### Original Source Lifecycle

Moved to [source-lifecycle.md](source-lifecycle.md). Read it only when the
original bytes of a source are retained (the deterministic `source` CLI).

### PDF Ingestion

PDFs are single-source ingests, not collection imports. Use them for court
filings, regulatory papers, academic PDFs, reports, and scanned documents whose
content should become one raw markdown source.

1. Determine source type:
   - `papers` for academic, technical, regulatory, or report-like PDFs.
   - `articles` for legal filings, court exhibits, notices, or web-published
     documents that are not papers.
2. Try `pdftotext -layout <pdf> -` only if it is available and produces
   non-trivial text. If local poppler is broken, missing, or returns garbled
   output, do not keep retrying it.
3. Fallback to a temporary Python virtual environment and a PDF library:
   - Prefer `pypdf` for text-first PDFs because it is lightweight.
   - Use `pymupdf` when layout fidelity or extraction quality matters.
   - Record `extraction_tool` and any dependency/version in frontmatter or a
     short provenance note.
4. If the PDF is image-only and no OCR tool is available, create a metadata stub
   with `extraction_status: ocr-needed`, page count if detectable, file hash,
   and the original path/URL. Do not invent text from the filename.
5. Preserve page boundaries in the body with `## Page N` headings when the
   extractor exposes them. Keep footnotes, docket numbers, tables, citations,
   and regulatory/legal identifiers intact.
6. Include extra frontmatter when known: `content_format: pdf`, `sha256`,
   `page_count`, `extraction_tool`, `extraction_status`, and `fetched` for URL
   PDFs.

## Freeform Text Ingestion

1. User provides quoted text as the argument
2. Stage it as an inbox markdown capture with the text and any title/context
3. If `--title` not provided, derive a title from the first sentence or ask
4. Auto-tag based on content keywords

## Inbox Processing

The `inbox/` directory is the source intake boundary. Users dump files there via
Finder, `cp`, etc.; agents also stage URL/text captures there before writing
processed artifacts to `raw/`.

### Processing `--inbox`:

1. Scan `inbox/` for all files (exclude `.processed/` subdirectory and hidden files)
2. For each file:
   - `.url` or `.webloc` files → extract the URL, then follow URL ingestion flow
   - `.md` or `.txt` files → ingest as notes or articles (auto-detect)
   - `.pdf` files → extract to markdown with the PDF ingestion flow
   - `.json`, `.csv`, `.tsv` → ingest as data, or hand off to
     `ingest-collection --adapter csv-messages` for per-message sources
   - Other files → create a metadata stub noting file type and path
3. Move each processed file to `inbox/.processed/` (or copy if user passed `--keep`)
4. Report each item processed
5. If 5+ items were processed, suggest: "You've ingested N new sources. Want me to compile? Run `wiki compile`"

## Slug Generation

1. Take the title, lowercase, replace spaces with hyphens, remove special characters
2. Prepend today's date: `YYYY-MM-DD-`
3. Truncate to 60 characters max (not counting .md extension)
4. Example: "Attention Is All You Need" → `2026-04-04-attention-is-all-you-need.md`
5. If a file with that slug already exists, append `-2`, `-3`, etc.
6. This canonicalization applies to new ingests. If a legacy or imported raw
   file already exists with spaces, title case, or upstream naming, do not
   rename it during later maintenance; provenance workflows resolve exact paths
   and slug fallbacks per `wiki-articles.md` Source Reference Resolution.

## Post-Ingestion Index Updates

After writing each processed raw artifact, update indexes in order:

1. `raw/{type}/_index.md` — add row to Contents table
2. `raw/_index.md` — add row to Contents table
3. `_index.md` (master) — increment source count, add to Recent Changes
4. Move the inbox item to `inbox/.processed/` and preserve enough provenance to
   trace the raw artifact back to the intake item

## Batch Ingestion

If the user provides multiple URLs or paths (comma-separated, space-separated, or one per line), process each sequentially. Report progress after each item.

## Compilation Nudge

After ingestion, count uncompiled sources (sources ingested after last compile date). If 5+, suggest running `wiki compile`.
