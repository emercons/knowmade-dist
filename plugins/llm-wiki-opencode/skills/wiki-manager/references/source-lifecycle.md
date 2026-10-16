# Original Source Lifecycle

Moved verbatim out of `ingestion.md` (it was `ingestion.md` § Original Source
Lifecycle). Read it only when the original bytes of a source are being kept.

When a project needs to retain binary/large originals outside the wiki's
immutable Markdown `raw/` layer, run the deterministic source subsystem before
normal ingestion. Every invocation takes the same wiki scope, written `<scope>`
below: `--local` (`.wiki/` in CWD), `--wiki <slug>` (registry) or
`--path <wiki-root>`, optionally with `--hub <path>`; with none, normal wiki
resolution applies. `<cli>` is the bundled `bin/llm-wiki` (`scripts/llm-wiki` in
a source checkout). `receive` prints the source id the later steps take.

1. `<cli> source receive <url-or-file> <scope> [--quarantine-reason "<why>"]
   [--canonical-url <url>] [--retrieved-at <iso>]` stores bytes in the
   configured durable local store. It refuses ephemeral CI unless the project
   explicitly declares a persistent backend. A URL argument is its own
   canonical URL; for a file captured from the web earlier, `--canonical-url`
   (an http(s) URL) records where it came from and `--retrieved-at` (ISO 8601)
   when, in the local record. Both apply to a new record only; re-receiving a
   known source never changes its record.
2. `<cli> source extract <source-id> <scope>` writes a durable generic text or
   metadata representation. An image-only PDF is marked `ocr-needed`; domain
   OCR, languages, page indexes, and corpus validators stay outside the engine.
3. `<cli> source decide <source-id> [--retain local|git] <scope>` records the
   explicit retention decision. `git` verifies SHA-256 before and after copying
   and preserves the local original. Without `--retain`, the decision comes
   from `source_store.default_decision` when that is `local` or `git`; the
   default `review-required` means a person must choose, so `decide` then fails
   and names the flag.
4. `<cli> source verify <source-id> <scope>` or `<cli> source verify --all
   <scope>` checks the pointer, derivatives and any available originals. Add
   `--portable` to model a clone without the local store, or
   `--require-originals` to fail when a `retention: local` original is missing,
   since that is the only copy. The JSON summary always carries
   `originals_available`, `originals_unavailable` and `missing_originals` (the
   unavailable `retention: local` ones), and each row its `retention`.

After `extract`, the agent still writes the processed `raw/` markdown from the
extracted representation as usual. A failed `receive` or `verify` stops the
ingest and is reported verbatim.

The `source_store` mapping in project-local `config.md` configures the
filesystem backend, persistence declaration, local/tracked/derived/pointer/
quarantine paths, `default_decision` and receive/Git size limits. Scalars take
a trailing `# comment`; `persistent` accepts `true/false`, `yes/no` and
`on/off` in any case and any other value is a critical lint finding and a
command error. A key the engine does not know (a typo such as
`git_max_file_mb`) is ignored with a stderr note from every `source` command
that lists the known keys. Unset paths of a hub topic resolve inside the
topic; a `-PRIVATE` wiki whose unset paths would land outside its directory
is refused until `source_store` sets them. Normal commands never delete an
original or derivative. Restricted or invalid bytes remain in quarantine with
a reason. A source pointer may support provenance for a processed `raw/`
representation, but it does not itself satisfy raw-source coverage.
