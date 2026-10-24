# Private Data Boundary: Read Freely, Write-Guard

This is the one hard privacy rule for public/private topic pairs. Commands,
references, and workflows that touch both sides cite this file instead of
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

**Privacy scan.** `llm-wiki privacy scan` runs the checkpoint pattern pack over
tracked text files plus `--check cross-boundary`: a public file whose link,
reference, wiki-link, `source:` value, inventory path or symlink resolves (`realpath`) into
a `*-PRIVATE/` directory or private registry topic, or a `goals.md` named from
`output/`, is critical; `checkpoint seal` runs it too. `--check tracked-private`
reports tracked `*-PRIVATE/` paths. Options: `references/cli.md`.
