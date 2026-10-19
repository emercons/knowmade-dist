---
description: "Run health checks on the wiki through the deterministic linter. Find broken links, missing indexes, stale content, archive registry drift, inconsistencies, and suggest improvements."
argument-hint: "[--fix] [--deep] [--include-archived] [--archived-only] [--private|--public] [--fail-on critical|warning|suggestion] [--strict-engine] [--wiki <name>] [--local]"
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(ls:*), Bash(wc:*), Bash(date:*), Bash(python3:*), Bash(scripts/llm-wiki:*), Bash(${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki:*), WebSearch
---

## Your task

Lint is CLI-first: `llm-wiki lint --json` runs every structural, schema, link,
provenance and registry check. Work from its JSON report and spend judgment
only on the semantic passes. Never re-implement a CLI check by hand.
Read `skills/knowmade/references/linting.md` for the rules table, fix
review and report format; `<cli> lint --help` has the exact flags.

### 1. Locate the CLI

`"${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki"` in Claude Code, `scripts/llm-wiki` in a
source checkout. Probe once with `<cli> lint --help`. If it cannot run, stop:
"The deterministic linter is unavailable, so lint cannot run. Install or repair
the plugin's `bin/llm-wiki` and retry." Never fall back to checking by hand.

### 2. Scope

Pass `--local`, `--wiki <name>`, a wiki-root path, `--private`/`--public`,
`--hub <path>`, `--include-archived`, `--fail-on <level>` (default
`suggestion`) and `--strict-engine` straight through; the CLI resolves the wiki.

A hub root is linted as a hub and never recurses into topics. To lint topics,
list them with `<cli> archive list --json` and run one lint per active slug
with `--wiki <slug>`; report `Archived topics: N skipped`. `--include-archived`
adds the archived slugs (`--wiki <slug> --include-archived`); `--archived-only`
(a command option, not a CLI flag) lints only those. Archived topics get
structural checks only. `<cli> lint --all --json` lints every hub root.

### 3. Run

```bash
<cli> lint --json [scope flags]
```

Non-zero with JSON means findings met `--fail-on`: read the report. Non-zero
without JSON is a failure: show the CLI message and stop ("No wiki found. Run
`/knowmade:wiki init` first." when no wiki resolves). Match findings by `check` and
`path`, never by message; group them, never paraphrase them away.

### 4. `--fix`

1. `<cli> lint --preflight --json [scope]`; review `plan[]` per `linting.md`
   § Fix review. Leave out archived scopes the user did not target.
2. `<cli> lint --fix --json [scope]`. It applies unambiguous fixes and logs.
3. Report `fixes[]` and the residual `issues[]`; do not hand-edit what the CLI
   left, those are judgment items.

### 5. Semantic passes

Read-only unless `--fix` is set and `linting.md` says the pass is fixable. A
message ending in `(kind)` is a CLI candidate: judge it, never recompute it.

- **Coverage**: C6 sources, `orphan-article`, `see-also-one-way`: compile,
  link, or backlog.
- **Freshness**: rank C14 findings with `<cli> freshness score --json`.
- **Projects**: `project-legacy-manifest`: `<cli> project migrate-why --json`,
  `--apply` only with `--fix`. `project-stale`, `project-candidate`,
  `output-loose-*`: recommend, never move.
- **Migration candidates**: judge fit, suggest the `migrate-output --dry-run`.
- **`tag-near-duplicate`**: propose the canonical tag; semantic variants too.
- **Rows still marked agent** (C7, C13), only for layers that exist.
- **`--deep`**: WebSearch spot-checks of key claims and newer information.

### 6. Report

Use `linting.md` § Report. Lead every line with plain English, never a check
code (good: "Migrate 3 legacy project manifests"; bad: "C8c — migrate 3").
Update the master `_index.md` "Last lint" date and any `index-stats-drift`
statistic. Without `--fix`, append to
`log.md`: `## [YYYY-MM-DD] lint | N critical, N warnings, N suggestions, N auto-fixed`.
