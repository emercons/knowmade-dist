---
description: "Archive or restore whole topic wikis so old interests stay preserved but out of default context. Archived topics move under HUB/topics/.archive and are hidden from normal semantic and maintenance workflows."
argument-hint: "list [--archived] | topic <slug> [--reason \"why\"] | restore <slug> | peek <query> [--wiki <name>] [--include-archived] [--hub <path>] [--private|--public]"
allowed-tools: Read, Glob, Grep, Bash(ls:*), Bash(date:*), Bash(python3:*), Bash(scripts/llm-wiki:*), Bash(${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki:*)
---

## Your task

Manage the archive lifecycle for topic wikis. Archive is a context filter, not
deletion: preserve the wiki, remove it from normal semantic workflows, and keep
explicit restore/maintenance possible.

Archive moves are deterministic, so this command calls the CLI. It never runs
`mkdir`/`mv` or edits `wikis.json`, the hub `_index.md` or `log.md` by hand.
Read `skills/wiki-manager/references/archive.md` for the lifecycle and
`skills/wiki-manager/references/cli.md` for exact flags.

### Locate the CLI and the hub

Use `"${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki"` in Claude Code, or `scripts/llm-wiki`
in a source checkout. If it cannot run, stop and say the deterministic archive
helper is unavailable; do not fall back to manual moves.

Archive is a hub-level operation. The CLI resolves HUB with the standard
protocol (`hub_path`, legacy fallbacks, the macOS permission diagnostic) and
refuses an uninitialized hub. Pass `--hub <path>` only when the user gave one.
Archive options precede the subcommand:

```bash
<cli> archive [--hub <path>] [--private | --public] list [--archived] [--json]
<cli> archive [--hub <path>] [--private | --public] topic <slug> [--reason "why"]
<cli> archive [--hub <path>] [--private | --public] restore <slug>
```

`--private` resolves a public slug to its `<slug>-PRIVATE` sibling and
`--public` does the reverse. Quote CLI failures verbatim (already archived,
destination exists, private topic not ignored by version control, path outside
`topics/`) and let the user decide; never work around them with manual moves.

Do not archive project-local `.wiki/` directories in v1. Project archive remains
`/wiki:project archive`; inventory and dataset archive remain `status:
archived`.

### Parse $ARGUMENTS

Subcommands:

- **list**: `<cli> archive list --json`; add `--archived` to show archived
  topics in a separate table (slug, reason, archived date, path). The CLI also
  reports archived directories missing from the registry as repair candidates.
- **topic <slug>** (`--reason "why"`): `<cli> archive topic <slug> [--reason ...]`.
  The CLI refuses `hub`, moves the wiki to `HUB/topics/.archive/<slug>`, updates
  `wikis.json` atomically (`status`, `archived`, `archive_reason`), rewrites the
  hub `_index.md` and appends both logs.
- **restore <slug>**: `<cli> archive restore <slug>`. The CLI refuses an
  existing active destination, restores the registry entry (`status: active`,
  `restored`) and appends both logs. Afterwards stale-check the restored topic's
  master `_index.md` and suggest `/wiki:query --resume --wiki <slug>`.
- **peek <query>**: agent-only semantic pass, described below.

Flags handled by the agent:

- **--include-archived**: allow explicit archived reads for `peek` or when a
  routed query follows an archived match.

If the input is empty, show the subcommands and examples.

Never delete topic content. Never move individual `raw/` or `wiki/` files.

### Peek

For `peek <query>`:

1. Read only archived topic `_index.md` files and category indexes when cheap.
   Take the archived slugs from `<cli> archive list --archived --json`.
2. Match against titles, summaries, tags, and recent-change lines.
3. Return an `Archived Matches` section. Make clear that these matches are
   preserved context, not active evidence.
4. Suggest either:
   - `/wiki:archive restore <slug>` when the user wants to resume the topic, or
   - `/wiki:query "<question>" --wiki <slug> --include-archived` when they want
     an explicit archived lookup without restoring.

Do not read archived articles during `list` or `peek`.

### Tool Interaction Reminder

Archived topics are hidden from normal query, compile, ingest,
ingest-collection, research, collect, output, plan, assess, inventory, dataset, project,
lessons-learned, librarian, refresh, and audit workflows. Deep query may surface
archived index matches separately, but must not cite them as active evidence
unless the user passes `--include-archived`.

`lint --include-archived` and `lint --archived-only` are the maintenance paths
for archived structure. `retract` may operate on archived sources when
explicitly targeted.

### Report

Always include:

- Hub path
- Action taken
- Topic slug(s)
- Active/archived path changes, if any (the CLI prints old and new paths)
- Registry changes made
- Restore or follow-up command
