# Migration And Upgrade Playbook

Knowmade inherits from `llm-wiki`, and real users may start with an upstream
wiki, an older Knowmade layout, or no wiki structure at all. Migration is an
explicit workflow, not a silent rewrite.

Brownfield adoption is one namespace of the deterministic CLI, `llm-wiki adopt`:

- `llm-wiki adopt scan` / `apply` - dry-run scan and classification, then
  initialization and import-batch creation;
- `llm-wiki adopt status|claim|complete|release|fail|sweep|verify` - the
  resumable transaction/state machine around semantic processing of that batch.

Flags, defaults and help strings are in the generated
[`cli.md`](cli.md) section `llm-wiki adopt`; this playbook does not repeat them.
The CLI is `scripts/llm-wiki` in a checkout and `bin/llm-wiki` in an installed
runtime package. `scripts/llm-wiki-adopt` and `scripts/llm-wiki-adopt-batch`
(and their `bin/` copies) remain only as thin compatibility wrappers over the
same implementation; write new instructions against the namespace.

Multi-hub federation is still proposed, not implemented. See root
`DESIGN.md` and `BACKLOG.md`.

## User Stories

| Story | Starting Point | Desired End State | Default Action |
|---|---|---|---|
| Upstream to Knowmade | Existing `llm-wiki` topic wiki | Current Knowmade structure and privacy rules | Preserve raw/wiki, run lint, repair structure intentionally |
| Old Knowmade upgrade | Earlier Knowmade layout | Current inbox/public-private conventions | Back up, lint, preserve evidence paths |
| Brownfield project adoption | Existing directory with downloads, PDFs, repos, data, notes, scraped trees | Local `.wiki/` plus complete import accounting | Run `llm-wiki adopt scan`, then `llm-wiki adopt apply`; claim/process/complete manifest rows until `llm-wiki adopt verify` passes |
| Public/private split | Mixed public/private layout | Public/private sibling topics | Move intentionally and audit privacy boundaries |
| Public release prep | Private knowledge/output should become public | Redacted public copy | Copy only publishable artifacts and audit links/provenance |

## Important Boundary: Runtime Install Is Not Wiki Bootstrap

Installing the Knowmade plugin/runtime and initializing a knowledge base are
different operations.

`scripts/bootstrap-codex-plugin.sh` or the Codex marketplace commands install
or register the **runtime plugin**. They do not:

- create `.wiki/` in the current project;
- create a topic in a hub;
- scan neighboring folders;
- move downloaded material into `inbox/`;
- ingest a pre-existing research tree.

Do not clone the Knowmade source repository into each research project just to
"install the wiki" there. Install the runtime separately; initialize/adopt the
knowledge root independently.

## Brownfield Project Adoption

Use this when the project existed before Knowmade and already contains source
material.

### Dry-run first

```bash
llm-wiki adopt scan /path/to/project
```

The dry-run does not create `.wiki/` and does not change source files. It
classifies the project tree into:

- `ingest` — ordinary single-source documents or small structured data;
- `collection` — nested Git repositories, compressed archives, and corpus-like
  trees for collection ingestion;
- `dataset` — large/query-oriented data for dataset manifests;
- `needs-human` — ambiguous media, loose code, or unknown formats;
- `skip` — dependencies, generated files, duplicates, explicit exclusions,
  and nested Knowmade engine checkouts.

Use `--json` for the complete machine-readable dry-run rows.

### Apply in safe reference mode

```bash
llm-wiki adopt apply /path/to/project
```

This initializes a project-local `.wiki/` when needed and creates:

```text
.wiki/inbox/.imports/<batch-id>/
├── PLAN.md
├── manifest.jsonl
└── state.json
```

Reference mode is the default. Existing source files remain in place.

### Copy mode

```bash
llm-wiki adopt apply /path/to/project --mode copy
```

Only ordinary `ingest` file rows are staged under the batch's `staged/`
directory. Collections and datasets remain referenced at their original paths
so the specialized importer can preserve structure/provenance.

### Guarded move mode

```bash
llm-wiki adopt apply /path/to/project --mode move --yes
```

Move mode fails closed without `--yes`. It moves only ordinary single-source
`ingest` files into batch staging. It never recursively moves a Git repository,
dataset root, corpus root, or the Knowmade runtime checkout.

### Batch contract

`manifest.jsonl` records, per candidate:

- stable batch-local item id;
- original relative and absolute source path;
- file/directory kind;
- size and bounded SHA-256 when practical;
- classification;
- proposed action;
- reason;
- current batch state;
- staged path when copy/move mode staged the file.

`state.json` records batch-level schema/version, source root, wiki root, mode,
classification counts, truncation status, staged-file count, warnings, and
current coverage counts.

`PLAN.md` is the human-readable summary and next-step contract.

The adoption scan has a configurable file cap. If `scan_truncated` is true, the
batch is explicitly incomplete; no later state transition may turn it into proof
of full project coverage.

## Resumable Batch Processing

Semantic extraction remains agentic, but **batch ownership and completion are
deterministic**. Use `llm-wiki adopt` batch subcommands around every row that needs work.

### Inspect status

```bash
llm-wiki adopt status \
  .wiki/inbox/.imports/<batch-id>
```

Use `--json --items` when an agent needs machine-readable state.

### Sweep deterministic terminal rows

`skip` and `needs-human` classifications do not need semantic ingestion merely
to become explicit coverage outcomes:

```bash
llm-wiki adopt sweep <batch>
llm-wiki adopt sweep <batch> --apply
```

The first command is dry-run. `--apply` marks only those two classes terminal.
It never auto-ingests evidence or creates dataset/collection records.

### Claim before semantic work

```bash
llm-wiki adopt claim <batch> \
  --classification ingest \
  --limit 1 \
  --worker <session-or-agent-id> \
  --json
```

Claiming is a deterministic transaction. The helper:

- acquires an exclusive batch lock;
- selects only unclaimed eligible rows;
- records worker, claim token, timestamp, and attempt count;
- rewrites manifest/state atomically;
- appends an event to `events.jsonl`.

Do **not** process an adoption row semantically before claiming it when multiple
sessions/agents could touch the same batch.

### Process according to classification

After a successful claim, perform the normal Knowmade workflow:

- `ingest` -> inbox/extraction -> immutable `raw/` artifact;
- `collection` -> either full collection ingestion, or a durable corpus tracking
  artifact when ingestion is intentionally deferred;
- `dataset` -> `datasets/<slug>/MANIFEST.md` or other canonical dataset record;
- a newly discovered ambiguity -> explicit human-review outcome;
- a confirmed irrelevant/duplicate item -> explicit skip outcome.

The batch helper does not invent extraction, collection content, or dataset
schema. Those remain semantic/provenance operations.

### Complete only after durable output exists

```bash
llm-wiki adopt complete <batch> \
  --item-id <id> \
  --outcome ingested \
  --destination raw/notes/<source>.md
```

Terminal outcomes are classification-constrained:

- `ingested`;
- `registered-dataset`;
- `tracked-collection`;
- `needs-human`;
- `skipped`.

Evidence/dataset/collection outcomes require `--destination` to point to an
**existing path inside the owning wiki**. This prevents an agent from declaring
success before writing a durable artifact.

### Failure and retry

On transient/semantic failure:

```bash
llm-wiki adopt fail <batch> \
  --item-id <id> --error "<reason>"
```

Error rows are visible and are not silently retried. Reclaim them explicitly:

```bash
llm-wiki adopt claim <batch> --retry-errors --limit 1
```

If a worker decides not to process an already claimed row yet, release it:

```bash
llm-wiki adopt release <batch> --item-id <id>
```

### Interrupted move batch

`adopt claim` exits 2 on a batch whose `state.json` says `staging: interrupted`
(a `--mode move --apply` that died part-way): unmoved originals still sit at
their source paths. `adopt verify` lists which rows moved. `adopt repair <batch>
--yes` stages the rest; `claim --allow-interrupted` overrides the guard.

### Lock recovery

Mutations use `.batch.lock` and fail closed if another worker owns the batch.
`--force-unlock` exists only for verified stale-lock recovery. Never use it as a
normal concurrency shortcut.

### Final verification

```bash
llm-wiki adopt verify <batch>
```

Verification passes only when:

1. every manifest row has a terminal state;
2. every durable result path still resolves inside the owning wiki;
3. the original adoption scan was not truncated.

`state.json` is updated with terminal/pending/claimed/error counts and overall
status. `events.jsonl` is append-only operational history. Neither file is
factual evidence.

A `.wiki/` directory, an import manifest, or even a fully staged batch is not by
itself proof that the legacy source tree was accounted for. **Passing batch
verification is the deterministic coverage boundary.**

## Existing Upstream `llm-wiki` To Knowmade

Use this when the source is already a structurally recognizable wiki.

1. Back up or work in a copy.
2. Decide public/private placement. Under the current hub model, a private
   sibling is exactly `<slug>-PRIVATE`.
3. Preserve `raw/` as processed evidence. Do not move it back to `inbox/`.
4. Ensure future intake uses `inbox/` and `inbox/.processed/`.
5. Add `goals.md` only from actual user-provided goals, and only in private
   context where appropriate.
6. Run deterministic lint without `--fix` first.
7. Apply only unambiguous structural fixes.
8. Re-run lint and append a migration log entry.

The current configured `hub_path` is a default administrative root, not a claim
that all knowledge must live in one physical mega-hub.

## Old Knowmade To Current Knowmade

1. Back up the wiki.
2. Keep raw evidence and compiled article paths stable unless an explicit
   migration rule requires otherwise.
3. Treat genuinely unprocessed source dumps as inbox material; do not confuse
   already-processed `raw/` with intake.
4. Keep private goals separate from factual evidence.
5. Run deterministic lint and repair only mechanical defects automatically.
6. Use librarian/audit workflows for semantic quality; do not hide semantic
   migration inside structural lint fixes.

## Public/Private Split

Under the current single-default-hub model:

1. Use sibling topic roots `topics/<slug>/` and `topics/<slug>-PRIVATE/`.
2. Keep private material outside public publication/version-control scope.
3. Public material may support private work; private material must not leak into
   public claims/outputs without an explicit redaction/publication pass.
4. Lint both sides when both exist.
5. Audit public outputs for private paths, private goals, private inventory, and
   private raw references.

## Multi-Hub Boundary

Knowmade still lacks a first-class federation registry and durable cross-hub
logical link resolver.

Until federation exists:

- do not merge unrelated hubs just to make them discoverable;
- do not use absolute cross-hub filesystem paths as durable identity;
- keep natural project-local `.wiki/` roots independent;
- resolve multiple roots explicitly when one task needs them;
- treat future stable IDs/federation as a separate migration layer.

## Field Notes From A Real Hub Upgrade

From upgrading eight wikis (one hub, six topics, two project-local `.wiki/`
trees) to v0.24.3. Full write-up: `docs/history/field-notes-hub-upgrade.md` in the repo.

**Nothing stamps the engine version.** Date a wiki by its defects instead:

| Signature | Current shape |
|---|---|
| `type: raw-article`, `source_type:`, `source_urls:` | `type: articles` + `source:` + `ingested:` |
| `type: chat-intake` / `intake` in `raw/notes/` | one of `articles`, `data`, `notes`, `papers`, `repos` |
| `type: candidate` + `status: open` | `kind:` + current-enum `status:` + `priority:` |
| `inventory/views/*.md` with no `view:` | `view:` + `summary:` |
| Hand-written `_index.md` prose | generated contract index |

Only `goals.md` at a topic root and `inbox/` at the **hub** root are
Knowmade-only; topic-level `inbox/` is upstream, and a `*-PRIVATE` sibling
proves nothing. Record the version by hand after upgrading.

**`--fix` hazards.** Two fixers can disagree and the destructive one wins: a
valid inventory record one directory too high is quarantined into
`inbox/.unknown/` rather than moved where the placement check says, breaking
inbound links — read the auto-fixed list for `Moved ... to inbox/.unknown/`.
Relocating a file breaks links in both directions. Index regeneration
overwrites hand-written prose. `--fix` never repairs raw or inventory
frontmatter; that is always hand work, and it is most of the effort.

**Order:** back up first (private topics are usually gitignored, so git is no
safety net) → lint everything read-only → fix criticals by hand → then `--fix`
per wiki reviewing `git diff` → re-lint and repair link fallout → log entry
per wiki.

## Test Expectations

Brownfield adoption has deterministic coverage in:

- `tests/test-local-cli-adopt.sh` — scanning/classification/init/staging;
- `tests/test-local-cli-adopt-batch.sh` — claim/complete/release/fail/retry,
  deterministic sweep, locking, destination checks, event journal, terminal
  verification, and truncated-scan rejection.

`scripts/run-checks` runs both harnesses plus the docs, package,
structural, lint, and runtime-sync tests. The lane runs locally and gates
every push through `.githooks/pre-push`; there is no GitHub Actions job.

## Remaining Work

Track implementation in root `BACKLOG.md`. Important follow-ups now are:

- wire the agent workflow/evals so natural-language `@wiki` adoption reliably
  uses the deterministic batch state machine;
- add selective subtree adoption without rescanning unrelated content;
- full old-upstream/old-Knowmade migration fixtures;
- private-to-public redaction checker;
- stable hub/wiki identity, federation registry, and cross-hub resolution.
