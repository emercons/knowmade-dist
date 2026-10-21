---
description: "Compile raw sources into wiki articles. Synthesizes, cross-references, and organizes active knowledge."
argument-hint: "[--full] [--source <path>] [--topic <article-name>] [--include-archived] [--wiki <name>] [--local]"
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(ls:*), Bash(wc:*), Bash(date:*), Bash(mv:*), Bash(mkdir:*), Bash(scripts/llm-wiki:*), Bash(${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki:*)
---

## Your task

<!-- hub-prelude:start -->
**Resolve the wiki.** A few file reads, not a search: never `find`, Explore, or scan the filesystem. Full protocol and edge cases: `skills/knowmade/references/hub-resolution.md`.
1. HUB: `$LLM_WIKI_HUB` or read `$HOME/.config/llm-wiki/config.json` and use `hub_path` (expand only a leading `~`); `resolved_path` is a fallback cache; with no config use `$HOME/wiki`. If the hub is stat-able but reads fail with `Operation not permitted`, stop and report the launcher permission denial.
2. Wiki, first match: `--local` → `.wiki/`; `--wiki <name>` → `HUB/wikis.json` entry (`wikis.local.json` path first; stale path → `HUB/topics/<name>`); `.wiki/` in CWD; else HUB. Reject archived entries (`status: archived`, `topics/.archive/`) unless the command takes `--include-archived`.
3. `<slug>` and `<slug>-PRIVATE` are sibling topics in one hub: read private freely, write under the Write-Guard in `skills/knowmade/references/knowmade-profile.md` § Private Data Boundary.
<!-- hub-prelude:end -->
4. Read `<wiki>/_index.md` to verify. If missing → stop with "No wiki found. Run `/knowmade:wiki init` first."

Read the compilation protocol at `skills/knowmade/references/compilation.md` and the indexing protocol at `skills/knowmade/references/indexing.md`. Then compile raw sources into wiki articles.

Inventory awareness: compile consumes `raw/` and writes `wiki/`; it does not
compile inventory records into articles. Read `inventory/_index.md` only to
notice active candidates, blocked source queues, or next actions that explain
why sources were ingested. If compilation completes work represented by an
inventory record, report the recommended record update instead of silently
changing operational state.

Archive awareness: compile targets active topic wikis only by default. If the
resolved `--wiki <name>` path is archived (`wikis.json` status `archived` or
path under `topics/.archive/`), stop and ask the user to restore the topic or
rerun with `--include-archived`. When `--include-archived` is present, compile
only inside that archived topic path and keep it archived; do not use archived
raw sources to update active articles.

### Parse $ARGUMENTS

- **--full**: Recompile everything from scratch
- **--source <path>**: Compile only this specific source file
- **--topic <article-name>**: Create or update one specific topic *article* under `wiki/topics/`. This names an article, not a topic wiki; choose the wiki with `--wiki`
- **--include-archived**: Explicitly compile an archived target wiki. Never
  makes the wiki active.

### Compilation Process


0. **Topic-guide preflight**: Follow `references/compilation.md` § Topic-guide preflight. In short, read `schema.md` when present and use its cardinality, article-boundary, scope, and keep-separate guidance while planning. Advisory guidance is a planning preference: explain a deliberate deviation in the compile report. For `schema_state: strict`, stop before writing a planned deviation and request confirmation. Topic guidance never changes global categories/frontmatter, never rewrites existing articles merely to conform, and never exempts a raw source from C6 coverage.

1. **Survey and placement**: Run `<cli> compile plan --json` (`<cli>` is `"${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki"`, `scripts/llm-wiki` in a source checkout) with the target of this compile (`--wiki <slug>`, `--local` or the wiki path), `--include-archived` when you got it, and `--full` or `--source <path>` per $ARGUMENTS. It is read-only and returns every raw source with its status, `to_compile`, `unreadable_sources`, the existing articles (path, title, category, tags, summary: your map-to-existing input), `placement.issues` and the topic guide. If it exits 2, report its message and stop.

   **Placement** (C11, C12, C13 from `references/linting.md`): if `placement.issues` is non-empty, apply `placement.fix` (preview with `placement.preview`; `fix_argv` is the same without a shell, prefer it), which is `<cli> lint <root> --fix --fix-only placement` and moves misplaced files in `raw/`, `wiki/` and `inventory/`; fix C13 alias issues by hand from the C13 table; then re-run the plan so it sees canonical state. A file loose at the wiki root (C12) is quarantined by lint to `inbox/.unknown/` (`--fix-only quarantine`) and goes through ingest triage, not compile.

   Only if the CLI is unavailable (not when the plan errors): read `raw/_index.md` and `wiki/_index.md` and take the sources ingested after the "Last compiled" date in master `_index.md`; walk `raw/` and for each `.md` file read its frontmatter, rewrite legacy keys/values using the C13 alias table, then `mv` a file whose `type` does not match its directory (or sits loose at the wiki root) to the canonical path, creating the destination directory if needed. On slug collisions at the destination, skip and warn. Does not touch `output/projects/` (compile step 7's territory).

2. **Report what the plan could not plan**: if `unreadable_sources` is non-empty, report "N raw files have no readable frontmatter (lint C1) and must be fixed before they can be compiled" with the paths; they are never compiled and never "already compiled". If `last_compiled` is null in incremental mode, say that only uncompiled sources were planned because no "Last compiled" date was found. If `to_compile` is empty (incremental mode) and nothing is unreadable, report: "All sources are already compiled. Use `--full` to recompile everything."

3. **Read sources**: Read each `to_compile` source in full. Extract key concepts, facts, relationships.

4. **Plan**: For each concept found:
   - Check existing wiki articles (via `wiki/_index.md` summaries and tags)
   - Decide: create new article, update existing, or mention within another article
   - Classify new articles as concept, topic, or reference

5. **Write/Update articles**: Follow the protocol in `references/compilation.md` and core principle #9 (chunked writes):
   - New articles: Write frontmatter + abstract first, then Edit to append body, See Also, Sources
   - Updated articles: use Edit to integrate new information, update frontmatter dates
   - Every article must link to at least one other via See Also

5.4. **Write-guard (public topics)**: When the wiki being compiled is a public, non-`-PRIVATE` topic, apply `skills/knowmade/references/knowmade-profile.md` § Private Data Boundary: Read Freely, Write-Guard to every article touched in step 5: no fact or citation traced to a `-PRIVATE` sibling, `goals.md`, or private inventory enters the article; `sources:` and See Also links resolve inside this topic; the file contains no `-PRIVATE` path string. Material that fails moves to the private sibling or is dropped, and if private context shaped the compile, say so in the Report and log it in the private sibling's `log.md`, not here.

5.5. **Self-validation pass**: For every article touched in step 5 (new or updated), re-read the frontmatter and verify:
   - `sources:` is a non-empty list resolving to existing raw files, OR `compiled-from: conversation` is set
   - `volatility:` is set to `hot`, `warm`, or `cold`
   - `verified:` is set to today's date
   - `confidence:` is set to `high`, `medium`, or `low`

   If any check fails, halt with: `Article <path>: missing required frontmatter (<field>). See references/compilation.md § Step 5 (write protocol) and references/wiki-articles.md § Volatility Classification.` This catches silent agent skips before lint has to catch them later. Do not "fix and continue" — stop, surface the failure, let the user re-run with awareness.

6. **Bidirectional links**: For every See Also link A→B, ensure B→A exists

7. **Update indexes (best-effort)**: Run `<cli> index rebuild --wiki <slug>` first (<cli> is `"${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki"` (`scripts/llm-wiki` in a source checkout)); it brings every directory index up to date, so only the master `_index.md` (article count, the `- Last compiled: YYYY-MM-DD` line `compile plan` reads, Recent Changes) is left by hand. Only if the CLI is unavailable, update `wiki/concepts/_index.md`, `wiki/topics/_index.md`, `wiki/references/_index.md`, `wiki/_index.md`, and master `_index.md` (article count, "Last compiled" date, Recent Changes). If `output/projects/` exists, rebuild `output/_index.md` as a projects-aware listing: scan each `output/projects/*/WHY.md` for its first `#` heading (title) and first non-heading paragraph (goal), list them as a table, then list any remaining loose outputs in `output/` below. If any index update is skipped or interrupted, no data is lost — the next read operation will detect the stale index and rebuild it from file frontmatter. See `references/indexing.md` Derived Index Protocol and `references/compilation.md` Step 7.

8. **Log**: `<cli> log append --wiki <slug> --kind compile "N sources → X new articles, Y updated (list slugs)"` (by hand, only if the CLI is unavailable: append to `log.md`: `## [YYYY-MM-DD] compile | N sources → X new articles, Y updated (list slugs)`)

9. **Report**:
   - Sources processed: N
   - New articles created: list with paths
   - Existing articles updated: list with paths
   - New cross-references added: count
   - Volatility set: count of `hot`, `warm`, `cold` articles touched. If any are `hot`, append a one-line note pointing at `references/wiki-articles.md § Volatility Classification` so the author can confirm the rubric matched their intent.
   - Suggest: `/knowmade:lint` to verify consistency
