---
description: "Knowmade practical knowledge base — understands natural language. Say what you want (dispatch the hub inbox, edit a document through a registered private adapter, apply a personal specialist method, capture or shape an Idea, turn an approved Idea into a Project, add a URL, collect a catalog, track inventory, ask a question, research, audit, or resume work) and it routes to the right workflow. Also handles init, status, and config."
argument-hint: "[<natural language request>] [init <topic-name> [--local]] [config hub-path [<path>]] [--wiki <name>]"
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(ls:*), Bash(wc:*), Bash(mkdir:*), Bash(date:*), Bash(mv:*), Bash(python3:*), Bash(scripts/llm-wiki:*), Bash(${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki:*)
---

## Your task

**Resolve the wiki.** A few file reads, not a search: never `find`, Explore, or
scan the filesystem. The CLI resolves HUB and the target wiki itself, so never
re-implement it: `<cli> hub where` names the hub. `<cli>` is
`"${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki"` (`scripts/llm-wiki` in a source
checkout). Archived entries are rejected unless the user targets one with
`--include-archived`. If the hub is stat-able but reads fail with `Operation not
permitted`, stop and report the launcher permission denial. Full protocol, for
the CLI-unavailable fallbacks below: `skills/knowmade/references/hub-resolution.md`.

Variant: **wiki-neutral** — `wiki.md` is the router, init, and config command,
so "wiki missing" is not always an error; the init subcommand creates the wiki,
status shows an empty hub gracefully, and the natural-language router explains
how to create one.

You are the Knowmade knowledge base manager. `llm-wiki` is upstream.

**Reference loading.** Nothing is read up front, and this command never loads
`SKILL.md` or the Knowmade profile itself:

- **Status** (no arguments, `status`, `stats`, `show`) runs `<cli> status` and
  loads no reference file.
- **`init`** runs `<cli> init`. Only the manual fallback loads
  `references/wiki-structure.md` (layout, `wikis.json`),
  `references/wiki-format.md` (`_index.md`, `log.md`) and
  `references/wiki-articles.md` (`config.md`, topic guide).
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

**Run `<cli> init --topic <slug> [--description ...]`** (or `--local`, or a path; `--dry-run` previews). It scaffolds the hub if needed and the topic with a fresh `wiki_id`, exits 1 writing nothing on a conflict (slug registered elsewhere or archived), and is a no-op on an existing wiki. Pass the answer to "What is this wiki about?" as `--description`. Do the manual fallback only if the CLI is unavailable.

**Manual fallback.** In order:

1. **Collision check.** Look at `HUB/topics/<slug>/`, `HUB/topics/.archive/<slug>/` and any `wikis.json` entry with that slug. If an archived topic exists, stop and ask whether to restore it with `/knowmade:archive restore <slug>` or choose a different slug; never silently create an active topic over an archived topic boundary.
2. **Hub, if missing** (layout: `wiki-structure.md` § Hub; formats: `wiki-format.md`): `HUB/wikis.json` (empty registry), `_index.md` (empty topic wiki table), `log.md`, `topics/`, and optionally `inbox/` with `.routed/`, `.needs-human/`, `.skipped/` for global intake. No `raw/`, `wiki/`, `inventory/`, `datasets/`, `output/`, `config.md` or `.obsidian/` at the hub. `HUB/.gitignore` holds `topics/**/*-PRIVATE/` (private topics stay out of git, live or under `topics/.archive/`) and the machine-local lines `/.sessions/`, `/.skills/registry.json`, `**/.audit/`, `**/.librarian/`, `**/.session-events.jsonl`, `**/.session-checkpoint.json`, `**/.obsidian/` and `**/.wiki-local/`. Write it even if the hub is not a git repository yet, or sits inside a larger one; lint tolerates it and `lint --fix` appends whatever is missing once the hub is in a git work tree.
3. **Topic directories** (`wiki-structure.md` § Topic Sub-Wiki): `inbox/` with `.processed/`; `raw/` with `articles/ papers/ repos/ notes/ data/`; `wiki/` with `concepts/ topics/ references/ theses/`; `output/`. Not `inventory/` or `datasets/`: `/knowmade:inventory`, `/knowmade:dataset` or lint create those lazily. For `--local`, ignore `.wiki/` in a git project (`wiki-structure.md` § Local Wiki).
4. **`.obsidian/`** with `app.json` `{"showFrontmatter": true, "alwaysUpdateLinks": true, "newLinkFormat": "relative", "useMarkdownLinks": false}`, `appearance.json` `{"accentColor": ""}` and `graph.json` `{"collapse-filter": false, "search": "", "showTags": true, "showAttachments": false, "showOrphans": true, "collapse-color-groups": false, "collapse-display": false, "showArrow": true, "textFadeMultiplier": 0, "nodeSizeMultiplier": 1, "lineSizeMultiplier": 1}`.
5. **Indexes and log.** An empty `_index.md` (today's date, all counts 0; `wiki-format.md` § _index.md Format) only in the directories created above, none for optional layers that do not exist yet; `log.md` with the entry `## [YYYY-MM-DD] init | Wiki initialized`.
6. **`config.md` and `schema.md`.** Ask "What is this wiki about?" and write `config.md` (title, description, scope, today's date; `wiki-articles.md` § config.md Format) plus a starter `schema.md` in `schema_state: advisory` with a small default vocabulary (`wiki-articles.md` § Topic Guide); it is human-owned and can be trimmed later.
7. **Register** (`wiki-structure.md` § wikis.json Format) in `HUB/wikis.json` with the portable relative path `topics/<slug>` and update the hub `_index.md` table; a `-PRIVATE` topic is registered by slug and path only, never with its description or title (`wikis.json` and the hub index are committed). For a local wiki outside the hub run `<cli> identity assign <wiki-root>` first, then add a `local_wikis` entry with its `id` (the `wiki_id`) and a path relative to HUB (`../<project>/.wiki`). Never write a path with a drive letter, `/home/...` or `/Users/...` into a tracked `wikis.json`; if no relative path reaches the wiki, ask the user instead of recording an absolute one.

Report what was created and suggest the starter options of the no-wiki Step 3 below, plus:
- `/knowmade:adapter list` — inspect explicitly registered local private adapters
- `/knowmade:inventory add ingest-candidate "title"` — track a candidate, corpus, entity, or next action
- `/knowmade:dataset add "title" --location <path-or-url>` — index a large/external dataset
- `/knowmade:compile` — compile sources into wiki articles
- `/knowmade:query <question>` — ask questions

---

### If $ARGUMENTS is freeform text (not "init", "config", or empty) and a wiki exists

The user typed something that isn't a known keyword. Read
`skills/knowmade/references/router.md` and follow it: it holds the single
routing table (first match wins), the precedence rules, the confidence and
ambiguity handling, and the negative controls. Invoke the matched `wiki x`
row as `Skill: knowmade:x` with the user's text as arguments. Do not route from
memory or from any other table.

Before routing an external-action request, run the route check with the
bundled CLI, normalizing the requested effect to a lowercase token first:
`<cli> adapter route --intent <effect> --resource '<url>' --json`
(flags in `adapter route --help`). If the CLI cannot run, treat the
result as `unavailable` for an action request and fail closed; `router.md`
says how each outcome routes.

---

### If $ARGUMENTS is empty (or just "status"/"stats"/"show") and a wiki exists

Show wiki status, CLI-first.

1. **Topic wiki** (`--wiki <name>`, `--local`, a path, or a `.wiki/` in the CWD): run `<cli> status [--wiki <name> | --local | <path>]` (`--json` for scripting) and show its output: title, location, source/article/inventory/dataset/output counts, inbox pending, last log entry, lint and freshness summaries. Add the last 5 recent changes from the topic `_index.md`. If the CLI cannot run, read `config.md`, `_index.md` and `log.md` and count the files per layer. Status is read-only and refuses a hub root or a private placeholder (exit 2).
   - If the topic is active and has no root `schema.md`, show one non-blocking
     topic-guide adoption nudge. For small/simple wikis:
     `Optional topic guide missing. Existing wiki is valid. Add a starter with: llm-wiki schema adopt <wiki-root>`
     For established wikis with roughly 10+ compiled articles and no recent
     `output/schema-proposal-*.md` or `.librarian/REPORT.md` covering schema
     advice:
     `Optional topic guide missing. Run /knowmade:librarian scan --passes staleness,quality,conventions for a proposal, then apply the useful parts to schema.md.`
   - Do not block normal status/query/compile/ingest on this nudge. Do not
     auto-create `schema.md`, proposal outputs, or dismissal files from status.
     Suppress the nudge when a schema or recent schema proposal already exists.

2. **Hub** (HUB, no topic selected): `<cli> hub index --stats` counts active, archived and private topics and flags registry drift; list the active topics from `HUB/wikis.json`.
   - For each active registered topic run `<cli> status --wiki <slug>`: it applies the `wikis.local.json` overlay and the stale-path fallback to `HUB/topics/<slug>`. `-PRIVATE` topics are listed and marked private (status is read-only, so the Write-Guard never applies here). A private placeholder (a `-PRIVATE` topic absent on this machine) makes `status` exit 2: list that topic as "unavailable on this machine" and continue; never fail the table.
   - Only if the CLI cannot run, read `HUB/_index.md`, `HUB/wikis.json` and each topic's `_index.md` by hand; the overlay and stale-path rules are in `skills/knowmade/references/hub-resolution.md` § Registry Path Resolution.
   - Show a summary table: wiki name, description, source count, article count.
   - Show `Archived topics: N` (registry entries with `status: archived` or paths under `topics/.archive/`) and suggest `/knowmade:archive list --archived` when N > 0.
   - Show the last 5 entries of `HUB/log.md`.

3. **Stale indexes.** Before trusting an `_index.md`, check it: `<cli> index check --wiki <slug>` for a topic (exit 1 means stale; `index rebuild` repairs the derived rows and leaves the master statistics and dates alone), `<cli> hub index --check` for the hub (`--write` refreshes its generated table).

4. List available subcommands.

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

**Step 2: On user response,** derive a slug from their topic (lowercase, hyphens, max 40 chars) and run `<cli> init --topic <slug> --description "<their topic>"` (add `--hub <path>`; with no configured hub, ask the user where to create it — never assume `~/wiki/`). If the CLI is unavailable, run the manual fallback above.

**Step 3: After init completes,** suggest the immediate next action based on what's most likely useful:

> **Wiki created at `HUB/topics/<slug>/`**
>
> What would you like to do first?
>
> 1. **Research** — I'll search the web and build your knowledge base automatically
>    → Just say: `/knowmade:research "<your topic>" --wiki <slug>`
>
> 2. **Add a specific source** — paste a URL or file path
>    → Just say: `/knowmade:ingest <url>`
>
> 3. **Import existing notes** — drop files into `HUB/topics/<slug>/inbox/`
>    → Then run: `/knowmade:ingest --inbox`
>
> 4. **Import an existing wiki/repo** — ingest a Git docs repo or MediaWiki dump
>    → Just say: `/knowmade:ingest-collection <url-or-path>`
>
> 5. **Collect examples or artifacts** — find many things, dedupe them, and save a provenance-rich catalog
>    → Just say: `/knowmade:collect "<things to collect>"`
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

Set a custom hub location in `~/.config/llm-wiki/config.json`.

1. If `<path>` is provided:
   - Expand only a leading `~` to validate it on this machine; check it exists as a directory, else offer to create it.
   - Write `~/.config/llm-wiki/config.json` (create `~/.config/llm-wiki/` if needed) with the portable path as the user typed it:
     ```json
     {
       "hub_path": "<path as the user typed it>"
     }
     ```
     Do not write `resolved_path` for normal shared hubs: it bakes in this machine's `/Users/<name>/...` path and breaks iCloud-shared wiki folders on another Mac. Older configs that already have it remain readable as a fallback.
   - Optionally suggest `ln -s "<expanded path>" ~/wiki` so `~/wiki/` resolves immediately even without reading config.
   - If a wiki already exists at the OLD hub location (previous config path or `~/wiki/` fallback), ask "Move existing wiki data from `<old>` to `<new>`? (y/n)". If yes: `mv <old>/* <new>/` and update hub-owned `wikis.json` topic paths to relative `topics/<slug>` entries. If no: only update the config; the user moves data manually.
   - Report: "Hub path set to `<path>`. All wiki commands now use this location."

2. If no `<path>` provided: run `<cli> hub where` (it names the winning hub and where it came from), read the config if it exists, and report "Current hub path: `<path>`" (prefer `hub_path`; mention `resolved_path` only if it is the only value) or "No hub configured. Run `config hub-path <path>` to set one."

#### `config` (no subcommand)

Show all configuration:
- **Hub path**: the hub `<cli> hub where` names (and whether it is from config or default)
- **Config file**: `~/.config/llm-wiki/config.json` (exists / not found)
- **Topic wikis**: count from wikis.json
