# Knowmade Wiki Article And Topic-Guide Formats

`config.md`, topic guide (`schema.md`), compiled-article, Source Reference Resolution,
volatility, freshness, linking and output-artifact formats, moved verbatim out of
`wiki-structure.md`. Directory layout and the `wikis.json` registry stay in
[`wiki-structure.md`](wiki-structure.md); index, log, raw-source, naming and tag
formats are in [`wiki-format.md`](wiki-format.md).

## config.md Format

```markdown
---
title: "Wiki Title"
description: "What this wiki is about"
created: YYYY-MM-DD
freshness_threshold: 70
---

# Wiki Configuration

## Scope

[What topics this wiki covers]

## Conventions

[Any wiki-specific conventions beyond defaults]
```

## Topic Guide (`schema.md`) Format

`schema.md` is default for new topic wikis and is human-owned. It is a topic
guide, not a database schema: it captures the local vocabulary that helps
agents avoid taxonomy drift, including entity types, relationship verbs, article
subtypes, source conventions, inventory/dataset boundaries, and adoption notes.

Every runtime's compilation workflow reads a topic's `schema.md` in full before
planning new or updated articles. Its structural guidance can shape cardinality
(for example, consolidate several sources about one mechanism), article
boundaries, topic scope, and named concepts that should remain separate. This
is an operational planning input, not a second lint schema.

The guide cannot redefine deeper global primitives: the `category` vocabulary
stays `concept`/`topic`/`reference`, the physical layout stays `raw/` →
`wiki/concepts|topics|references/`, and the required-frontmatter set stays
fixed. Additional topic fields remain optional to deterministic tooling.
Likewise, prose telling compilation to exclude a raw source does not satisfy C6
coverage: the source remains visible in the uncompiled-source backlog until an
article cites it or the user explicitly re-ingests it into the correct topic.

```markdown
---
title: "Wiki Title Topic Guide"
schema_state: advisory
created: YYYY-MM-DD
updated: YYYY-MM-DD
summary: "Human-owned topic guide for local vocabulary and conventions."
---

# Wiki Title Topic Guide

> This is not a database schema and it does not make existing wiki content invalid.

## State

- `schema_state`: `advisory`

## Entity Types

| Type | Meaning |
|------|---------|
| `concept` | Bounded idea or mechanism. |

## Relationship Verbs

- `cites`
- `supports`
- `contradicts`
- `supersedes`
- `depends-on`
- `implements`

## Source Conventions

[Topic-specific evidence and boundary rules]

## Compile Guidance (optional)

[Topic-specific cardinality, article-boundary, scope, and keep-separate rules.
These rules shape compilation planning but do not redefine global categories,
frontmatter, or raw-source coverage.]
```

Adoption states:

| State | Meaning | Behavior |
|-------|---------|----------|
| `missing` | Older wiki without `schema.md` | Valid; show non-blocking adoption nudge |
| `proposed` | Librarian wrote `output/schema-proposal-*.md` | Human reviews and edits before adoption |
| `advisory` | `schema.md` exists | Follow by default; explain deliberate compile deviations |
| `strict` | Explicit opt-in in `schema.md` | Confirm planned deviations before writing; never auto-rewrite content |

Topic-guide helpers:

- `llm-wiki schema status <wiki-root>` shows whether a wiki has a schema.
- `llm-wiki schema adopt <wiki-root>` writes an advisory starter when missing.
- `llm-wiki schema adopt --dry-run <wiki-root>` previews the starter.
- `llm-wiki schema migrate --apply <wiki-root>` remains as a compatibility alias.
- `llm-wiki lint --fix <wiki-root>` may also create the default topic guide.

## Wiki Article Format (wiki/)

```markdown
---
title: "Article Title"
category: concept|topic|reference
sources: [raw/type/file1.md, raw/type/file2.md]
created: YYYY-MM-DD
updated: YYYY-MM-DD
tags: [tag1, tag2]
aliases: [alternate names for Obsidian discovery]
confidence: high|medium|low   # lint also accepts medium-high, medium-low (upstream warns on them)
volatility: hot|warm|cold
verified: YYYY-MM-DD
freshness: snapshot          # optional; a dated state, never refreshed
compiled-from: sources|conversation|mixed   # optional; defaults to "sources"
summary: "2-3 sentence summary for index"
---

# Article Title

> [One-paragraph abstract]

## [Sections as appropriate]

[Synthesized content — explain, contextualize, connect. NOT copy-paste.]

When referencing another wiki article inline, use dual-link format:
[[article-slug|Display Name]] ([Display Name](../category/article-slug.md))

This ensures both Obsidian (reads [[wikilink]]) and the agent (follows relative path) can navigate.

## See Also

- [[related-slug|Related Article]] ([Related Article](../category/related-slug.md)) — relationship note

## Sources

- [Source Title](../../raw/type/file.md) — what this source contributed
```

## Source Reference Resolution

The `sources:` field is a path list, not a bag of slugs. Maintenance workflows
that follow provenance (`librarian`, `lint`, `audit`, `refresh`, and project
staleness checks) must resolve source references with this protocol:

1. Parse `sources:` as structured YAML when possible. If using a line-based
   fallback, preserve the complete scalar after `- ` through the end of the line
   and strip only matching wrapping quotes. Never split source entries on
   whitespace.
2. Resolve exact paths first:
   - `raw/...`, `wiki/...`, and `output/...` are relative to the wiki root.
   - `../...` and `./...` are relative to the file that owns the `sources:`
     field.
   - Absolute paths are allowed only when they point inside the resolved wiki
     root; report outside paths as external/unmanaged.
3. If exact path resolution fails, use slug fallback for legacy or human-entered
   references. Normalize both the requested value and every candidate raw file
   stem by lowercasing, replacing whitespace/underscores with hyphens, removing
   non-alphanumeric characters except hyphens, collapsing repeated hyphens, and
   trimming leading/trailing hyphens. Also compare candidate stems after removing
   a leading `YYYY-MM-DD-` date prefix. A single match resolves; zero or
   multiple matches must be reported as unresolved or ambiguous.
4. Do not rename raw files during resolution. Raw immutability means old or
   imported filenames may contain spaces, title case, or upstream identifiers.
   Canonicalize future ingests, but preserve existing raw file paths.
5. When writing new `sources:` entries for filenames with spaces or punctuation,
   prefer block-list YAML and quote the path:
   `- "raw/articles/2026-01-03-Title Cased Source.md"`.
6. When linking to a raw file whose path contains spaces in article body
   markdown, use angle-bracket link destinations:
   `[Source Title](<../../raw/articles/2026-01-03-Title Cased Source.md>)`.

## Volatility Classification

Wiki articles carry a `volatility` field that controls how quickly their freshness score decays. The `verified` field records when a human last confirmed the article's conclusions are still accurate.

| Tier | Decay rate | When to use | Examples |
|------|-----------|-------------|----------|
| `hot` | Fast | Fast-moving sources: product specs, pricing, current events, competitive landscape | NVIDIA Spark specs, election results, API changelog |
| `warm` | Moderate | Quarterly-to-annual cadence: best practices, framework comparisons, market analysis | Testing patterns, CLI UX patterns, market positioning |
| `cold` | Slow | Foundational concepts, historical events, mathematical proofs, stable reference | TCP/IP fundamentals, Lindy Effect, cryptographic algorithms |

Default is `warm`. The compilation agent sets volatility based on source characteristics: news/trends sources suggest `hot`, foundational/historical sources suggest `cold`. Authors can override.

### Freshness Score (0-100)

Each source-backed article's freshness is a composite of four dimensions, each contributing 0-25 points:

| Dimension | What it measures | Computed from |
|-----------|-----------------|---------------|
| **Source freshness** | How old are the raw sources this article was compiled from? | Average days since `ingested:` across all `sources:` entries |
| **Verification recency** | When did a human last confirm accuracy? | Days since `verified:` |
| **Compilation recency** | When was this article last recompiled? | Days since `updated:` |
| **Source chain integrity** | Do all referenced sources still exist? | % of `sources:` entries that resolve to actual files |

Each dimension's decay curve is scaled by the article's `volatility` tier — a hot article's source freshness decays faster than a cold one's. The Lindy Effect applies: cold content that has survived without needing updates is more durable, not less.

Articles with `compiled-from: conversation` have no fetchable raw source chain. For those articles, skip source freshness and source chain integrity, compute verification recency and compilation recency at 0-25 points each, then multiply the 50-point subtotal by 2 so the final score still lands on 0-100. Articles with `compiled-from: mixed` use the standard four-dimension formula because they still carry raw sources.

The freshness threshold is set per wiki in `config.md` (`freshness_threshold`, default: 70); the librarian and `wiki refresh --due` flag articles scoring below it. Deterministic lint does not compute the score and never reads the threshold: it applies the day windows below.

### Lint Windows And Snapshots

Lint (C14) and `llm-wiki freshness report` call an article stale when more days have passed since `verified` (else `updated`) than its tier's window: hot 30, warm 180, cold 365 by default. `config.md` frontmatter may set `freshness_hot_days`, `freshness_warm_days` and `freshness_cold_days` (positive whole days); a topic under `HUB/topics/` inherits each key it does not set from `HUB/config.md`.

An article with `freshness: snapshot` records a dated state (a research note named for its date) and is old by design: past its window lint reports it as info, and `freshness report` leaves it out unless `--include-snapshots`. `snapshot` is the only value. An invalid window, an unknown `freshness:` value and staleness only under a window shorter than the default are reported through the rule-set ladder (info on a rules/3 corpus).

`llm-wiki freshness report --path <topic> [--due N] --json` lists the articles past their window, or within N days of it, with the windows in force and where each came from.

## Dual-Link Convention

All cross-references between wiki articles use BOTH link formats on the same line:

```
[[target-slug|Display Text]] ([Display Text](../category/target-slug.md))
```

- **Obsidian** reads the `[[wikilink]]` for its graph view, backlinks panel, and navigation
- **The agent** follows the standard markdown `(relative/path.md)` link
- Both coexist on one line so neither system misses the connection
- Wiki-link notation (what `lint` accepts): `[[slug]]`, `[[slug|text]]` and dir-qualified `[[../topics/slug]]` (resolved relative to the file, then by the last path segment). `[[_index]]` and links to files in `inventory/`, `raw/` or `output/` are valid but are not compiled articles, so lint reports them as info

For inline mentions in article body text, use the same pattern:
```
The [[transformer-architecture|Transformer]] ([Transformer](../concepts/transformer-architecture.md)) uses self-attention...
```

## Obsidian Compatibility

The wiki is designed to be opened as an Obsidian vault. On `wiki init`, a `.obsidian/` config directory is created with minimal settings. Key compatibility notes:

- YAML frontmatter `tags` field is read natively by Obsidian
- `aliases` in frontmatter lets Obsidian find articles by alternate names
- `_index.md` files appear as regular notes in Obsidian (this is fine)
- The `inbox/` folder works as a natural Obsidian inbox
- Graph view shows connections via `[[wikilinks]]`

## Output Artifact Format (output/)

```markdown
---
title: "Output Title"
type: summary|report|study-guide|slides|timeline|glossary|comparison
sources: [wiki/category/article.md, ...]
generated: YYYY-MM-DD
---

[Content in the appropriate format for the type]
```
