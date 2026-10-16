# Knowmade Profile

Knowmade is the identity of this project. The upstream project is
`llm-wiki`; Knowmade inherits its topic-wiki structure, provenance rules,
indexing discipline, and multi-runtime packaging, but it is not just an
academic research wiki. It is a practical problem-solving system: it researches,
tracks decisions, accumulates useful context, and turns evidence into plans,
artifacts, and actions.

At the highest level, Knowmade exists to help a human act on real goals,
desires, needs, and constraints. In private topic wikis, those human intentions
are the North Star for the work. They decide which questions matter, which gaps
are worth filling, which outputs should be produced, and what "useful" means.
They are planning context, not factual evidence.

## Command Notation

References name workflows as `wiki x` (for example `wiki ingest`, `wiki init`).
`wiki x` is `/wiki:x` in Claude Code and `$wiki x` in Codex (or an `@wiki`
request); in any other runtime it is the equivalent natural-language request.

## Upstream Relationship

- `llm-wiki` is upstream. Keep compatibility with upstream wiki structure unless
  a Knowmade rule below intentionally overrides it.
- Knowmade may ingest or migrate existing `llm-wiki` topic wikis. Treat those as
  legacy-compatible inputs, not as foreign data.
- When upstream and Knowmade diverge, preserve migration notes and lint rules so
  older wikis can be upgraded intentionally instead of silently rewritten.

## Public And Private Topic Wikis

Knowmade's default deployment model is one hub:

```json
{
  "hub_path": "~/knowmade"
}
```

An upstream-style path remains valid:

```json
{
  "hub_path": "~/wiki"
}
```

Private and public versions are separate topic wiki roots inside that hub:
`topics/<slug>/` and `topics/<slug>-PRIVATE/`. The private topic may contain
personal goals, preferences, constraints, and source material that should not
leak. Public repos should ignore private topic directories with `.gitignore`
using `topics/**/*-PRIVATE/`, which also covers archived private topics under
`topics/.archive/` (the older `topics/*-PRIVATE/` does not, because `*` never
matches `/`). The private suffix is exact and uppercase:
`workshop` pairs with `workshop-PRIVATE`; `workshop-private` is
legacy/noncanonical and should be migrated.

Public material may be referenced from private work. What private material
may reach a public file is governed by the next section; it is the single
statement of that rule.

## Private Data Boundary: Read Freely, Write-Guard

This is the one hard privacy rule for public/private topic pairs. Commands,
references, and workflows that touch both sides cite this heading instead of
restating it.

**Private material** is anything under a `-PRIVATE` topic root (its `raw/`,
`wiki/`, `inventory/`, `output/`, `log.md`, sessions, and inbox), any
`goals.md`, private inventory records, private inbox or raw source files, and
private adapter or specialist allowlists.

**Reading is free.** When a `-PRIVATE` topic is accessible, any workflow may
read it for context: sibling index peeks, `--with <wiki>`, `--wiki all`, hub
status, portfolio views, query auto-selection, and plan context assembly may
all include private topics. Reading private material never needs a flag.

**Writing is guarded.** Before writing into a public (non-`-PRIVATE`) topic or
producing a public output (exported files, checkpoints, `output/` artifacts,
promoted notes, compiled public articles), apply the write-guard:

1. Trace every fact, citation, link, and `source:`/`sources:` entry that came
   from private material. Each one is either dropped from the public write, or
   the write target moves to the private sibling (`<slug>-PRIVATE`).
2. Public files must not contain `-PRIVATE` path strings, `source:`/`sources:`
   entries pointing into a private topic, private goals, private inventory, or
   private source files. There is no "redacted enough" middle ground inside a
   public file: the whole derived item moves or goes.
3. When a public workflow used private context, say so in the response and
   append the note to `log.md` of the private sibling, never to the public
   `log.md` or any other public file.

An explicit private-to-public publication pass requested by the user is a
deliberate, reviewed copy, not an exception to the write-guard. The checkpoint
privacy scanner (`llm-wiki checkpoint seal`) is the deterministic backstop for
`-PRIVATE` path strings; everything else relies on this rule.

## Inbox-First Source Lifecycle

Sources enter through `inbox/`. The raw layer is not the drop zone.

1. A user, agent, or import places source material in `inbox/` or stages it
   there as a URL/text/file capture.
2. The agent processes the inbox item: fetches, extracts, classifies, summarizes,
   and records provenance.
3. The processed artifact is written to `raw/<type>/` as an immutable backup of
   what was extracted.
4. The original inbox item is moved to `inbox/.processed/` or left pending if
   classification is uncertain.
5. Compilation reads `raw/` and writes synthesized articles under `wiki/`.

This keeps user dumps, partial captures, and unclassified material out of the
canonical raw corpus until an agent has actually processed them.

### Exception: session distillates

Inbox-first governs external material: URLs, files, pasted text, imports and
research captures. Session distillates are not external sources. `wiki ll`,
`wiki session promote` and `wiki feedback promote` write `raw/notes/` directly
because they are produced by the hub's own tooling from session data it already
holds: the live session for `wiki ll`, and digests and candidates captured under
`HUB/.sessions/` for the two promote commands. A promoted note's `source:` is
the `HUB/`-relative path of its session artifact, a `wiki ll` note uses
`source: session`, and neither copies a raw transcript. The Private Data Boundary
write-guard still applies to all three: private material never lands in a
public topic's note, and the two promote commands refuse a target slug that does
not end in `-PRIVATE` unless `--allow-public` is passed, which also drops machine
identity from the note. Every other path into `raw/` goes through `inbox/`.

## Brownfield Projects

A project may contain a large source tree before Knowmade is initialized. In
that case, **do not assume creating `.wiki/` makes the old files visible to the
knowledge lifecycle**, and do not recursively move the whole project into
`inbox/`.

Read [migration.md](migration.md) and use the deterministic brownfield helpers:

1. `llm-wiki adopt scan` classifies the existing tree without writing;
   `llm-wiki adopt apply` initializes `.wiki/` and creates an import batch under
   `inbox/.imports/<batch-id>/`.
2. `llm-wiki adopt claim|complete|release|fail|sweep` own resumable row state.
   Before semantic processing, claim a row; after creating the appropriate
   durable raw/dataset/collection artifact, complete it; on interruption release
   or fail it explicitly.
3. Treat `llm-wiki adopt verify` as the coverage boundary. Do not claim
   brownfield migration is complete merely because `.wiki/`, `manifest.jsonl`,
   or staging files exist.
4. Multiple agents must use the deterministic claim/lock protocol rather than
   independently selecting manifest rows.

The batch files and event journal are operational state, not factual evidence.
Factual claims still trace to `raw/`/`wiki/`.

## Goal-Directed Workflow

Academic wikis can optimize for neutral coverage. Knowmade private wikis should
optimize for human usefulness: the point is not to know everything about a
topic, but to help the human satisfy the goals and needs that made the topic
worth tracking.

When operating in a private topic wiki, any substantial research, planning,
output, audit, assessment, or implementation-support workflow should assemble a
short goal brief and keep it in context while working:

1. Read `config.md` for the topic scope and conventions.
2. Read `goals.md` when it exists.
3. Read high-priority inventory records when they describe active tasks,
   candidates, constraints, watch items, or decisions.
4. Summarize the current human intent in a few bullets before choosing sources,
   deciding what to compile, ranking gaps, or producing outputs.

The goal brief should guide tradeoffs: practical relevance over encyclopedic
completeness, actionable outputs over abstract summaries, and explicit next
steps over vague "interesting further research." When the user's stated intent
changes, update the private goal context or create/update inventory records so
future sessions inherit the new direction.

Keep the boundary clean:

- Goals and desires explain why the work matters.
- `inventory/` tracks durable tasks, candidates, watch items, and decisions
  that need status, priority, or next actions.
- `output/` contains generated deliverables.
- `output/projects/<slug>/WHY.md` records the goal and rationale for a group of
  related deliverables.
- `raw/` and `wiki/` remain the evidence and synthesis layers for factual
  claims.

Do not use private goals as citations for facts. When a recommendation depends
on a private goal, say so as reasoning. When a factual claim needs support, cite
raw or wiki sources.

## Cross-Topic Human Intake

Humans often work across several unrelated topic wikis in one sitting. Knowmade
should optimize for fast capture: the human can dump unclassified research
results into `HUB/inbox/` and let agents dispatch them later.

The dispatcher operates at the hub level. It reads the active topic registry and
topic scopes, classifies each hub inbox item by subject and privacy, routes it
to the appropriate topic `inbox/`, and leaves the original in `.routed/` after
the target copy exists. The dispatcher should be shallow and careful; it does
not turn hub inbox items directly into `raw/` or compiled wiki articles.

Worker agents then operate inside the selected topic wiki, with that topic's
goals, sources, and privacy boundary in context. This keeps the human capture
flow simple without blending unrelated medical, business, personal, or technical
contexts into one semantic wiki.

## Personal Goals And Desires

Knowmade private wikis should store human goals, desires, constraints, and
current decisions in private context only:

- Private context: `goals.md` at a private topic's root when the goals are
  broad and stable.
- Topic-level context: `config.md` scope plus an optional `goals.md` for that
  topic.
- Operational tracking: inventory records for goals, tasks, candidates, and
  watch items that need status, priority, or next actions.

Research, planning, output generation, audit, and assessment should consult the
private goal context when operating in a private topic wiki. Public topic
operations may read private goals for context; what reaches public files is
governed by § Private Data Boundary: Read Freely, Write-Guard above.

When goals affect a recommendation, say so in the reasoning. When a factual
claim needs evidence, cite raw/wiki sources rather than private goals.
