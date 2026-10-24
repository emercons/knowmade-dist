# Project Setup Protocols

The rare branches of `/knowmade:project`, moved out of the command so they are
read only when taken: the manual fallbacks for `new`, `list` and `add` (when the
`llm-wiki project` CLI cannot run) and the archive-layer rule. Read the section
for the branch in hand, not the file top to bottom. The rationale for the model
itself (why `WHY.md` is the only required file, staleness, archive-as-move) is in
[`projects.md`](projects.md). All project paths are relative to the resolved
wiki root (`<wiki-root>/output/projects/<slug>/`).

## Archive layers

Topic archive and project archive are different layers. Project commands
operate on active topic wikis by default. If `--wiki <name>` resolves to a topic
under `topics/.archive/`, stop and ask the user to restore the topic or rerun
with `--include-archived`; explicit archived operations must stay inside that
archived topic path. The project subcommand `archive <slug>` only moves
`output/projects/<slug>` inside the selected topic wiki.

**--include-archived**: Explicitly allow the selected topic wiki itself to be
archived. This is separate from `list --archived`, which includes archived
projects inside an active topic wiki.

## Manual fallback: `new <slug> "goal"`

Only when `llm-wiki project new` cannot run. (The CLI validates the slug — exit 2
with a suggestion — refuses an existing project — exit 1 — writes `WHY.md`, adds
the `output/_index.md` entry, appends the `log.md` line, and refuses a public
wiki's goal that names a `-PRIVATE` path.)

**Validate slug**:
- Lowercase only
- Hyphen-separated
- Max 40 characters
- No spaces, no uppercase, no special chars except `-`
- If invalid, suggest a corrected version and exit: `Invalid slug "Foo Bar" — use "foo-bar" instead.`

**Validate goal**: Mandatory. If missing, prompt: `Goal is required. What is this project trying to accomplish?`

**Check collision**: If `<wiki-root>/output/projects/<slug>/` already exists (active or archived), fail: `Project "<slug>" already exists at <path>. Use a different slug.`

**Create**:
1. `mkdir -p <wiki-root>/output/projects/<slug>/`
2. Write `<wiki-root>/output/projects/<slug>/WHY.md` using the template below
3. Report: `Created project "<slug>" at <path>.`

**`WHY.md` template**:
```markdown
# <Title>

<goal>

## Context

<!-- Why this project exists, what triggered it, who cares. Delete this section if you don't need it. -->

## Current state

<!-- Where things stand, what's next, outstanding questions. Delete this section if you don't need it. -->
```

Derive the title from the slug: `bitcoin-quantum-risk` → `Bitcoin Quantum Risk`. Capitalize each hyphen-separated token. Drop the `<!-- ... -->` placeholders if the user gave a goal rich enough to stand alone.

**No frontmatter.** `WHY.md` is plain markdown. The convention is "first `#` heading = title, body = rationale." That's it. Don't add `type:`, `status:`, `created:`, `updated:` — filesystem state is the state.

## Manual fallback: `list [--archived]`

Only when `llm-wiki project list` cannot run. (The CLI returns title, goal,
member count, last update and the `legacy_manifest` and `stale` flags with
`--json`.)

1. Glob `<wiki-root>/output/projects/*/WHY.md` (active projects)
2. If `--archived` is set, also glob `<wiki-root>/output/projects/.archive/*/WHY.md`
3. For each, read the first `#` heading (the title) and the first non-heading paragraph (the goal, first 120 chars)
4. Count members: `ls <wiki-root>/output/projects/<slug>/` minus `WHY.md`, recursively counting files at max 3 levels
5. Render a table:

```
Active projects (N)

| Slug | Title | Goal | Members |
|------|-------|------|---------|
| bitcoin-quantum-risk | Bitcoin Quantum Risk | Ship a migration plan for Bitcoin's quantum transition | 4 |
| llm-wiki-roadmap | llm-wiki Roadmap | v0.1 features | 2 |
```

With `--archived`, render a second "Archived projects" table below.

If no projects exist, report: `No projects in <wiki>. Create one with /knowmade:project new <slug> "goal".`

## Manual fallback: `add <slug> <path>`

Only when `llm-wiki project add` cannot run. (The CLI moves files from `output/`
only — refusing anything else with exit 1 and nothing moved — drops their
`output/_index.md` rows, logs once and lists files that still link the old path;
it is idempotent.)

1. Verify the project exists: `<wiki-root>/output/projects/<slug>/WHY.md`. If not, fail: `Project "<slug>" does not exist. Create it first with /knowmade:project new <slug> "goal".`
2. Resolve the source path (absolute or relative to wiki root)
3. Verify the file exists
4. `mv <source> <wiki-root>/output/projects/<slug>/<basename>`
5. Report: `Added <basename> to project "<slug>".`

**Warning**: If the source file is referenced by other articles (grep for its old path in `wiki/` and `output/`), print those references and warn the user they may be broken. Do not auto-fix — the user decides whether to update the links manually or run lint.

**Frontmatter note**: This command does **not** add `project: <slug>` to the moved file's frontmatter. Folder position is authoritative; frontmatter duplication is a drift trap. If the user wants Obsidian-style project tagging for search, they can add it manually — it's not required.
