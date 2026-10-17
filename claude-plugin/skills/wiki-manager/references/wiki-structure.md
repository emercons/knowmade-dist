# Knowmade Wiki Directory Structure

> **Configurable hub path**: Knowmade uses one `hub_path` read from
> `~/.config/llm-wiki/config.json` (`resolved_path` is a legacy fallback). If no
> config exists, `~/wiki/` remains the upstream-compatible fallback. Throughout
> this document, `HUB/` means the resolved hub path.

## Public And Private Topic Pairs

The preferred configuration is `{ "hub_path": "~/knowmade" }`. Public and
private versions are separate topic roots inside that one hub:
`topics/<slug>/` and `topics/<slug>-PRIVATE/`. The suffix is exact and uppercase.
Either side may be absent. Public repositories should ignore
`topics/**/*-PRIVATE/`, which covers both the live topic and its archived form
under `topics/.archive/` (the older `topics/*-PRIVATE/` rule misses the
archive because `*` never matches `/`); public outputs must not cite private
goals, sources, inventory, sessions, or paths without an explicit
redaction/publication pass.

## Hub (HUB/)

The hub is lightweight. It tracks topic wikis, optional operational session
memory, an optional global intake queue, and an optional personal specialist
method library. It does not contain topic `raw/`, `wiki/`, `inventory/`,
`datasets/`, or `output/` layers.

```
HUB/                               # resolved from ~/.config/llm-wiki/config.json
├── wikis.json                     # Registry of all topic wikis
├── _index.md                      # Lists topic wikis with stats
├── log.md                         # Global activity log
├── .sessions/                     # Optional redacted harness session state
├── inbox/                         # Optional unclassified human dump queue
│   ├── .routed/
│   ├── .needs-human/
│   └── .skipped/
├── .skills/                       # Optional personal specialist method library
│   ├── _index.md                  # Derived specialist catalog
│   ├── registry.json              # Per-active-topic specialist allowlists
│   └── <name>/
│       ├── SKILL.md               # Agent Skills-compatible review method
│       └── references/            # Optional Markdown references only
└── topics/                        # Each topic is a full wiki
    ├── dementia/
    ├── dementia-PRIVATE/          # Private sibling, normally gitignored
    ├── quantum-computing/
    ├── .archive/                  # Archived topic wikis, hidden by default
    │   └── old-topic/
    └── ...
```

`HUB/inbox/` is staging only. A dispatcher reads active topic scopes and
privacy boundaries, routes each item into one or more topic `inbox/`
directories, and moves the original to `.routed/` only after target copies
exist. Topic workers then ingest and compile inside their own roots.

The hub still has no `raw/`, `wiki/`, `inventory/`, `datasets/`, `output/`,
`config.md`, `schema.md`, or `.obsidian/`. `.skills/` contains reusable
instructions and allowlists, not evidence or topic content. Its packages are
instruction-only: `SKILL.md` plus optional Markdown under `references/`. See
[specialists.md](specialists.md).

## Topic Sub-Wiki (HUB/topics/<name>/)

All content lives here. Init creates a core structure first, including a
human-owned advisory `schema.md`. Optional layers are created lazily when a
command needs them. This keeps new wikis fast to create and avoids blank
scaffolding for inventory, datasets, and generated sidecars that may never be
used.

```
HUB/topics/<name>/
├── .obsidian/                     # Optional Obsidian vault config
├── _index.md                      # Master index: stats, quick nav, recent changes
├── .librarian/                    # Optional: wiki-only maintenance reports
│   ├── REPORT.md
│   └── scan-results.json
├── .audit/                        # Optional: umbrella audit reports
│   ├── REPORT.md
│   └── scan-results.json
├── config.md                      # Title, scope, conventions
├── goals.md                       # Optional private goals/desires for this topic
├── schema.md                      # Topic-local vocabulary/conventions
├── log.md                         # Topic-level activity log
├── inbox/                         # Drop zone for this topic
│   └── .processed/
├── inventory/                     # Lazy: durable tracking records (see inventory.md)
│   ├── _index.md
│   ├── items/                     # Physical/digital items, parts, tools, assets
│   │   ├── _index.md
│   │   └── *.md
│   ├── ideas/                     # Proposals shaped before Project commitment
│   │   ├── _index.md
│   │   └── *.md                   # kind: idea
│   ├── candidates/                # Ingest candidates, tasks, questions, watch items
│   │   ├── _index.md
│   │   └── *.md
│   ├── entities/                  # People, orgs, projects, venues, standards bodies
│   │   ├── _index.md
│   │   └── *.md
│   ├── corpora/                   # Source collections, archives, datasets, forums
│   │   ├── _index.md
│   │   └── *.md
│   └── views/                     # Derived chat/list views over inventory
│       ├── _index.md
│       └── *.md
├── datasets/                      # Lazy: dataset manifests for large/external data
│   ├── _index.md
│   └── <dataset-slug>/
│       ├── _index.md
│       ├── MANIFEST.md
│       ├── samples/_index.md      # Lazy: created by dataset sample
│       ├── profiles/_index.md     # Lazy: created by dataset profile
│       └── queries/_index.md      # Lazy: created for query recipes
├── raw/                           # Immutable processed artifacts extracted from inbox
│   ├── _index.md
│   ├── articles/
│   │   ├── _index.md
│   │   └── *.md
│   ├── papers/
│   │   ├── _index.md
│   │   └── *.md
│   ├── repos/
│   │   ├── _index.md
│   │   └── *.md
│   ├── notes/
│   │   ├── _index.md
│   │   └── *.md
│   └── data/
│       ├── _index.md
│       └── *.md
├── wiki/                          # Compiled articles (LLM-maintained)
│   ├── _index.md
│   ├── concepts/
│   │   ├── _index.md
│   │   └── *.md
│   ├── topics/
│   │   ├── _index.md
│   │   └── *.md
│   ├── references/
│   │   ├── _index.md
│   │   └── *.md
│   └── theses/                    # Thesis investigations
│       ├── _index.md
│       └── *.md
└── output/                        # Generated artifacts
    ├── _index.md
    ├── projects/                  # Project folders (see projects.md)
    │   ├── <slug>/
    │   │   ├── WHY.md             # Required: goal + rationale in plain markdown
    │   │   ├── *.md               # Markdown deliverables
    │   │   ├── *.png, *.svg       # Colocated images/diagrams
    │   │   ├── code/              # Optional — prototype scripts
    │   │   └── data/              # Optional — CSVs, JSON exports
    │   └── .archive/              # Archived projects (moved here by wiki project archive)
    │       └── <slug>/
    │           └── WHY.md
    └── *.md                       # Loose outputs (backward compatible)
```

See [inventory.md](inventory.md) for inventory records, [ideas.md](ideas.md) for
the Concept → Idea → Project workflow, [datasets.md](datasets.md) for dataset
manifests, and [projects.md](projects.md) for the full projects architecture.
Files under `inventory/views/` are derived list/table views. They are not
inventory records and should not be treated as authoritative tracking state.
Missing optional roots (`inventory/`, `datasets/`, `.obsidian/`, `.librarian/`,
or `.audit/`) mean the layer has not been used yet. Missing `schema.md` means
an older wiki has not adopted a topic guide; it remains valid, but status/lint should
offer a non-blocking adoption path.

## Local Wiki (--local flag)

Same structure as above but rooted at `<project>/.wiki/` without `wikis.json`
or `topics/`. Local wikis are normally private by default. In a git project
`.wiki/` must be ignored, because `inbox/.imports/` holds staged copies of
adopted material. `llm-wiki adopt apply` does this itself: when git does not
already ignore the wiki it appends `.wiki/` to the project's `.gitignore`
(creating the file if needed) and says so; `--no-gitignore` opts out and prints
a warning. A hand-made `init --local` appends the same rule unless the user
wants the wiki committed. Outside git nothing is written.

## Wiki Resolution Order

When a command runs, first resolve the hub path (HUB) from
`~/.config/llm-wiki/config.json` (see `hub-resolution.md`). Public/private
selection happens at the topic slug, not by choosing another hub. Then resolve:

1. `--local` flag present → `<cwd>/.wiki/`
2. `--wiki <name>` flag present → look up name in `HUB/wikis.json`; resolve `<HUB>`, leading `~`, absolute, and HUB-relative paths, and fall back to `HUB/topics/<name>` when a registry path is stale. Explicit private selection normalizes to `<name>-PRIVATE`; explicit public selection removes that suffix.
3. Current directory has `.wiki/` → use it
4. Otherwise → HUB

## wikis.json Format

```json
{
  "default": "<HUB>",
  "wikis": {
    "hub": { "path": "<HUB>", "description": "Global knowledge base" },
    "<topic>": { "path": "topics/<topic>", "description": "...", "status": "active" },
    "<archived-topic>": {
      "path": "topics/.archive/<archived-topic>",
      "description": "...",
      "status": "archived",
      "archived": "YYYY-MM-DD",
      "archive_reason": "optional"
    }
  },
  "local_wikis": [
    { "id": "<wiki_id>", "path": "../<project>/.wiki", "description": "..." }
  ]
}
```

A `-PRIVATE` topic is registered by slug and path only (plus `id` and `status`
bookkeeping): `wikis.json` and the hub `_index.md` are committed with the hub,
so they never carry a private topic's description or title. The `_index.md` row
lists the slug with empty Title/Description cells, and lint reports a
`description` on a private entry (`private-registry-description`, info).

Topic paths inside the shared hub should be relative (`topics/<topic>`) or use
the `<HUB>` token. Avoid storing `/Users/<name>/...` absolute paths for
hub-owned topic wikis; those break when an iCloud wiki is opened from another
Mac with a different home directory.

External `local_wikis` entries follow the same rule: a path relative to HUB
plus the wiki's `id` (`wiki_id` from its `config.md`, created by
`identity assign`), so a moved or replaced wiki is detected instead of trusted.
Never commit a drive-letter, `/home/...` or `/Users/...` path to a tracked
`wikis.json`; the checkpoint privacy scan flags it. Until logical references
exist (`KM-HUB-006`), refer to another wiki in content by its `wiki_id`, not
by a machine path.

Archived topic wikis live under `topics/.archive/<slug>` and should keep their
registry entries with `status: archived`. Normal wiki resolution, status,
query, compile, research, output, librarian, refresh, and audit workflows skip
archived entries unless the user explicitly includes archived content. See
[archive.md](archive.md) for lifecycle semantics and restore rules.

## Formats

Other file formats live next to this layout, not in it, so a command loads only the part it needs:

- [wiki-format.md](wiki-format.md): `_index.md`, `log.md`, raw source files, file naming, tags.
- [wiki-articles.md](wiki-articles.md): `config.md`, topic guide (`schema.md`) and Compile Guidance, compiled articles, Source Reference Resolution, Volatility Classification and Freshness Score, Dual-Link Convention, Obsidian compatibility, output artifacts.
