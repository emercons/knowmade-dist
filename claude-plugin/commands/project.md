---
description: "Manage projects inside a topic wiki. Projects are folders under output/projects/ that group related outputs (playbooks, images, code, data) with a goal captured in WHY.md."
argument-hint: "new <slug> \"goal\" | list [--archived] | show <slug> | add <slug> <path> | archive <slug> [--include-archived]"
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(ls:*), Bash(mkdir:*), Bash(mv:*), Bash(date:*), Bash(basename:*), Bash(find:*), Bash(wc:*), Bash(scripts/llm-wiki:*), Bash(${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki:*)
---

## Your task

Manage projects — folders inside a topic wiki's `output/projects/` directory that group related outputs. The only required file in a project is `WHY.md`, which captures the goal/rationale in plain markdown.

<!-- hub-prelude:start -->
**Resolve the wiki.** A few file reads, not a search: never `find`, Explore, or scan the filesystem. Full protocol and edge cases: `skills/knowmade/references/hub-resolution.md`.
1. HUB: `$LLM_WIKI_HUB` or read `$HOME/.config/llm-wiki/config.json` and use `hub_path` (expand only a leading `~`); `resolved_path` is a fallback cache; with no config use `$HOME/wiki`. If the hub is stat-able but reads fail with `Operation not permitted`, stop and report the launcher permission denial.
2. Wiki, first match: `--local` → `.wiki/`; `--wiki <name>` → `HUB/wikis.json` entry (`wikis.local.json` path first; stale path → `HUB/topics/<name>`); `.wiki/` in CWD; else HUB. Reject archived entries (`status: archived`, `topics/.archive/`) unless the command takes `--include-archived`.
3. `<slug>` and `<slug>-PRIVATE` are sibling topics in one hub: read private freely, write under the Write-Guard in `skills/knowmade/references/private-boundary.md`.
<!-- hub-prelude:end -->
4. Read `<wiki>/_index.md` if found. Variant: **wiki-neutral** — see deviation below for the step-4 fallback.

Deviation: step 4's fallback to HUB becomes **ask the user which topic wiki, or fail if no topic wikis exist** (an empty hub has nothing to operate on). All project paths below are relative to the resolved wiki root (`<wiki-root>/output/projects/<slug>/`).

### If --include-archived is given or the wiki resolves to an archived topic

Stop and ask the user to restore the topic or rerun with `--include-archived`; with the flag, stay inside that archived topic path. The layer rules (topic archive vs project archive, `list --archived`) are in `skills/knowmade/references/project-setup.md` § Archive layers.

### Parse $ARGUMENTS

The first word is the subcommand; the rest are its args.

| Subcommand | Args | Purpose |
|------------|------|---------|
| `new` | `<slug> "goal"` | Create a new project with a WHY.md |
| `list` | `[--archived]` | List projects (active by default) |
| `show` | `<slug>` | Show a project's WHY.md and member files |
| `add` | `<slug> <path>` | Move an existing file into a project |
| `archive` | `<slug>` | Move folder to `.archive/` (reversible via mv) |

If `$ARGUMENTS` is empty, show help (list subcommands with examples) and exit.

**Removed in v0.2**: `focus`, `unfocus`, `retract`, `rename` (pass `--project <slug>` explicitly instead of focus; rename and retract are rare and better done with `mv` and `rm -rf` than wrapped subcommands). The rationale is in the projects-model branch below.

### Subcommand: `new <slug> "goal"`

`llm-wiki project new <slug> --goal "<goal>" [--title <t>] [--dry-run] [--json]`. A slug is lowercase, hyphen-separated, at most 40 characters; the goal is mandatory. The CLI validates both, refuses an existing project (active or archived), writes `WHY.md` (plain markdown, no frontmatter), adds the `output/_index.md` entry, appends the `log.md` line, and refuses a public wiki's goal that names a `-PRIVATE` path.

### Subcommand: `list [--archived]`

`llm-wiki project list [--archived] --json` returns title, goal, member count, last update and the `legacy_manifest` and `stale` flags. Render a table (Slug, Title, Goal, Members), plus an "Archived projects" table with `--archived`.

### Subcommand: `show <slug>`

Show a project's goal and members.

1. Look for `<wiki-root>/output/projects/<slug>/WHY.md` (or `.archive/<slug>/WHY.md` if not in active)
2. If not found, fail: `Project "<slug>" not found.`
3. Print the full WHY.md
4. Below it, print a member list scanned at display time:
   - Glob the project folder (max 3 levels deep), excluding WHY.md, hidden files, `.DS_Store`
   - Sort: markdown first, then code, then binaries
   - Render as a relative-path list, one per line

No derived cache, no regeneration step — `show` always reflects current filesystem state.

### Subcommand: `add <slug> <path>`

`llm-wiki project add <slug> <path>... [--dry-run] [--json]` moves files from `output/` (refusing anything else, exit 1, nothing moved), drops their `output/_index.md` rows, logs once and lists files that still link the old path. Idempotent. It adds no `project:` frontmatter: folder position is authoritative. Print the listed links as a warning; the user decides whether to fix them or run lint.

### Subcommand: `archive <slug>`

Move a project to `.archive/`.

1. Verify the project exists: `<wiki-root>/output/projects/<slug>/WHY.md`
2. `mkdir -p <wiki-root>/output/projects/.archive/`
3. `mv <wiki-root>/output/projects/<slug> <wiki-root>/output/projects/.archive/<slug>`
4. Report: `Archived "<slug>". Folder moved to output/projects/.archive/<slug>. Reverse with: mv output/projects/.archive/<slug> output/projects/<slug>.`

This is the entire archive operation. No frontmatter updates, no status flags, no linked-file cleanup. Filesystem state is the state.

**Broken links**: files elsewhere that linked to the archived project now have broken links; lint check C4 catches them on the next run (an intentional tradeoff; see the projects-model branch).

### If the CLI cannot run (`new`, `list` or `add`)

Do the manual steps of `skills/knowmade/references/project-setup.md` § Manual fallback for the subcommand in hand (slug and goal validation, collision check, `WHY.md` template, member listing, move and link warning).

### If the user turns an Idea into a Project ("approve the brief", or equivalent)

Route to the Idea promotion workflow in `skills/knowmade/references/ideas.md` (§ Promotion): it freezes `BRIEF.md` and cross-links the records. `project new` stays for direct Projects that do not originate as Ideas.

### If the user asks for a project backlog, source queue, watch list or task list

Suggest inventory records or a saved inventory view linked from the project (`/knowmade:inventory`) instead of putting tracking state in `WHY.md`: projects group outputs around a goal; inventory tracks durable items with status, priority and next action.

### If the request asks why projects work as they do, or proposes changing the model

Read the projects architecture at `skills/knowmade/references/projects.md`: why `WHY.md` is the only required file (it holds the precious, non-derivable rationale), why everything else is derived from filesystem state, and § Focus for why focus was dropped.

## Report

After any mutation, output:

- **Action taken**: created/listed/shown/added/archived
- **Project(s) affected**: slug and path
- **Members delta** (for `add`): `+1 member` or equivalent
- **Next steps**: suggestions like `/knowmade:research <topic> --project <slug>` or `/knowmade:project show <slug>`
