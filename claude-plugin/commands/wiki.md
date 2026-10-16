---
description: "Knowmade practical knowledge base — understands natural language. Say what you want (dispatch the hub inbox, edit a document through a registered private adapter, apply a personal specialist method, capture or shape an Idea, turn an approved Idea into a Project, add a URL, collect a catalog, track inventory, ask a question, research, audit, or resume work) and it routes to the right workflow. Also handles init, status, and config."
argument-hint: "[<natural language request>] [init <topic-name> [--local]] [config hub-path [<path>]] [--wiki <name>]"
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(ls:*), Bash(wc:*), Bash(mkdir:*), Bash(date:*), Bash(mv:*), Bash(python3:*), Bash(scripts/llm-wiki:*), Bash(${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki:*)
---

## Your task

<!-- hub-prelude:start -->
**Resolve the wiki.** A few file reads, not a search: never `find`, Explore, or scan the filesystem. Full protocol and edge cases: `skills/wiki-manager/references/hub-resolution.md`.
1. HUB: read `$HOME/.config/llm-wiki/config.json` and use `hub_path` (expand only a leading `~`); `resolved_path` is a fallback cache; with no config use `$HOME/wiki`. If the hub is stat-able but reads fail with `Operation not permitted`, stop and report the launcher permission denial.
2. Wiki, first match: `--local` → `.wiki/`; `--wiki <name>` → `HUB/wikis.json` entry (stale path → `HUB/topics/<name>`); `.wiki/` in CWD; else HUB. Reject archived entries (`status: archived`, `topics/.archive/`) unless the command takes `--include-archived`.
3. `<slug>` and `<slug>-PRIVATE` are sibling topics in one hub: read private freely, write under the Write-Guard in `skills/wiki-manager/references/knowmade-profile.md` § Private Data Boundary.
<!-- hub-prelude:end -->
4. Read `<wiki>/_index.md` if found. Variant: **wiki-neutral** — `wiki.md` is the router, init, and config command, so "wiki missing" is not always an error; the init subcommand creates the wiki, status shows an empty hub gracefully, and the natural-language router explains how to create one.

You are the Knowmade knowledge base manager. `llm-wiki` is upstream.

**Reference loading.** Nothing is read up front, and this command never loads
`SKILL.md` or the Knowmade profile itself:

- **Status** (no arguments, `status`, `stats`, `show`) reads only the hub
  `_index.md`, `wikis.json`, each topic's `_index.md` (plus `config.md` for one
  topic) and `log.md`. It reads `references/indexing.md` only when an index is
  stale and must be rebuilt.
- **`init`** reads `references/wiki-structure.md` for the layout and the `wikis.json`
  registry, `references/wiki-format.md` for the `_index.md` and `log.md`
  formats, and `references/wiki-articles.md` for the `config.md` and topic-guide
  formats, and nothing else.
- **`config`** reads `~/.config/llm-wiki/config.json` and no reference file.
- **Routing** (free-form text) reads `references/router.md` and nothing else;
  the routed command then loads its own references.

---

### If $ARGUMENTS contains "init"

Initialize a new wiki. Parse arguments:
- `init <name>` → create topic wiki at `HUB/topics/<name>/`
- `init <name> --local` → create local wiki at `.wiki/` in current project
- `init` (no name) → ask: "What topic is this for?" Then create the topic wiki with their answer.

**A topic name is always required.** There is no bare global wiki — HUB is only a hub (wikis.json + _index.md + log.md). All content lives in topic sub-wikis.

**Steps:**

1. If HUB doesn't exist yet, create the hub first:
   - `HUB/wikis.json` (empty registry)
   - `HUB/_index.md` (hub index with empty topic wiki table)
   - `HUB/log.md` (global activity log)
   - `HUB/topics/` directory
   - `HUB/.gitignore` containing the line `topics/**/*-PRIVATE/`, so private
     topics stay out of git whether they live in `topics/` or
     `topics/.archive/`, and the machine-local state lines `/.sessions/`,
     `/.skills/registry.json`, `**/.audit/`, `**/.librarian/`,
     `**/.session-events.jsonl`, `**/.session-checkpoint.json`,
     `**/.obsidian/` and `**/.wiki-local/`. Write it even if the hub is not a
     git repository yet, or sits inside a larger one; lint tolerates it and
     `lint --fix` appends whatever is missing once the hub is in a git work tree.
   - Optional `inbox/` with `.routed/`, `.needs-human/`, and `.skipped/` may be
     created when the user wants global intake.
   - NO `raw/`, `wiki/`, `inventory/`, `datasets/`, `output/`, `config.md`, or `.obsidian/` at the hub level.

2. Create the core topic wiki directory structure:
   - `inbox/`, `inbox/.processed/`
   - `raw/`, `raw/articles/`, `raw/papers/`, `raw/repos/`, `raw/notes/`, `raw/data/`
   - `wiki/`, `wiki/concepts/`, `wiki/topics/`, `wiki/references/`, `wiki/theses/`
   - `output/`
   - Do not create `inventory/` or `datasets/` during init. Those layers are
     created lazily by `/wiki:inventory`, `/wiki:dataset`, or lint when a
     partially existing layer needs repair.
   - For local wikis (`--local`): append `.wiki/` to the project's `.gitignore`.

3. Create `.obsidian/` directory with minimal vault config:
   - `.obsidian/app.json`:
     ```json
     {
       "showFrontmatter": true,
       "alwaysUpdateLinks": true,
       "newLinkFormat": "relative",
       "useMarkdownLinks": false
     }
     ```
   - `.obsidian/appearance.json`:
     ```json
     {
       "accentColor": ""
     }
     ```
   - `.obsidian/graph.json`:
     ```json
     {
       "collapse-filter": false,
       "search": "",
       "showTags": true,
       "showAttachments": false,
       "showOrphans": true,
       "collapse-color-groups": false,
       "collapse-display": false,
       "showArrow": true,
       "textFadeMultiplier": 0,
       "nodeSizeMultiplier": 1,
       "lineSizeMultiplier": 1
     }
     ```

4. Create empty `_index.md` only in the directories created during init,
   following the format in `references/wiki-format.md` § _index.md Format. Use today's date.
   Set all counts to 0. Do not write empty indexes for optional layers that do
   not exist yet.

5. Create `log.md` with initial entry:
   ```
   # Wiki Activity Log

   ## [YYYY-MM-DD] init | Wiki initialized
   ```

6. Ask the user: "What is this wiki about?" Use their answer to create
   `config.md` with title, description, scope, and today's date. Also create a
   starter `schema.md` in `schema_state: advisory` with a small default
   vocabulary; it is human-owned and can be trimmed later.

7. Before registering, check both `HUB/topics/<slug>/` and
   `HUB/topics/.archive/<slug>/`, plus any `wikis.json` entry with that slug.
   If an archived topic already exists, stop and ask whether to restore it with
   `/wiki:archive restore <slug>` or choose a different slug. Do not silently
   create a new active topic over an archived topic boundary.

8. Register in `HUB/wikis.json` with a portable relative path (`topics/<slug>`) and update hub `_index.md` topic wiki table. For a local wiki outside the hub, run `"${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki" identity assign <wiki-root>` first (`scripts/llm-wiki` in a source checkout), then add a `local_wikis` entry with its `id` (the `wiki_id`) and a path relative to HUB (`../<project>/.wiki`). Never write a path with a drive letter, `/home/...` or `/Users/...` into a tracked `wikis.json`: it points into one checkout on one machine and discloses its layout. If no relative path reaches the wiki, ask the user instead of recording an absolute one.

9. Report what was created and suggest:
   - `/wiki:research "topic" --sources 10` — auto-research to bootstrap
   - `/wiki:ingest <url|file|text>` — add source material
   - `/wiki:ingest-collection <repo|wiki-dump>` — bulk import a bounded upstream collection
   - `/wiki:adapter list` — inspect explicitly registered local private adapters
   - `/wiki:collect "<things>"` — find, dedupe, and catalog examples, artifacts, tools, memes, or source candidates
   - `/wiki:idea new "<seed>"` — capture a proposal for research and shaping before Project commitment
   - `/wiki:inventory add ingest-candidate "title"` — track a candidate, corpus, entity, or next action
   - `/wiki:dataset add "title" --location <path-or-url>` — index a large/external dataset
   - `/wiki:compile` — compile sources into wiki articles
   - `/wiki:query <question>` — ask questions

---

### If $ARGUMENTS is freeform text (not "init", "config", or empty) and a wiki exists

The user typed something that isn't a known keyword. Read
`skills/wiki-manager/references/router.md` and follow it: it holds the single
routing table (first match wins), the precedence rules, the confidence and
ambiguity handling, and the negative controls. Invoke the matched `wiki x`
row as `Skill: wiki:x` with the user's text as arguments (the Hub Inbox
Dispatch row points at `references/ingestion.md`). Do not route from memory or
from any other table.

Before routing an external-action request, run the route check with the
bundled CLI, normalizing the requested effect to a lowercase token first:
`"${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki" adapter route --intent <effect> --resource '<url>' --json`
(`scripts/llm-wiki` in a source checkout; flags in
`skills/wiki-manager/references/cli.md`). If the CLI cannot run, treat the
result as `unavailable` for an action request and fail closed; `router.md`
says how each outcome routes.

---

### If $ARGUMENTS is empty (or just "status"/"stats"/"show") and a wiki exists

Show wiki status. Before reading any `_index.md`, stale-check it: count `.md` files in the directory vs rows in the index table. If mismatched, rebuild inline from file frontmatter first (see `references/indexing.md` Derived Index Protocol).

1. If at the hub level (HUB):
   - Read `HUB/_index.md` and `HUB/wikis.json`
   - For each active registered topic wiki, read its `_index.md` to get current stats; `-PRIVATE` topics are listed and marked private (status is read-only, so the Write-Guard never applies here)
   - Count archived topics from registry entries with `status: archived` or
     paths under `topics/.archive/`
   - Show a summary table: wiki name, description, source count, article count
   - Show `Archived topics: N` and suggest `/wiki:archive list --archived` when
     N > 0
   - Show global log (last 5 entries)

2. If targeting a specific topic wiki (`--wiki <name>` or local):
   - Read its `_index.md` for statistics and recent changes
   - Read `config.md` for title and description
   - Count actual files for accuracy
   - Show: title, location, source/article/inventory/dataset/output counts, inbox pending, last compiled/lint dates, last 5 recent changes
   - If the topic is active and has no root `schema.md`, show one non-blocking
     topic-guide adoption nudge. For small/simple wikis:
     `Optional topic guide missing. Existing wiki is valid. Add a starter with: llm-wiki schema adopt <wiki-root>`
     For established wikis with roughly 10+ compiled articles and no recent
     `output/schema-proposal-*.md` or `.librarian/REPORT.md` covering schema
     advice:
     `Optional topic guide missing. Run /wiki:librarian scan --passes staleness,quality,conventions for a proposal, then apply the useful parts to schema.md.`
   - Do not block normal status/query/compile/ingest on this nudge. Do not
     auto-create `schema.md`, proposal outputs, or dismissal files from status.
     Suppress the nudge when a schema or recent schema proposal already exists.

3. List available subcommands

---

### If no wiki exists and no "init" argument

The user is new or hasn't initialized a wiki yet. Instead of dumping a command list, walk them through getting started.

**Step 1: Welcome and orient.** Explain what Knowmade does in one sentence, then ask what they want to research or accomplish:

> **Welcome to Knowmade** — a practical knowledge system that captures material once, routes it to focused public or private topic wikis, and turns sourced knowledge into useful decisions and outputs.
>
> To get started, what topic would you like to research? For example:
> - Quantum computing
> - Nutrition and supplements
> - Kubernetes deployment patterns
>
> Just tell me the topic, and I'll set everything up.

**Step 2: On user response,** derive a slug from their topic (lowercase, hyphens, max 40 chars) and run the full init protocol:
1. Create the hub if it doesn't exist (at the resolved HUB path from config, or ask the user where to create it if no config exists — never assume `~/wiki/`)
2. Create the topic wiki at `HUB/topics/<slug>/` with the core directory structure
3. Register in wikis.json and update hub _index.md
4. Create config.md using the user's topic description

**Step 3: After init completes,** suggest the immediate next action based on what's most likely useful:

> **Wiki created at `HUB/topics/<slug>/`**
>
> What would you like to do first?
>
> 1. **Research** — I'll search the web and build your knowledge base automatically
>    → Just say: `/wiki:research "<your topic>" --wiki <slug>`
>
> 2. **Add a specific source** — paste a URL or file path
>    → Just say: `/wiki:ingest <url>`
>
> 3. **Import existing notes** — drop files into `HUB/topics/<slug>/inbox/`
>    → Then run: `/wiki:ingest --inbox`
>
> 4. **Import an existing wiki/repo** — ingest a Git docs repo or MediaWiki dump
>    → Just say: `/wiki:ingest-collection <url-or-path>`
>
> 5. **Collect examples or artifacts** — find many things, dedupe them, and save a provenance-rich catalog
>    → Just say: `/wiki:collect "<things to collect>"`
>
> 6. **Capture an Idea** — save a rough proposal, research it, and shape it before committing to a Project
>    → Just say: `I have an idea for ...`

Do NOT show the full command reference, config options, or advanced flags during onboarding. Keep it to these starter options. The user can discover the rest via `/wiki` (status view) once they have a wiki.

**Permission hint (one-time):** If this is the first wiki being created, also append:

> **Tip:** Research sessions fetch many URLs. To skip approval prompts, add this to your project's `.claude/settings.local.json`:
> ```json
> "WebFetch", "WebSearch"
> ```
> in the `permissions.allow` array.

---

### If $ARGUMENTS contains "config"

Configure the wiki system.

#### `config hub-path <path>`

Set a custom hub location. Creates `~/.config/llm-wiki/config.json`.

**Steps:**

1. If `<path>` is provided:
   - Expand only a leading `~` in the path to validate it on this machine.
   - Check if the path exists as a directory. If not, offer to create it.
   - Write `~/.config/llm-wiki/config.json` (create `~/.config/llm-wiki/` if needed) with the user-facing portable path:
     ```json
     {
       "hub_path": "<path as the user typed it>"
     }
     ```
     Do not write `resolved_path` for normal shared hubs; it bakes in this machine's `/Users/<name>/...` path and can break iCloud-shared wiki folders on another Mac. Older configs that already have `resolved_path` remain readable as a fallback.
   - Suggest creating a symlink for maximum robustness:
     > For shell convenience, optionally run: `ln -s "<expanded path>" ~/wiki`
     > This makes `~/wiki/` always resolve immediately, even without reading config.
   - If a wiki already exists at the OLD hub location (previous config path or `~/wiki/` fallback):
     - Ask: "Move existing wiki data from `<old>` to `<new>`? (y/n)"
     - If yes: move contents (`mv <old>/* <new>/`), update hub-owned `wikis.json` topic paths to relative `topics/<slug>` entries
     - If no: just update the config — user will move data manually
   - Report: "Hub path set to `<path>`. All wiki commands now use this location."

2. If no `<path>` provided (just `config hub-path`):
   - Read `~/.config/llm-wiki/config.json` if it exists
   - Report current hub path (prefer `hub_path`; mention `resolved_path` only if it is the only value or was used as a fallback)
   - Report: "Current hub path: `<path>`" or "No hub configured. Run `config hub-path <path>` to set one."

#### `config` (no subcommand)

Show all configuration:
- **Hub path**: current resolved path (and whether it's from config or default)
- **Config file**: `~/.config/llm-wiki/config.json` (exists / not found)
- **Topic wikis**: count from wikis.json
