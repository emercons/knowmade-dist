---
description: "Keep the active wiki article layer in check: staleness, quality, accuracy, and coherence. Focused maintenance tool; broader trust audits belong to /knowmade:audit."
argument-hint: "scan [--article <path>] [--resume] [--passes <list>] [--include-archived] | report | fix <id> [--wiki <name|all>] [--local]"
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(ls:*), Bash(wc:*), Bash(date:*), Bash(mv:*), Bash(mkdir:*), Bash(python3:*), Bash(scripts/llm-wiki:*), Bash(${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki:*), WebFetch, WebSearch, Agent
---

## Your task

<!-- hub-prelude:start -->
**Resolve the wiki.** A few file reads, not a search: never `find`, Explore, or scan the filesystem. Full protocol and edge cases: `skills/knowmade/references/hub-resolution.md`.
1. HUB: `$LLM_WIKI_HUB` or read `$HOME/.config/llm-wiki/config.json` and use `hub_path` (expand only a leading `~`); `resolved_path` is a fallback cache; with no config use `$HOME/wiki`. If the hub is stat-able but reads fail with `Operation not permitted`, stop and report the launcher permission denial.
2. Wiki, first match: `--local` → `.wiki/`; `--wiki <name>` → `HUB/wikis.json` entry (`wikis.local.json` path first; stale path → `HUB/topics/<name>`); `.wiki/` in CWD; else HUB. Reject archived entries (`status: archived`, `topics/.archive/`) unless the command takes `--include-archived`.
3. `<slug>` and `<slug>-PRIVATE` are sibling topics in one hub: read private freely, write under the Write-Guard in `skills/knowmade/references/knowmade-profile.md` § Private Data Boundary.
<!-- hub-prelude:end -->
4. `--wiki all` (this command only) → HUB with an `ALL_TOPICS` flag for step 6, instead of one wiki.
5. Read `<wiki>/_index.md` to verify. If missing → stop with "No wiki found. Run `/knowmade:wiki init` first."
6. **Hub-level detection**: Librarian operates on the `wiki/` layer of a single topic wiki. The HUB has no `wiki/` subdirectory — only `wikis.json`, `_index.md`, `log.md`, and `topics/`. If `ALL_TOPICS` is set from `--wiki all`, read registered topic wikis from `HUB/wikis.json` (excluding the synthetic `hub` entry) and iterate the scan against each topic wiki sequentially. If the resolved wiki is the HUB (detected by the presence of `wikis.json` and the absence of a `wiki/` subdir) AND the user did not pass `--wiki <name>`, do NOT proceed with the scan against an empty topic surface. Instead, present a numbered list of registered topic wikis from `HUB/wikis.json` and ask the user to pick one. Re-resolve the wiki path with the selected name (`HUB/topics/<name>/`) and continue.

Read the librarian reference at `skills/knowmade/references/librarian.md`. Then execute the requested subcommand.

`/knowmade:librarian` is the focused wiki-maintenance tool. It reviews the `wiki/` layer only. If the user wants a broader trust inspection across outputs, provenance, and fresh research, direct them to `/knowmade:audit`.

Inventory awareness: librarian reports may recommend inventory records for
durable maintenance follow-ups such as stale article clusters, weak source
chains, recurring refresh tasks, or watch items. Do not create records during a
scan unless the user explicitly asks; include a compact sample shape when
suggesting a larger maintenance backlog.

### Parse $ARGUMENTS

First argument is the subcommand:

- **scan**: Score all wiki articles for staleness and quality. Default subcommand if none specified.
- **report**: Display the latest `.librarian/REPORT.md`. If no report exists, suggest running `scan` first.
- **fix <id>**: (Phase 3, not yet implemented) Apply a proposed fix. Respond: "Fix operations are not yet available. Use the report to identify issues and address them manually."

Flags (apply to `scan`):

- **--article <path>**: Scan only this article instead of the full wiki. Path relative to wiki root (e.g., `wiki/concepts/librarian-agent.md`).
- **--resume**: Explicitly resume from checkpoint. Also happens automatically if `checkpoint.json` exists.
- **--passes <list>**: Comma-separated list of passes to run. Default:
  `staleness,quality`. Optional: `schema` for a proposal-only topic schema
  migration pass. Future: `verification,coherence,dedup`.
- **--wiki <name|all>**: Target a specific topic wiki, or every registered topic wiki sequentially.
- **--local**: Use project-local `.wiki/`.
- **--include-archived**: Explicitly include archived topic wikis. Archived
  topics are skipped by default so stale old interests do not create
  maintenance chores.

When `--wiki all` is used, iterate active registered topic wikis only unless
`--include-archived` is present. If a single `--wiki <name>` target is archived,
ask for `--include-archived` or restoration before scanning. `-PRIVATE` topics
are iterated too; see `skills/knowmade/references/knowmade-profile.md` § Private Data Boundary: Read Freely, Write-Guard.

Write-guard: each topic's `.librarian/REPORT.md`, checkpoint, and any proposed
fix are written inside that topic only, so a public topic's report never
names a `-PRIVATE` path, private source, or private sibling finding; keep the
cross-topic summary for the chat response and mark private topics there.

### Scan Protocol

#### 0. Initialize

1. Create `.librarian/` directory if it doesn't exist (`mkdir -p`).
2. Check for existing `checkpoint.json`:
   - If exists and `--resume` or no explicit flag: read it, report how many articles are already done, continue from where it left off.
   - If exists and user started a fresh scan (no `--resume`): warn "Found checkpoint from [date] with N/M articles done. Resume? (y/n)". On "n", delete checkpoint and start fresh.
3. Read `config.md` for `freshness_threshold` (default: 70).
4. Build article list:
   - Full scan: `Glob wiki/**/*.md`, exclude `_index.md` files.
   - `--article`: just the one file.
5. Subtract already-completed articles (from checkpoint) to get the pending list.

#### 1. Staleness Pass (per article)

For each pending article:

1. Read the article's YAML frontmatter (do NOT read the full body yet — Tier 1 is metadata-only).
2. Read `volatility` (default: `warm`), `verified`, `updated`, `created`, `sources`, `confidence`.
3. Take its staleness score from one `<cli> freshness score --all --json` run (`${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki`, or `scripts/llm-wiki` in a checkout): `score`, `factors` and `unresolved_sources` per article, formula in `references/librarian.md` § Staleness Scoring. Do not recompute it by hand.
4. Write result to `checkpoint.json` (atomic: write `.checkpoint.tmp`, rename).

#### 2. Quality Pass (per article)

For each article (same loop, immediately after staleness):

**Tier 1 (all articles, metadata-only)**:

1. Count sources and read their `confidence:` fields → source quality score (1-5).
2. Get file size (`wc -w` or estimate from Read) and count `## ` headings → depth proxy (1-5).
3. Check for "See Also" section presence → flag `no-see-also` if missing.
4. Record Tier 1 quality scores in checkpoint.

**Tier 2 escalation** — read the full article body if ANY of these are true:
- Staleness score < threshold (from Pass 1)
- `volatility: hot`
- Tier 1 depth proxy = 1 or 2 (suspected stub)

When escalated:

5. Read the full article body.
6. Score coherence (1-5): Does it flow logically? Are there unsupported jumps?
7. Score utility (1-5): Would a reader find this actionable?
8. Refine depth and source quality scores with body-level evidence.
9. Apply quality flags per `references/librarian.md` § Quality Flags.
10. Update checkpoint with Tier 2 results.

**Non-escalated articles**: coherence and utility default to 3 (adequate). This avoids reading every article body on large wikis.

#### 3. Stale Article Triage

After all articles are scored:

1. Sort articles by staleness score (ascending — worst first).
2. Filter to those below the freshness threshold.
3. For each stale article, recommend an action:
   - `refresh` — sources are old, article may be outdated. Delegate to `/knowmade:refresh`.
   - `verify` — article lacks `verified:` date or it's been too long. User should read and confirm.
   - `expand` — thin article with few sources. Suggest `/knowmade:research` to find more sources.
4. Present the triage list to the user:

```
## Stale Articles — Action Required

1. [NVIDIA Spark Specs](wiki/topics/nvidia-spark.md) — score 31/100
   Sources 180 days old, never verified.
   → Refresh sources? (y/n/skip)

2. [CLI UX Patterns](wiki/concepts/cli-ux-patterns.md) — score 45/100
   Verified 120 days ago, warm volatility.
   → Verify still accurate? (y/n/skip)
```

5. For articles the user selects to refresh: invoke the refresh protocol in `skills/knowmade/references/refresh-protocol.md` for that article.
6. For articles the user selects to verify: update `verified:` to today in the article's frontmatter.

#### 3b. Topic-Guide Conventions Pass

Run only when `conventions` or legacy `schema` is included in `--passes`.

1. Check whether `<wiki-root>/schema.md` exists. If it exists, treat the schema
   as advisory unless it explicitly declares a stricter mode.
2. Derive observed topic vocabulary from existing markdown:
   - article titles, aliases, tags, and categories;
   - See Also links and markdown link neighborhoods;
   - raw source types, collection manifests, and source tags;
   - inventory kinds/entities/corpora and dataset manifests when present;
   - recurring output/project names and prefixes.
3. If no schema exists and the wiki is small (roughly fewer than 10 compiled
   articles) with no obvious repeated category/link/source confusion, report
   `topic guide recommended: default advisory starter` and suggest
   `llm-wiki schema adopt` or `llm-wiki lint --fix`. Do not write a
   proposal for tiny wikis unless the user asked for a topic-guide design.
4. If a schema would help, write
   `output/schema-proposal-<topic>-YYYY-MM-DD.md`. The proposal may recommend
   entity types, relationship verbs, article subtypes, source conventions, and
   inventory/dataset boundaries.
5. Do **not** create or update `schema.md` during the scan. Applying a proposal
   is a separate explicit user decision. The deterministic migration helper may
   create a default advisory `schema.md`, but the librarian scan itself must not.
6. Add schema findings to `scan-results.json` and `REPORT.md` under a
   `Schema Advice` section, including whether the state is `missing`,
   `proposed`, `advisory`, or `strict`.

#### 4. Generate Reports

1. Compile all results from checkpoint into `scan-results.json` (format per `references/librarian.md`).
2. Generate `REPORT.md` from the JSON (format per `references/librarian.md`).
3. Delete `checkpoint.json` (scan complete — no longer needed).
4. Write both files to `.librarian/`.

#### 5. Log and Report

1. Append to `.librarian/log.md`:
   `## [YYYY-MM-DD] scan | N articles, M stale, K low-quality (passes: staleness, quality)`

2. Append to the wiki's `log.md`:
   `## [YYYY-MM-DD] librarian | scanned N articles, M stale, K low-quality`

3. Present the summary to the user:

```
## Librarian Scan Complete

Scanned N articles in <wiki-name>.

| Metric | Value |
|--------|-------|
| Below staleness threshold | M |
| Low quality (< 50) | K |
| Average staleness | X/100 |
| Average quality | Y/100 |

Full report: .librarian/REPORT.md
Scan data: .librarian/scan-results.json
```

4. If stale articles were found and user hasn't triaged them yet, prompt for triage (step 3 above).

### Report Subcommand

When the user runs `report` (or `/knowmade:librarian report`):

1. Check if `.librarian/REPORT.md` exists. If not: "No librarian report found. Run `/knowmade:librarian scan` first."
2. Read and display `REPORT.md`.
3. Note when the scan was run (from `scan-results.json` → `completed_at`).
4. If the scan is older than the wiki's staleness threshold for hot articles (30 days), suggest re-scanning.
