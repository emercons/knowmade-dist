# Research Modes

Optional research modes, moved verbatim out of `commands/research.md`. Read only the
section the invocation selects: `--min-time` (Minimum Time Research), question input
(Question Research Mode), thesis input (Thesis Research Mode), `--plan` (Plan Mode).
The phases they modify are in [`research-protocol.md`](research-protocol.md); wherever a section here says "below" for Phases 1-5 or the Agent Prompt Template, read it there.

### Minimum Time Research (`--min-time`)

**--min-time <duration>**: Minimum research time. Keep running research rounds until this duration is reached. Formats: `30m`, `1h`, `2h`, `4h`. Default: single round (no minimum).

#### Multi-Round Session State

When `--min-time` is set, create and maintain:

- an **ephemeral session registry** for crash recovery and round-to-round state
- **durable provenance artifacts** for replayable audit trails and resume briefings

Both live in the wiki root: `.research-session.json` (ephemeral, deleted on
normal completion) and `.session-events.jsonl` plus `.session-checkpoint.json`
(durable). Their schemas, lifecycle, resume protocol and cleanup are in
`references/research-sessions.md` (`mode: plan` adds `paths`:
`references/research-plan.md`). On resume, skip Phase 1 and start from the last
completed round's gaps. Round scoring, termination and reflection are in
`references/research-rounds.md`.

When `--min-time` is set, research runs in ROUNDS until the time budget is spent:

```
Round 1: Run full research protocol (Phase 1-5)
         → Produces gaps, progress score, and suggested follow-ups
         → Update .research-session.json
         → Append to .session-events.jsonl and refresh .session-checkpoint.json
         → Check elapsed time

Reflect: Review ALL findings so far holistically (not just this round's gaps)
         → Score each gap: impact (1-5) × feasibility (1-5) × specificity (1-5)
         → Re-evaluate: Has the research direction shifted? Are earlier gaps still relevant?
         → Pick top 3 gaps by composite score for next round
         → If progress score ≥ 80 and no high-impact gaps → recommend early completion

Round 2: Run research on top 3 gaps as subtopics
         → Compile into wiki, discover new gaps
         → Update .research-session.json
         → Append to .session-events.jsonl and refresh .session-checkpoint.json
         → Check elapsed time

Reflect: Same holistic reflection — look for cross-topic connections between rounds
         → Research shows this catches 34% more cross-topic connections than gap-picking alone

Round 3+: Continue pattern (research → reflect → decide) until:
          - --min-time is reached, OR
          - Progress score ≥ 80 with no high-impact gaps (early completion), OR
          - Two consecutive rounds with progress score < 40 (diminishing returns)

Final:   Run /knowmade:lint --fix to clean up
         → Generate a summary of everything researched
         → Report total: rounds, sources, articles, progress trajectory, time spent
         → Append completion event, refresh .session-checkpoint.json
         → Delete .research-session.json
```

**Round strategy:**
- Each round picks the most important unfilled gaps from the previous round's report
- Subtopics get progressively more specific as rounds continue (broad → narrow → niche)
- If a round finds no new gaps, switch to `--deep` mode on existing articles to find connections and contradictions
- If still no gaps after deep mode, research is complete regardless of remaining time — report early completion
- Each round logs to `log.md` independently
- **Progress-based termination**: After each round, calculate a progress score (0-100). If score ≥ 80 and no high-priority gaps remain, recommend early completion even if time budget remains — more rounds of diminishing returns waste tokens without improving quality.
- **Low-yield detection**: If a round's progress score is < 40, the round produced little value. Switch strategy: try --deep angles, broaden search terms, or narrow topic focus. Don't keep doing the same thing.

**Time tracking:**
- Check wall clock at the start and after each round
- A round that would exceed the time budget by more than 50% should not start (e.g., if 45 min left and rounds average 40 min, run it; if 10 min left, don't)
- Report time spent in final summary

**Example:**
```
/knowmade:research "CRISPR gene therapy" --new-topic --min-time 2h --deep
```
Creates `HUB/topics/crispr-gene-therapy/`, then runs ~3-5 research rounds over 2 hours, progressively drilling into subtopics the earlier rounds surfaced.

#### Question Research Mode

The question itself is the scope constraint — like a thesis constrains thesis research.

**Step 1: Decompose the question** into 3-5 focused sub-questions. Example:

Input: "What makes long form articles go viral and how to replicate it"
Decomposition:
- **What**: What patterns do viral long-form articles share? (structure, length, hooks, format)
- **Why**: What psychological/social mechanisms drive sharing? (emotion, identity, utility)
- **How**: What is the step-by-step process to write one? (research, outline, writing, distribution)
- **Who**: Who has done this successfully and what do they say? (case studies, practitioner interviews)
- **Data**: What does the data say? (studies on shareability, engagement metrics, platform algorithms)

Present the decomposition to the user. Then proceed.

**Step 2: One agent per sub-question.** Instead of generic angles (academic, technical, applied), each agent targets a specific sub-question. This keeps research focused and produces a complete answer.

| Agent | Sub-question | Search Strategy |
|-------|-------------|----------------|
| Agent 1 | What patterns? | Search for analyses of viral articles, content structure studies, BuzzSumo-style research |
| Agent 2 | Why psychologically? | Search for psychology of sharing, Jonah Berger's research, social currency, emotion triggers |
| Agent 3 | How to do it? | Search for practitioner playbooks, writing frameworks, distribution strategies |
| Agent 4 | Who does it well? | Search for case studies, specific viral articles and breakdowns of why they worked |
| Agent 5 | What does data say? | Search for studies on content virality, engagement data, platform algorithm research |

In `--deep` mode: add agents for adjacent sub-questions discovered during decomposition.
In `--retardmax` mode: add rabbit-hole agents + skip decomposition confirmation.

**Step 3: Compile with structure.** Articles are organized to answer the original question:
- Concept articles for each key finding
- A **topic article** that synthesizes the full answer to the original question — this is the "playbook"
- Reference articles for tools, examples, and further reading

**Step 4: Generate playbook.** After compilation, automatically create an output artifact:
- Save to `output/playbook-{slug}-{YYYY-MM-DD}.md` following core principle #9 (chunked writes): Write frontmatter + intro first, then Edit to append each section
- Structure: the original question, key findings per sub-question, actionable steps, examples, sources
- This is the deliverable — a practical, actionable answer filed back into the wiki

**Step 5: Derive theses.** From the findings, suggest 2-3 testable claims that could be investigated with `--mode thesis`. Example: "Articles with a personal narrative hook get 3x more shares than data-led hooks" — this is a specific claim that can be verified.

#### Thesis Research Mode (`--mode thesis`)

**--mode thesis "<claim>"**: Activate thesis mode. The claim is the scope constraint — sources that don't relate to it are skipped. Agents evaluate evidence for/against. A verdict is rendered at the end.

**Why this exists as a mode, not a separate command**: thesis research is 70% the same pipeline as topic/question research (agent dispatch, credibility scoring, session registry, multi-round --min-time, compilation, ingestion). The 30% that's different is: scope filtering by claim, for/against agent framing, evidence strength classification, and verdict rendering. Those differences are described below as modifications to the standard phases. Previously this was a separate `/knowmade:thesis` command (224 lines) which duplicated all the shared infrastructure.

**Phase 0: Decompose the thesis.** Before any research, break the claim into:

1. **Core claim**: the central assertion in one sentence
2. **Key variables**: the specific things being connected (e.g., "sunlight exposure", "CAA progression", "vitamin D")
3. **Testable prediction**: what would be true if the thesis is correct?
4. **Falsification criteria**: what evidence would disprove it?
5. **Scope boundary**: what is NOT part of this thesis? (This is the bloat filter — if a source doesn't touch these variables, skip it.)

Present the decomposition to the user for confirmation before proceeding.

**Wiki setup modification**: In addition to the standard wiki resolution, create a thesis file at `wiki/theses/<slug>.md`:

```markdown
---
title: "Thesis: <thesis statement>"
type: thesis
status: investigating
created: YYYY-MM-DD
updated: YYYY-MM-DD
verdict: pending
confidence: pending
core_claim: "<one sentence>"
key_variables: [var1, var2, var3]
falsification: "<what would disprove this>"
---

# Thesis: <thesis statement>

## Core Claim
## Key Variables
## Testable Prediction
## Falsification Criteria
## Evidence For
(populated during research)
## Evidence Against
(populated during research)
## Nuances & Caveats
(populated during research)
## Verdict
**Status**: Investigating
```

**Phase 2 modification: thesis-directed agents.** Instead of the standard agent table (Academic, Technical, etc.), use:

| Agent | Focus | Thesis Lens |
|-------|-------|-------------|
| **Supporting** | Evidence that supports the thesis | Search for studies, data, mechanisms that confirm the claim. Strongest evidence first. |
| **Opposing** | Evidence that contradicts the thesis | Counter-evidence, failed replications, alternative explanations. Steelman the opposition. |
| **Mechanistic** | HOW/WHY the thesis could be true or false | Underlying mechanisms, pathways, causal chains connecting the variables. |
| **Meta/Review** | Meta-analyses, systematic reviews, expert consensus | Aggregate evidence on this specific question. These carry the most weight. |
| **Adjacent** | Related findings that nuance the thesis | Edge cases, moderating variables, conditions under which the thesis holds or fails. |

In `--deep` mode, add: **Historical** (evolution of thinking on this claim), **Quantitative** (effect sizes, confidence intervals, dose-response data), **Confounders** (variables that could make a spurious correlation look causal).

Each agent must evaluate: relevance (direct/indirect/tangential — SKIP tangential), evidence strength (meta-analysis > RCT > cohort > case > opinion > anecdotal), and direction (supports/opposes/nuances).

**Phase 4 modification: evidence compilation.** After standard compilation, update the thesis file:
- **Evidence For**: list each supporting finding with source, evidence strength, and one-line summary. Sort by evidence strength (meta-analyses first).
- **Evidence Against**: same format for opposing findings.
- **Nuances & Caveats**: conditions, moderators, edge cases.
- Each finding is marked "Strong" / "Moderate" / "Weak" based on combined credibility + evidence strength.

**Phase 5 modification: verdict.** After all rounds complete, render a verdict in the thesis file:

```markdown
## Verdict
**Status**: Supported | Partially Supported | Insufficient Evidence | Contradicted | Mixed
**Confidence**: High | Medium | Low
**Summary**: 2-3 sentences on what the evidence shows.
**Strongest supporting evidence**: [list]
**Strongest opposing evidence**: [list]
**Key caveats**: [list]
**What would change this verdict**: [specific future findings]
**Suggested follow-up theses**: [derived from findings]
```

Update frontmatter: `status: completed`, `verdict: <result>`, `confidence: <level>`.

**Multi-round modification: anti-confirmation-bias.** When `--min-time` is set:
- **Round 1**: Broad evidence gathering — supporting + opposing + mechanistic
- **Round 2**: Drill into the WEAKEST side. If Round 1 found mostly supporting evidence, Round 2 focuses harder on finding counter-evidence (and vice versa). This prevents confirmation bias — the most important methodological difference from standard research.
- **Round 3+**: Follow up on specific sub-questions, confounders, or moderating variables
- **Final**: Synthesize verdict, update thesis file

Session state uses `.thesis-session.json` instead of `.research-session.json`, tracking evidence for/against counts and current verdict direction per round. Keep the same provenance pattern: the thesis session file is ephemeral, while `.session-events.jsonl` and `.session-checkpoint.json` persist across normal completion. Resume detection: "Found interrupted thesis session (Round N, current leaning: X). Continue?"

### Plan Mode (`--plan`)

**--plan**: Decompose the topic into 3-5 independent research paths, present the plan for confirmation, then execute all paths in parallel. Each path gets its own agent group (5 agents standard, 8 with `--deep`). Ingest runs in parallel across paths (each path stages unique inbox captures and writes unique raw files); compilation runs once after all paths complete (single pass sees all sources for better cross-referencing). Without `--plan`, research runs a single path as before.

When `--plan` is set, insert a planning phase between Phase 1 and Phase 2. Plan mode works with topic, question, AND thesis input — it decomposes any of them into parallel paths. It is independent of `--min-time` (which controls multi-round iteration within a single path or after plan execution).

#### Phase 1.5: Research Plan Generation

After Phase 1 (existing knowledge check), generate a research plan:

1. Based on the topic, existing wiki state, and identified gaps, decompose into 3-5 independent research paths. Each path should:
   - Have a clear, non-overlapping scope
   - Be searchable independently (no dependencies on other paths' findings)
   - Target a specific aspect: foundational concepts, current state, practical applications, criticisms/limitations, adjacent connections

2. Present the plan to the user:

   ```
   ## Research Plan — <topic>

   ### Paths (will run in parallel)

   1. **<Path name>**
      Focus: <what this path covers>
      Search angles: <2-3 specific search strategies>
      Target: <N> sources

   2. **<Path name>**
      ...

   ### Estimated
   - <N> paths x <M> agents each = <total> parallel agents
   - Target sources: <total across all paths>
   - Compilation: single pass after all paths complete

   Proceed? (y/n/edit)
   ```

3. Wait for user confirmation. If "edit", let them modify paths. If "n", abort.

#### Phase 2 modification: Parallel Path Dispatch

On confirmation, launch one Agent per path (single message, multiple Agent tool calls). Each path-agent receives:

- Its specific focus and search angles from the plan
- The standard agent template from `references/research-agents.md`
- Instructions to run the full search → stage → credibility → ingest pipeline (Phases 2, 2b, 3 below). The path-agent is the batch coordinator for its own sub-agents: sub-agents stage captures in `inbox/`, the path-agent reviews and processes them into `raw/`
- A path identifier used to prefix inbox capture and raw file slugs for deduplication

Each path-agent internally launches its own 5/8/10 sub-agents (standard/deep/retardmax) for the search phase, exactly as single-path research does today.

**File ownership rule**: Each path stages captures as `inbox/YYYY-MM-DD-<path-slug>-<agent-slug>-<source-slug>.md` and writes processed artifacts to `raw/<type>/YYYY-MM-DD-<path-slug>-<source-slug>.md`. The path-slug prefix prevents filename collisions between paths; the agent-slug keeps the captures of one path's sub-agents distinct. Processed captures move to `inbox/.processed/` under the same name.

**Index updates**: Path agents skip `_index.md` updates during parallel ingest. Indexes rebuild on the next read per the Derived Index Protocol (`references/indexing.md`).

#### Phase 4 modification: Cross-Path Compilation

After all path agents return, run a single compilation pass that:

1. Reads ALL newly ingested sources from all paths
2. Follows the standard compilation protocol in `references/compilation.md`
3. Draws cross-references between findings from different paths — this is the plan's primary value: the compiler sees the full picture across all paths simultaneously
4. Creates articles that synthesize across paths where findings connect

#### Phase 5 modification: Plan Report

In addition to the standard report fields, include:

- **Per-path breakdown**: sources found, sources ingested, key findings per path
- **Cross-path connections**: relationships discovered during compilation that span paths
- **Low-yield paths**: paths that returned few sources — suggest follow-up or different search angles

#### Session Registry Extension

When `--plan` is active, `.research-session.json` includes additional fields. See `references/research-plan.md` for the full schema. The `mode` field distinguishes plan sessions from single-path sessions. Resume detection checks `paths[].status` to re-launch only incomplete paths.

#### Interaction with `--min-time`

`--plan` + `--min-time` work together: Round 1 executes the full plan (all paths in parallel). The reflection step identifies remaining gaps. Round 2 generates a NEW plan targeting those gaps (typically 2-3 paths). Each round is a full plan-dispatch-compile cycle. The time budget and progress scoring apply across all rounds, same as single-path `--min-time`.
