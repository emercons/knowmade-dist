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
`SKILL.md` or the Knowmade profile itself. Each branch below names the
reference it reads:

- **Status** (no arguments, `status`, `stats`, `show`) runs `<cli> status` and
  loads no reference file.
- **`init`** runs `<cli> init`; its manual fallback and the starter suggestions
  after a successful init read the setup reference.
- **`config`** reads `~/.config/llm-wiki/config.json`; its procedures are in a
  setup reference.
- **Routing** (free-form text) reads the router and nothing else; the routed
  command then loads its own references.
- The first-run welcome flow, the `init` manual fallback and the `config`
  procedures are rare, so they live in one small setup reference read only on
  those branches.

---

### If $ARGUMENTS contains "init"

Initialize a new wiki. Parse arguments:
- `init <name>` → create topic wiki at `HUB/topics/<name>/`
- `init <name> --local` → create local wiki at `.wiki/` in current project
- `init` (no name) → ask: "What topic is this for?" Then create the topic wiki with their answer.

**A topic name is always required.** There is no bare global wiki — HUB is only a hub (wikis.json + _index.md + log.md). All content lives in topic sub-wikis.

**Run `<cli> init --topic <slug> [--description ...]`** (or `--local`, or a path; `--dry-run` previews). It scaffolds the hub if needed and the topic with a fresh `wiki_id`, exits 1 writing nothing on a conflict (slug registered elsewhere or archived), and is a no-op on an existing wiki. Pass the answer to "What is this wiki about?" as `--description`. Do the manual fallback only if the CLI is unavailable: `skills/knowmade/references/wiki-setup.md` § Init manual fallback.

Report what was created and suggest the starter options of `skills/knowmade/references/wiki-setup.md` § Welcome flow, Step 3, plus:
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

The user is new or hasn't initialized a wiki yet. Instead of dumping a command
list, walk them through getting started: follow
`skills/knowmade/references/wiki-setup.md` § Welcome flow (welcome, ask the
topic, run `<cli> init --topic <slug>`, offer the starter options).

---

### If $ARGUMENTS contains "config"

Configure the wiki system: `config hub-path [<path>]` sets or shows the hub
location and a bare `config` shows all configuration. The procedures are in
`skills/knowmade/references/wiki-setup.md` § Config; the `config.json`
semantics are in `skills/knowmade/references/hub-resolution.md`.
