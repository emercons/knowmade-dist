---
description: "Run health checks on the wiki through the deterministic linter. Find broken links, missing indexes, stale content, archive registry drift, inconsistencies, and suggest improvements."
argument-hint: "[--fix] [--deep] [--include-archived] [--archived-only] [--private|--public] [--fail-on critical|warning|suggestion] [--strict-engine] [--wiki <name>] [--local]"
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(ls:*), Bash(wc:*), Bash(date:*), Bash(python3:*), Bash(scripts/llm-wiki:*), Bash(${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki:*), WebSearch
---

## Your task

Lint is CLI-first. `llm-wiki lint` performs the structural, schema, link,
provenance and registry checks deterministically and in milliseconds; this
command runs it, works from its JSON report, and spends agent effort only on
the semantic passes the CLI cannot judge. Never re-implement a CLI check by
hand. Read `skills/wiki-manager/references/linting.md` for the rules table and
the semantic-pass guidance, and `skills/wiki-manager/references/cli.md` for
exact flags.

### 1. Locate the CLI

Use `"${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki"` in Claude Code, or `scripts/llm-wiki`
in a source checkout. Probe it once with `<cli> lint --help`. If it cannot run
(missing file, no `python3`), stop and say so: "The deterministic linter is
unavailable, so lint cannot run. Install or repair the plugin's `bin/llm-wiki`
and retry." Do not fall back to checking files by hand.

### 2. Choose the scope

Pass the user's scope flags straight through; the CLI applies the normal hub
rules (`--local` → `.wiki/`, `--wiki <name>` → registry, otherwise `.wiki/` in
CWD, otherwise the hub).

| Argument | CLI |
|---|---|
| `--local` / `--wiki <name>` / a wiki-root path | same flag or positional |
| `--private` / `--public` | same flag (switch to the `-PRIVATE` sibling or back) |
| `--hub <path>` | same flag |
| `--include-archived` | same flag, only for an explicitly targeted archived wiki |
| `--fail-on <level>` | same flag; sets the lowest severity that exits non-zero (default `suggestion`) |
| `--strict-engine` | same flag; a declared/running engine mismatch becomes critical |

A hub root is linted as a hub (registry, private-ignore rules, specialist
library, archive container) and never recurses into topics. To lint the topics,
list them with `<cli> archive list --json` and run one lint per slug with
`--wiki <slug>`. By default lint only active topics and report
`Archived topics: N skipped`. With `--include-archived` add the archived slugs
(`--wiki <slug> --include-archived`). `--archived-only` is a command option,
not a CLI flag: lint only the archived slugs, the same way. Archived topics get structural checks only; skip freshness and
coverage pressure unless the user asks for a full archived maintenance pass.
`<cli> lint --all --json` lints every root of the hub, read-only.

### 3. Run it

```bash
<cli> lint --json [scope flags] [--fail-on <level>] [--strict-engine]
```

A non-zero exit with a JSON report only means findings met `--fail-on`; read
the report. A non-zero exit without JSON (unresolved wiki, archived target,
usage error) is a failure: show the CLI message and stop. If no wiki is found,
reply "No wiki found. Run `/wiki init` first."

The report has `status`, `counts`, `fixes`, and `issues[]` with `check` (the
rules-table id; match by it, not by message), `severity`, `message`, `path`,
`fixable`. Do not paraphrase away findings; group them for the user.

### 4. If `--fix`

1. Dry run: `<cli> lint --preflight --json`. Review its `plan[]` per
   `references/linting.md` § Fix review, § Fix classes. If a proposed fix
   would touch an archived topic the user did not target, leave that scope out
   of the `--fix` run.
2. Run `<cli> lint --fix --json [same scope flags]`. The CLI applies only
   unambiguous fixes and appends its own `log.md` entry.
3. Report `fixes[]` and the residual `issues[]`. Do not hand-edit what the CLI
   chose not to fix; those are judgment items.

### 5. Semantic passes (agent work)

After the CLI report, run only the passes that need judgment, on the active
wiki, read-only unless `--fix` is set and `references/linting.md` says the pass
is fixable:

- **Coverage judgement**: for each raw source or article the CLI flags as
  uncovered or orphaned, decide whether it needs compilation, a `See Also`
  link, or a coverage backlog entry.
- **Freshness narrative**: turn the CLI's staleness findings into a ranked list
  with the composite score for flagged articles.
- **Passes the CLI does not implement yet**: the rows marked `agent` in the
  rules table (legacy `_project.md` migration, project and inventory/dataset
  candidates, bidirectional `See Also`, index statistics, semantic tag
  near-duplicates). Run each only for the layers that exist.
- **`--deep`**: WebSearch spot-checks of key claims and newer information.

### 6. Report

Present the report in the format of `references/linting.md` § Report. **Lead
every user-visible line with a plain-English description of what happened,
never with a check code.** Describe recommended fixes in human terms:

- Good: `Migrate 3 legacy project manifests (_project.md → WHY.md)`
- Bad: `C8c — migrate 3 _project.md → WHY.md`

Update the master `_index.md` "Last lint" date. Without `--fix`, append to
`log.md`: `## [YYYY-MM-DD] lint | N critical, N warnings, N suggestions, N auto-fixed`
(with `--fix` the CLI already appended its entry).
