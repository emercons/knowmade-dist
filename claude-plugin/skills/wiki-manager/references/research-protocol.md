# Research Protocol

The base research path, moved verbatim out of `commands/research.md` so the command
file stays small: pre-flight awareness and specialist selection, then the single-round
topic-mode protocol (Phases 1-5, the agent prompt template, the gap-closing offer).
Read it after the target wiki is resolved. Modes and multi-round research live in
[`research-modes.md`](research-modes.md); templates, scoring and session schemas in
[`research-infrastructure.md`](research-infrastructure.md).

## Pre-flight (before Phase 1)

### Specialist selection

After resolving the target and before decomposing the question, read
`skills/wiki-manager/references/specialists.md`. If `--no-specialists` is set,
record an empty specialist list and continue without reading `HUB/.skills/`.

For a hub topic, read `HUB/.skills/_index.md` when present, then run:

```bash
"$LLM_WIKI" specialist list \
  --wiki <topic-slug> --json
```

`LLM_WIKI` is the bundled CLI, located as in [`adapters.md`](adapters.md) §
Locate the bundled CLI (`scripts/llm-wiki` in a source checkout). With explicit `--specialist`
flags, require every name in the returned allowlist and validate each package;
stop rather than silently substituting. Without explicit flags, classify the
task by domain, decision type, risk, jurisdiction, freshness, and evidence
needs, then choose zero to three allowlisted specialists (normally one). Run
`specialist validate <name>` for every selected method and stop on any finding.
Do not load unselected package bodies.

For project-local `.wiki/`, make no implicit selection. An explicit specialist
may be loaded from the resolved hub after `specialist validate <name>` because
the user selected it for this run; do not persist a local allowlist.

Give selected specialists the same bounded packet: exact question, intended
use, as-of date, jurisdiction when relevant, selected wiki/raw evidence, known
missing inputs, and output schema. Apply a single method inline by default.
Use isolated agents only for genuinely independent or cross-domain judgments.
Loading a specialist never expands tools, writes, network access, or subagent
authority.

Synthesize by claim and evidence strength, preserving disagreement and
unknowns. Verify citations, dates, and stop rules. Record each selected name,
version, and SHA-256 in session state, completion events, and the final
playbook/report provenance. High-stakes methods require current authoritative
primary sources and named qualified-human review.

### Research Protocol (Single Round — Topic Mode)

#### Phase 1: Existing Knowledge Check

1. Read `wiki/_index.md` and `raw/_index.md` to understand what the wiki already knows
2. Use Grep to search for the topic and related terms across existing articles
3. Identify gaps — what specific aspects, subtopics, and questions are NOT covered?
4. Generate a list of 5-8 specific search angles based on the gaps

#### Phase 2: Web Research — Parallel Agent Swarm

Launch agents IN PARALLEL (single message, multiple Agent tool calls) to maximize coverage and speed.

**Standard mode (default):** 5 parallel agents

| Agent | Focus | Search Strategy |
|-------|-------|----------------|
| **Academic** | Peer-reviewed papers, meta-analyses, systematic reviews | Search Google Scholar, PubMed, arxiv. Prioritize recent (last 2 years). Look for landmark papers. |
| **Technical** | Technical deep-dives, specifications, documentation | Search for technical guides, whitepapers, official documentation, engineering blogs. |
| **Applied** | Case studies, real-world implementations, practical guides | Search for how-to guides, industry reports, practitioner perspectives, tutorials. |
| **News/Trends** | Recent developments, announcements, emerging research | Search for news from last 6 months, conference talks, announcements, trend analyses. |
| **Contrarian** | Criticisms, limitations, counterarguments, failed approaches | Search for critiques, rebuttals, known limitations, what doesn't work, common mistakes. |

**Deep mode (`--deep`):** 8 parallel agents — adds:

| Agent | Focus | Search Strategy |
|-------|-------|----------------|
| **Historical** | Origin, evolution, key milestones of the topic | Search for history, foundational papers, how the field evolved, key figures. |
| **Adjacent** | Related fields, interdisciplinary connections | Search for cross-domain applications, analogies from other fields, unexpected connections. |
| **Data/Stats** | Quantitative data, benchmarks, statistics, datasets | Search for surveys, benchmarks, statistical analyses, market data, datasets. |

**Retardmax mode (`--retardmax`):** 10 parallel agents — ALL of the above plus:

| Agent | Focus | Search Strategy |
|-------|-------|----------------|
| **Rabbit Hole 1** | Whatever the first search surfaces — follow the most interesting link | Start with the topic, click the most compelling result, then search for what THAT references. Go deep. |
| **Rabbit Hole 2** | Same strategy, different starting search terms | Use synonyms or adjacent framing of the topic. Follow the trail wherever it goes. |

**Each agent must:**
- Run 2-3 different WebSearch queries (vary terms, don't repeat)
- For each promising result, use WebFetch to extract the full content
- Evaluate quality: Is this authoritative? Peer-reviewed? Recent? Unique perspective?
- Stage every source it returns as an `inbox/` capture (Phase 3 § Stage) — agents never write to `raw/`
- Return a ranked list: title, URL, inbox capture path, quality score (1-5), and why it's worth ingesting
- Target 3-5 high-quality sources per agent
- Skip: paywalled content, SEO spam, thin articles, duplicate information

Retardmax differences:
- **Skip Phase 1 entirely** — don't check what already exists, just go
- Each agent runs 4-5 searches instead of 2-3
- **Lower quality threshold** — ingest anything that's not obviously spam
- **--sources default bumps to 15** (override with explicit --sources)
- Compile fast — don't agonize over article structure
- Rabbit hole agents chase citations and references from pages they find

**Deduplication:** After all agents return, deduplicate by URL and by content similarity. If two agents found the same source, keep it once. If two sources cover identical ground, keep the higher quality one. In retardmax mode, be more lenient.

#### Agent Prompt Template

Every research agent receives a structured prompt following this template. Well-structured prompts are the #1 predictor of agent success — "most sub-agent failures aren't execution failures, they're invocation failures."

**Standard template (all modes)**:
```
You are a research agent. Your task:

**Objective**: Research "{topic}" from the {Agent Role} angle.
**Focus**: {Role-specific focus from the table above}
**Search strategy**: {Strategy from the table above}
**Current wiki state**: The wiki already covers: {brief summary from Phase 1}. Search for what's NOT covered.
**Constraints**:
- Run 2-3 WebSearch queries (vary terms, don't repeat)
- For each promising result, use WebFetch to extract full content
- Skip: paywalled, SEO spam, thin, duplicate
- Target 3-5 high-quality sources
- Stage each source you keep as an inbox capture at `inbox/{capture-name}` using the research capture schema (`references/research-infrastructure.md` § Inbox Staging): frontmatter title, source, research_query, research_agent, agent_quality, key findings, then the fetched content. Do NOT write anything under `raw/` — the coordinator processes the batch.

**Return format**: For each source found:
- Title, URL, inbox capture path, quality score (1-5)
- Key findings (3-5 bullet points)
- Why it's worth ingesting (1 sentence)

**Quality scoring guide**:
- 5: Peer-reviewed, landmark paper, primary data
- 4: Authoritative blog, official docs, well-sourced industry report
- 3: Decent coverage, some original insight, reasonable sourcing
- 2: Thin, mostly derivative, limited sourcing
- 1: SEO spam, no original content, unverifiable claims
```

**Question mode variant**: Replace "from the {Agent Role} angle" with "answering: {specific sub-question}". Add: "Your deliverable is evidence that answers this specific sub-question."

**Thesis mode variant**: Add thesis filter:
```
**Thesis**: "{thesis statement}"
**Key variables**: {extracted variables}
**Your lens**: {Agent Focus from thesis table}
**Evaluation**: For each source, rate:
- Relevance: direct | indirect | tangential (skip tangential)
- Evidence strength: meta-analysis > RCT > cohort > case study > expert opinion > anecdotal
- Direction: supports | opposes | nuances the thesis
```

#### Phase 2b: Credibility Review

After all agents return and before ingestion, run a credibility assessment. This prevents the "fox guarding the henhouse" problem where agents self-rate their own source quality. The review reads the staged `inbox/` captures (not only the agents' summaries) and records `credibility_score` and `credibility_tier` in each capture's frontmatter, so the decision travels with the item into `raw/`.

**For each source returned by agents, evaluate**:

| Signal | Scoring |
|--------|---------|
| Peer-reviewed? (DOI, journal name, conference) | +2 if yes, 0 if no |
| Publication recency (last 3 years) | +1 if recent, 0 if older, -1 if >10 years |
| Author authority (known expert, cited elsewhere) | +1 if established, 0 if unknown |
| Potential bias (industry-sponsored, activist org) | -1 if detected, 0 if clean |
| Corroboration (multiple agents found similar claims) | +1 per additional agent, max +2 |

**Credibility tiers**:
- **High** (4-6 points): Peer-reviewed, recent, authoritative, unbiased → ingest with confidence: high
- **Medium** (2-3 points): Published but not peer-reviewed, or older, or unknown author → ingest with confidence: medium
- **Low** (0-1 points): Blog, press release, unverifiable, biased → ingest only if no better source covers this angle; set confidence: low
- **Reject** (<0 points): Obvious spam, predatory journal, fabricated data → skip entirely

**Process**:
1. Score all sources from all agents
2. Deduplicate (same URL = keep once; >80% content overlap = keep higher-credibility one)
3. Rank by (credibility score × agent quality score)
4. Select top N for ingestion (--sources count)
5. Report skipped sources with reasons in Phase 5 — their captures are retired to `inbox/.processed/` with `ingest_status: skipped` (Phase 3)

In **retardmax mode**: lower the rejection threshold (accept Medium and above without filtering), but still score — the scores carry forward into confidence tags.

#### Phase 3: Stage and Ingest (inbox-first)

Research reuses the `/wiki:ingest` protocol: a source is staged in `inbox/` first, then processed into `raw/`, then the intake item moves to `inbox/.processed/`. The only research-specific differences are that many agents stage in parallel, one coordinator processes the batch, and the captures carry research metadata (query, agent, credibility) that is copied into the raw artifact. The capture schema is in `references/research-infrastructure.md` § Inbox Staging & Batch Ingest.

**Stage (each search agent, during Phase 2 — parallel-safe)**

For every source the agent decides to return, before returning:

1. Write `inbox/YYYY-MM-DD-research-<agent-slug>-<source-slug>.md` (plan mode: `inbox/YYYY-MM-DD-<path-slug>-<agent-slug>-<source-slug>.md`). The agent-slug (`academic`, `opposing`, `q3`, ...) makes parallel captures collision-free; the source-slug follows `references/ingestion.md` § Slug Generation.
2. Frontmatter: `title`, `source` (the URL), `research_query` (topic, sub-question, or thesis claim), `research_mode` (`topic|question|thesis`), `research_agent`, `research_session` (session id when `--min-time`), `research_round`, `agent_quality` (1-5), `key_findings`, and in thesis mode `thesis_direction` (`supports|opposes|nuances`) plus `evidence_strength`.
3. Body: the content WebFetch returned, as-is. The capture is the intake trace, not the processed artifact — do not clean it up or summarize it here.
4. Agents never create, edit, or move anything under `raw/` and never touch `_index.md` files.

**Process (the coordinator, after Phase 2b)**

The coordinator is the research command itself, or in `--plan` mode the path-agent for its path. For each staged capture, in credibility order:

1. **Selected** (top N by Phase 2b ranking, up to --sources): process the capture as `/wiki:ingest` processes an inbox item — read the capture, auto-detect type (academic → papers, news → articles, code → repos, guides → articles, data → data), extract and summarize, generate tags.
2. Write the processed artifact to `raw/{type}/YYYY-MM-DD-<source-slug>.md` (plan mode: `raw/{type}/YYYY-MM-DD-<path-slug>-<source-slug>.md`) with the standard raw frontmatter (`title`, `source`, `type`, `ingested`, `tags`, `summary`) plus the research provenance carried over from the capture: `inbox_item` (the capture's `inbox/.processed/` path), `research_query`, `research_agent`, `research_session` when present, `credibility_score`, and `credibility_tier`. The credibility tier is what compilation later turns into article `confidence:`.
3. Move the capture to `inbox/.processed/` (same filename). The raw artifact's `inbox_item` and the capture's `source` URL are the two ends of the provenance link.
4. **Skipped** (Reject tier, duplicate URL, >80% overlap, or ranked below the --sources cut): do not write to `raw/`. Add `ingest_status: skipped` and `skip_reason` to the capture frontmatter and move it to `inbox/.processed/` so the decision is auditable and the live inbox stays clean. Exception: a credible capture that was cut only by the `--sources` cap may be left pending in `inbox/` when the user is likely to want it — report it, and let the next round or `/wiki:ingest --inbox` process it.
5. Deduplicate against existing `raw/` by `source` URL before writing; if the same URL was already ingested, treat the capture as skipped with `skip_reason: already-ingested`.
6. Update `raw/{type}/_index.md`, `raw/_index.md`, and the master `_index.md` source count (best-effort; the Derived Index Protocol rebuilds on the next read). In `--plan` mode, path-agents skip index updates entirely.

Never write to `raw/` from a capture that does not exist in `inbox/` first. If a source was found but not staged (agent error, crash), stage it now and then process it — do not shortcut.

#### Phase 4: Compile

1. Read all newly ingested sources
2. Follow the compilation protocol in `skills/wiki-manager/references/compilation.md`:
   - Extract key concepts, facts, relationships
   - Map to existing wiki articles
   - Create new articles or update existing ones
   - Use dual-link format for all cross-references
   - Set confidence levels based on source quality and corroboration:
     - Multiple agents found corroborating sources → high
     - Single source or recent/unverified → medium
     - Contrarian agent only, or anecdotal → low
   - Add bidirectional See Also links
   - Write-guard (public topic): no fact, citation, `sources:` entry, or link
     in a new or updated article traces to a `-PRIVATE` sibling, `goals.md`,
     or private inventory, and no article contains a `-PRIVATE` path string;
     such material moves to the private sibling or is dropped
3. Update all `_index.md` files
4. Update master `_index.md` with new stats

#### Phase 5: Report & Log

1. Append to topic wiki `log.md`: `## [YYYY-MM-DD] research | "topic" → N sources ingested, M articles compiled (K captures staged, P pending in inbox)`
2. Append to hub `HUB/log.md`: same entry
   - Write-guard: when the topic is public and private context (a `-PRIVATE`
     sibling, `goals.md`, private inventory) shaped the round, say so in the
     Report below and append that note to the private sibling's `log.md`;
     neither the public `log.md` nor `HUB/log.md` names private material

3. Report:
   - **Topic researched**: the query
   - **Round**: N of M (if --min-time)
   - **Agents launched**: N (list which angles)
   - **Sources found**: N total across all agents
   - **Sources staged**: K inbox captures (all moved to `inbox/.processed/` unless listed as pending)
   - **Sources ingested**: M (list with URLs, quality scores, and raw paths)
   - **Sources skipped**: list with reason (low quality, duplicate, paywall, thin) — retired to `inbox/.processed/`
   - **Pending in inbox**: captures left for a later round or `/wiki:ingest --inbox`
   - **Articles created**: list with paths and summaries
   - **Articles updated**: list with what was added
   - **Confidence map**: which claims are high/medium/low confidence and why
   - **New connections**: cross-references discovered between new and existing articles
   - **Remaining gaps**: what's still not covered (numbered list — used by gap-closing offer below)
   - **Suggested follow-ups**: specific subtopics for next round (or manual `/wiki:research` commands)
   - **Time spent**: this round / total elapsed (if --min-time)
   - **Progress score**: 0-100, calculated as:
     - Sources ingested this round × 3 (max 30)
     - Articles created/updated this round × 5 (max 30)
     - Cross-references added × 2 (max 20)
     - Average source credibility score × 4 (max 20)
     - Score interpretation: 0-40 = minimal, 41-70 = moderate, 71-90 = strong, 91-100 = comprehensive
   - **Cumulative progress** (if --min-time): total across all rounds, round-over-round delta
   - **Termination recommendation**: If progress score ≥ 80 AND remaining gaps are low-priority AND cross-reference density is high (>60% of articles linked) → "Research quality ceiling reached. Consider early completion." If progress score < 40 → "Low yield this round. Consider: different search terms, --deep mode, or narrower topic focus."

#### Gap-Closing Offer

After presenting the report, if there are 2 or more remaining gaps, offer to close them using `--plan` parallel research. Present the gaps as a numbered list and let the user pick which ones to pursue:

```
### Close gaps?

Pick which gaps to research in parallel (all run at once):

1. Dose-response curves for red vs near-infrared wavelengths
2. Long-term safety data for daily exposure
3. Device comparison (clinical vs consumer panels)
4. Combination protocols with other therapies
5. Pediatric and geriatric contraindications

Enter numbers (e.g. 1,2,4), "all", or "skip":
```

On user selection:
- Parse the selected numbers into research paths
- Each selected gap becomes a path in a `--plan` dispatch
- Launch immediately — no second confirmation needed (the user just chose)
- The selected gaps inherit the current session's flags (`--deep`, `--wiki`, `--project`, etc.)
- After all paths complete, run the standard cross-path compilation and report

On "skip" or if fewer than 2 gaps remain: end normally.

This offer also appears after `--plan` rounds complete (the cross-path compilation may surface new gaps). It does NOT appear during `--min-time` multi-round research — those rounds manage their own gap-to-round pipeline via the reflection protocol.
