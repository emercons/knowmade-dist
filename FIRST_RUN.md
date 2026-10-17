# Knowmade First Run

Knowmade has two separate installation steps that should not be confused:

1. **Install the runtime once** — this teaches Codex/Claude/OpenCode the Knowmade workflows.
2. **Initialize or adopt a knowledge base** — this creates a `.wiki/` or hub topic and accounts for the project's source material.

Installing the plugin does **not** automatically scan the directory where you happen to be working.

## Install the runtime

Follow the [Install section of the README](README.md#install) for your runtime
(Codex, Claude Code, OpenCode, Pi, or a portable agent file). The README is the
only place that carries the version-pinned install commands, so they cannot
drift from the release; this page deliberately does not copy them. Releases
install from the public distribution repository `emercons/knowmade-dist`
without GitHub credentials; only installing from the private source repository
needs them. To move to a later release, see [Upgrade](README.md#upgrade).

After a Codex install, `@wiki` is the full workflow skill and `$wiki-query` is
the compact read-only query skill. In a Knowmade source checkout,
`./scripts/bootstrap-codex-plugin.sh --scope user --verify` installs for
development instead of from a tag.

## Codex session hooks (off by default)

A Codex install bundles six hooks (`SessionStart`, `UserPromptSubmit`,
`PostToolUse`, `PreCompact`, `PostCompact`, `Stop`) that Codex asks you to trust
under `/hooks`. They are the optional session-memory feature: they record a
redacted event for each prompt and tool call, only into `<hub>/.sessions/`.
They do nothing until you opt in:

```bash
llm-wiki-session enable    # creates <hub>/.sessions/config.json, ignores /.sessions/ in git
llm-wiki-session disable   # turns capture off again
```

You can also leave the hooks untrusted. The `@wiki` skill works either way. See
the [Session Memory section of the README](README.md#session-memory) and
[`sessions.md`](claude-plugin/skills/wiki-manager/references/sessions.md).

## New empty knowledge base

For a new project with no pre-existing research corpus, ask the full wiki workflow to initialize a project-local `.wiki/`, or create a named topic in the configured hub.

A project-local wiki is appropriate when the knowledge belongs naturally with one repository/project. A hub topic is appropriate when several related topic wikis should share one administrative root.

## Existing populated directory: brownfield adoption

This is the important case where PDFs, downloads, scraped sites, notes, repositories, and data already exist before Knowmade.

Use the deterministic `llm-wiki adopt` namespace (`scripts/llm-wiki` in a checkout, `bin/llm-wiki` in an installed runtime package). It is dry-run-first. Every flag is listed in the generated [`cli.md`](claude-plugin/skills/wiki-manager/references/cli.md) reference. The older `scripts/llm-wiki-adopt` and `scripts/llm-wiki-adopt-batch` executables remain only as compatibility shims over the same implementation; use the unified namespace in new work.

```bash
llm-wiki adopt scan /path/to/project
```

The first run writes nothing. It classifies the existing tree into:

- `ingest` — ordinary single-source documents/data suitable for normal ingestion;
- `collection` — Git repositories, archives, and bounded corpora that need collection ingestion;
- `dataset` — large/query-oriented data that should get a dataset manifest;
- `needs-human` — ambiguous media, loose code, or unknown formats;
- `skip` — generated files, dependencies, duplicates, the Knowmade engine checkout itself, and explicit exclusions.

For the complete machine-readable plan:

```bash
llm-wiki adopt scan /path/to/project --json
```

### Apply without moving source files

The safest default is reference mode:

```bash
llm-wiki adopt apply /path/to/project
```

This creates the local `.wiki/` if necessary and writes a resumable batch:

```text
.wiki/
└── inbox/
    └── .imports/
        └── <batch-id>/
            ├── PLAN.md
            ├── manifest.jsonl
            └── state.json
```

The existing project tree remains untouched.

### Process the batch safely

The batch is not just a report. It is a transaction journal that the full
Knowmade workflow can resume across sessions.

Inspect it:

```bash
llm-wiki adopt status \
  .wiki/inbox/.imports/<batch-id>
```

Mark deterministic `skip` and `needs-human` rows explicitly:

```bash
llm-wiki adopt sweep <batch> --apply
```

Before an agent semantically processes a source, it claims one row:

```bash
llm-wiki adopt claim <batch> \
  --classification ingest --limit 1 --json
```

After normal Knowmade ingestion creates a durable artifact, complete that row:

```bash
llm-wiki adopt complete <batch> \
  --item-id <id> \
  --outcome ingested \
  --destination raw/notes/<source>.md
```

Datasets use `registered-dataset`; deferred collections can use
`tracked-collection`. These durable outcomes require an existing destination
inside the owning wiki. A transient failure is recorded with `fail`; an
unfinished claim can be returned with `release`. Failed rows are retried only
with explicit `claim --retry-errors`.

When all rows are processed, verify coverage:

```bash
llm-wiki adopt verify <batch>
```

Verification fails if any row is nonterminal, a durable result disappeared, or
the original scan was truncated. This is the deterministic boundary for saying
that the old source tree has actually been accounted for.

### Copy ordinary source files into staging

```bash
llm-wiki adopt apply /path/to/project --mode copy
```

Only ordinary `ingest` file rows are copied under the batch's `staged/` tree. Collections and datasets stay referenced at their original locations so their structure is not destroyed.

### Move ordinary source files

Move mode is intentionally hard to trigger:

```bash
llm-wiki adopt apply /path/to/project --mode move --yes
```

Do not use it as the default. It moves only ordinary `ingest` files after the plan has classified them; collection/dataset roots are not recursively moved.

## Receiving individual source originals

For one-off originals (a PDF, an export) that you want to keep without
committing them, use the `llm-wiki source` namespace against a wiki root:

```bash
llm-wiki source receive /path/to/original.pdf --path /path/to/project/.wiki
llm-wiki source extract <source-id> --path /path/to/project/.wiki
llm-wiki source decide <source-id> --retain local --path /path/to/project/.wiki
llm-wiki source verify --all --portable --path /path/to/project/.wiki
```

`receive` stores the original in durable local storage (outside Git by
default), `extract` writes the derived text representation, `decide` records an
explicit `local` or `git` retention decision as a portable pointer, and
`verify` rechecks hashes and pointers. Storage paths and size limits come from
the `source_store` block of the wiki's `config.md` (shown in the README's
[Quick Start](README.md#quick-start)); every flag is in the generated
[`cli.md`](claude-plugin/skills/wiki-manager/references/cli.md).

## Do not clone Knowmade into every project

A nested checkout such as:

```text
my-research/
├── downloads/
├── sources/
└── knowmade/
```

is not the knowledge-base installation model. Knowmade's runtime should be installed separately. The adoption scanner detects a nested Knowmade engine checkout and marks it `skip` rather than ingesting its plugin source as research evidence.

## Hubs are not a mandatory mega-hub

The current implementation still supports one configured default `hub_path`, but project-local `.wiki/` roots are first-class. Do not physically merge unrelated projects merely to make them discoverable.

A future federation registry is planned to discover and cross-reference independent hubs/local wikis without copying them into one mega-hub. Until that exists, keep natural project boundaries and resolve multiple roots explicitly when a task needs them.

## What happens after adoption

Adoption is complete only when every manifest row has an explicit terminal outcome and `llm-wiki adopt verify` passes. Terminal outcomes include ingested evidence, registered datasets, tracked collections, explicit skips, and items left for human review. Merely having a `.wiki/` directory or a manifest is not proof that the old research tree has entered the knowledge lifecycle.
