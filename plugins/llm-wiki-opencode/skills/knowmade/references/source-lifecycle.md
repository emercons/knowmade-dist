# Original Source Lifecycle

Read this only when the original bytes of a source are being kept.

When a project needs to retain binary/large originals outside the wiki's
immutable Markdown `raw/` layer, run the deterministic source subsystem before
normal ingestion. Every invocation takes the same wiki scope, written `<scope>`
below: `--local` (`.wiki/` in CWD), `--wiki <slug>` (registry) or
`--path <wiki-root>`, optionally with `--hub <path>`; with none, normal wiki
resolution applies. `<cli>` is the bundled `bin/llm-wiki` (`scripts/llm-wiki` in
a source checkout). `receive` prints the source id the later steps take.

1. `<cli> source receive <url-or-file> <scope> [--quarantine-reason "<why>"]
   [--canonical-url <url>] [--retrieved-at <iso>] [--note <text>]
   [--alias <path>]...` stores bytes in the configured local store. It
   refuses ephemeral CI unless the project declares a persistent backend. A URL argument is its own canonical URL; for a file captured from
   the web earlier, `--canonical-url` (an http(s) URL) records where it came
   from and `--retrieved-at` (ISO 8601) when. `--alias` names another path of
   the same bytes. All four reach the record and the pointer. Re-receiving a
   known source fills only provenance its record lacks and adds aliases; it
   refuses to replace a recorded value.
2. `<cli> source extract <source-id> <scope>` writes a durable generic text or
   metadata representation. Text is decoded by BOM, then an HTML `<meta charset>`
   in the first 4 KB, else UTF-8, and the codec is recorded as the optional
   `source_encoding`. An image-only PDF is marked `ocr-needed`; domain
   OCR, languages, page indexes, and corpus validators stay outside the engine.
3. `<cli> source decide <source-id> [--retain local|git] <scope>` records the
   retention decision. `git` verifies SHA-256 before and after copying
   and preserves the local original. Without `--retain`, the decision comes
   from `source_store.default_decision` when that is `local` or `git`; the
   default `review-required` means a person must choose, so `decide` then fails
   and names the flag.
4. `<cli> source verify <source-id> <scope>` or `<cli> source verify --all
   <scope>` checks the pointer, derivatives and any available originals. Add
   `--portable` to model a clone without the local store, or
   `--require-originals` to fail when a `retention: local` original is missing.
   The JSON summary always carries
   `originals_available`, `originals_unavailable` and `missing_originals` (the
   unavailable `retention: local` ones), and each row its `retention`.

Commands for adapters and scripts (all print JSON and accept `--json`; exit
0, 1 when `ok` is false, 2 on a usage or store error):

- `source attach-derived <source-id> <file> --status <slug> --tool <name>`
  stores externally produced text (OCR, page-stable extraction) beside the
  generic one, e.g. `--status ocr-text`; `extract` keeps it.
- `source resolve <sha256> [--fallback <path>]...` prints the first readable
  original whose bytes hash to it: the local store, the pointer's paths, then
  each fallback. Exit 1 when none matches.
- `source paths` prints the resolved store locations, `persistent`,
  `ephemeral_ci` and `receive_allowed`.
- `source import-pointer <file> [--base <dir>] [--vault <dir>] [--dry-run]`
  validates a v1 pointer, or maps a flat legacy one (`sha256`, `bytes`,
  `local_original_path`/`vault_relative_path`/`original_repo_path`,
  `derived_text`, `source_url` or `acquisition_provenance`), into the pointers
  directory. An existing pointer for the same bytes only gains aliases.
- `source verify --pointer-file <path>` verifies one pointer anywhere. In the
  pointers directory, a file with another schema is listed under `foreign`
  and not validated (lint: `source-pointer-foreign`, info in rules/3).
- `source map` lists every alias and fails on an alias claimed by two sources.

The optional pointer keys `retrieved_at`, `note`, `aliases` and
`imported_from` stay within `llm-wiki/source-pointer/v1`: validators check
only the required keys.

After `extract`, the agent writes the processed `raw/` markdown from the
extracted representation with `source note`. A failed `receive` or `verify` stops the
ingest and is reported verbatim.

The `source_store` mapping in project-local `config.md` configures the
filesystem backend, persistence declaration, local/tracked/derived/pointer/
quarantine paths, `default_decision` and receive/Git size limits. Scalars take
a trailing `# comment`; `persistent` accepts `true/false`, `yes/no` and
`on/off` in any case and any other value is a critical lint finding and a
command error. An unknown key (a typo such as `git_max_file_mb`) is ignored with a stderr note from every `source` command
that lists the known keys. Unset paths of a hub topic resolve inside the
topic; a `-PRIVATE` wiki whose unset paths would land outside its directory
is refused until `source_store` sets them. Normal commands never delete an
original or derivative. Restricted or invalid bytes remain in quarantine with
a reason. A source pointer may support provenance for a processed `raw/`
representation, but it does not itself satisfy raw-source coverage.
