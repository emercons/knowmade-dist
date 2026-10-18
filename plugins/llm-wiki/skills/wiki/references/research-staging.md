# Research Staging (Phase 3)

How research stays inbox-first: agents stage captures, one coordinator processes the batch. Read at `research-protocol.md` § Phase 3, and by any agent that stages a capture.

### Why

Knowmade is inbox-first: `inbox/` is the intake boundary and `raw/` is the processed evidence layer (`knowmade-profile.md` § Inbox-First Source Lifecycle, `ingestion.md` § Inbox Processing). Research is the one workflow that captures many sources at once from many agents, which is exactly where a direct-to-`raw/` shortcut is most tempting and most damaging: unreviewed agent output would land in the immutable corpus with no intake trace. So research splits the ingest protocol into two roles — **agents stage**, **the coordinator processes** — and otherwise follows `wiki ingest` to the letter.

### Roles

| Role | Who | Writes | Never writes |
|------|-----|--------|--------------|
| Stager | each search agent (Phase 2) | `inbox/<capture>.md` | `raw/`, `_index.md` |
| Coordinator | the research command, or the path-agent in `--plan` mode | `raw/<type>/`, `inbox/.processed/`, indexes | — |

Parallel staging is safe because every capture name is unique per agent and source. Processing is a single sequential pass per coordinator, which is what makes the credibility review and the `--sources` cut meaningful.

### Capture Naming

```
inbox/YYYY-MM-DD-research-<agent-slug>-<source-slug>.md        # single-path research
inbox/YYYY-MM-DD-p<N>-<agent-slug>-<source-slug>.md            # --plan, path N
```

`<agent-slug>` is the agent role (`academic`, `contrarian`, `supporting`, `q2`, `rabbit-1`). `<source-slug>` follows `ingestion.md` § Slug Generation. The raw artifact drops the research/agent prefix (`raw/<type>/YYYY-MM-DD[-p<N>]-<source-slug>.md`) and keeps the plan path prefix, matching `research-plan.md` § File Ownership.

### Capture Schema

```yaml
---
title: "<page title>"
source: "<URL>"
research_query: "<topic | sub-question | thesis claim>"
research_mode: topic|question|thesis
research_agent: "<agent-slug>"
research_session: "<session id, when --min-time>"
research_round: 1
agent_quality: 4
key_findings:
  - "<finding>"
  - "<finding>"
thesis_direction: supports|opposes|nuances   # thesis mode only
evidence_strength: meta-analysis|rct|cohort|case|opinion|anecdotal   # thesis mode only
# added by the coordinator in Phase 2b:
credibility_score: 4
credibility_tier: high|medium|low|reject
# added by the coordinator when a capture is retired without a raw artifact:
ingest_status: skipped
skip_reason: "<reject-tier | duplicate | already-ingested | below-sources-cap>"
---
<content as fetched — not cleaned, not summarized>
```

The capture is the intake trace. The coordinator, not the stager, produces the summary, tags, and type.

### Processing Steps (per capture, coordinator)

1. Phase 2b scores the capture and writes `credibility_score`/`credibility_tier` into it.
2. Selected captures are processed exactly like an inbox item in `wiki ingest --inbox`: read, detect `type`, extract, summarize, tag.
3. Write `raw/<type>/...` with the standard raw frontmatter plus `inbox_item`, `research_query`, `research_agent`, `research_session` (when present), `credibility_score`, `credibility_tier`. These keys are listed as canonical optional raw-source keys in `linting.md` C13.
4. Move the capture to `inbox/.processed/`. Skipped captures get `ingest_status`/`skip_reason` and move there too; a credible capture cut only by `--sources` may stay pending in `inbox/` and is reported.
5. Update indexes best-effort (skipped in `--plan` mode; the Derived Index Protocol rebuilds them).

A raw artifact without a matching `inbox/.processed/` capture is a protocol violation, not a shortcut. If an agent returned a source it failed to stage, stage it first.

### Interaction With Other Workflows

- Pending research captures are ordinary inbox items: `wiki ingest --inbox` can process them, and the next `--min-time` round sees them in Phase 1.
- `collect --ingest-sources` and Idea development that "reuses normal research" inherit this staging because they call the research/ingest pipeline; they do not get a direct `raw/` path either.
- `HUB/inbox/` is not involved: research always targets one resolved topic wiki, so captures go to that topic's `inbox/`.
