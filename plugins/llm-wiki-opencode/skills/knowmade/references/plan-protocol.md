# Plan Protocol

The `/knowmade:plan` (`wiki plan`) workflow, moved verbatim out of `commands/plan.md` so every
runtime ships it. It continues the hub-resolution steps 1-3 in
[`hub-resolution.md`](hub-resolution.md); numbering below picks up at step 4.
`$ARGUMENTS` is the user's request: free text plus the flags listed under
Parse $ARGUMENTS.

4. Read `<wiki>/_index.md` to verify. If missing → stop with "No wiki found (or no articles). Run `/knowmade:wiki init` and `/knowmade:research` first to build a knowledge base."

Generate an implementation plan for what the user wants to build, grounded in the wiki's accumulated research. Follow the 6-stage pipeline below.

Private goals awareness: when planning from a private topic wiki, read relevant
`goals.md`, `config.md`, and high-priority inventory records as planning
context. Use them to choose tradeoffs and sequencing. They are not factual
evidence; factual claims still need wiki/raw citations. Private siblings and
`--with` wikis may be read freely; what a plan saved into a public topic may
contain is governed by `skills/knowmade/references/knowmade-profile.md` § Private Data Boundary: Read Freely, Write-Guard and enforced in Stage 6.

Inventory awareness: use active inventory records as planning constraints when
they are relevant to the goal, especially blocked tasks, candidate corpora,
open questions, and watch items. If the plan creates a durable work queue,
suggest inventory records or a saved inventory view and show a sample before
creating them. Keep project rationale in `WHY.md`; inventory is for trackable
items and next actions.

### Parse $ARGUMENTS

- **goal**: What to build — everything that is not a flag. This is the planning objective.
- **--wiki <name>**: Target a specific topic wiki as the primary knowledge source (subject matter)
- **--with <wiki>**: Load a supplementary wiki as additional context. The primary wiki provides **domain knowledge** (what to build); `--with` wikis provide **craft/skill** knowledge (how to build it, writing techniques, design patterns). Multiple `--with` flags allowed. A `-PRIVATE` `--with` wiki may be read; its material is subject to the Stage 6 write-guard.
- **--local**: Use project-local `.wiki/`
- **--include-archived**: Explicitly allow archived primary or supplementary
  wiki context. Label archived-derived constraints/sources in the plan.
- **--quick**: Skip interview and gap research — produce a plan directly from wiki content (Stage 1 → Stage 5)
- **--no-interview**: Skip the interview stage (Stage 2)
- **--no-research**: Skip gap research (Stage 3) — plan only from existing wiki content
- **--format**: Output structure. One of:
  - `roadmap` (default) — phased implementation plan with timeline
  - `rfc` — Request for Comments format (Google/Uber style: context, goals, design, alternatives)
  - `adr` — Architecture Decision Record (context, decision, consequences per choice)
  - `spec` — Technical specification (architecture, APIs, data models, testing)

---

## Stage 1: Context Assembly

Read the wiki deeply to understand what knowledge exists about the planning goal.

1. **Read master `_index.md`** — scan all articles for relevance to the goal
2. **Read ALL category `_index.md` files** — concepts, topics, references
3. **Grep the wiki** for key terms from the goal (synonyms, related concepts)
4. **Read all relevant articles in full** — follow See Also links, read cited raw sources
5. **Read sibling wiki `_index.md` files** — check if related knowledge exists elsewhere; a `-PRIVATE` sibling may be read, and anything taken from it is tagged private in the context summary so the Stage 6 write-guard can trace it
6. **Load `--with` wikis** (if specified): For each supplementary wiki, look up in `HUB/wikis.json`, read its `_index.md` and relevant articles. These provide craft/skill context — techniques, patterns, best practices to apply when generating the plan.

Archived primary or supplementary wikis are rejected unless
`--include-archived` is present. Deep context assembly may mention archived
index matches as a separate note, but archived material should not shape the
plan unless explicitly included.

Produce a **context summary**: what the wiki knows about this topic, organized by:
- **Directly relevant**: articles that address the goal head-on
- **Supporting context**: articles that inform design decisions
- **Gaps identified**: what the goal needs that the wiki doesn't cover

Present the context summary to the user before proceeding:

```
📚 Wiki knowledge for "{goal}":

Directly relevant (N articles):
- [Article 1](path) — what it contributes
- [Article 2](path) — what it contributes

Supporting context (N articles):
- [Article 3](path) — relevant because...

Knowledge gaps:
- Gap 1: The wiki doesn't cover X, which we'll need for this plan
- Gap 2: Y is mentioned but not in enough detail

Proceeding to interview...
```

---

## Stage 2: Interview (skip if `--no-interview` or `--quick`)

Ask the user 3-7 clarifying questions to surface requirements, constraints, and edge cases that the wiki doesn't address. These questions should be **informed by the wiki content** — don't ask about things the wiki already answers.

Good interview questions:
- Constraints the wiki can't know: "What's the timeline? What's the budget for dependencies?"
- Scale parameters: "How many articles/sources are we targeting?"
- Priority tradeoffs: "The wiki covers both X and Y approaches — which matters more for your case?"
- Edge cases: "The wiki notes [risk]. How should we handle it?"
- Non-functional requirements: "Performance targets? Backwards compatibility needs?"

Present all questions at once. Wait for answers before proceeding.

---

## Stage 3: Gap Research (skip if `--no-research` or `--quick`)

For each knowledge gap identified in Stage 1, run a targeted web search to fill it.

1. For each gap, run 1-2 WebSearch queries
2. For promising results, use WebFetch to extract relevant content
3. Synthesize findings into a brief gap-fill (3-5 bullet points per gap)
4. If a gap is substantial enough, ingest the source into the wiki's `raw/` for future use

Do NOT launch full research agents — this is lightweight, targeted filling. If gaps are large, suggest `/knowmade:research` as a follow-up instead.

---

## Stage 4: Synthesis

Merge wiki knowledge + interview answers + gap research into a unified context document. This is internal (not shown to user) — it's the input to Stage 5.

Structure:
```
GOAL: {what to build}
WIKI EVIDENCE: {key findings from relevant articles with citations}
USER REQUIREMENTS: {from interview}
GAP FILLS: {from Stage 3 research}
CONSTRAINTS: {from wiki + interview}
RISKS: {from wiki's contrarian/critique coverage}
```

---

## Stage 5: Plan Generation

Generate the plan in the requested `--format` (default: roadmap).

### Roadmap Format (default)

```markdown
# Plan: {goal}

> Generated from [{wiki-name}]({path}) wiki ({N} articles consulted)

## Executive Summary
[2-3 sentences: what we're building, why, key design decisions]

## Architecture Decisions

### Decision 1: {title}
**Context**: [cite wiki article] documents that...
**Options considered**:
- Option A: ... (per [wiki article])
- Option B: ... (per [wiki article])
**Decision**: Option A because [evidence from wiki]
**Consequences**: ...

### Decision 2: ...

## Implementation Phases

### Phase 1: {title} (estimated effort: X)
**Goal**: ...
**Tasks**:
- [ ] Task 1
- [ ] Task 2
**Dependencies**: None
**Validation**: How to verify this phase works
**Wiki grounding**: Based on [article](path) which says...

### Phase 2: {title} (estimated effort: X)
...

## Risks & Mitigations
| Risk | Source | Mitigation |
|------|--------|------------|
| Risk 1 | [Wiki article](path) | How to handle |

## Open Questions
- Questions the wiki and interview didn't resolve
- Suggested follow-up research

## Sources Consulted
- [Article 1](path) — what was drawn from it
- [Article 2](path) — what was drawn from it
```

### RFC Format (`--format rfc`)

Follow Google/Uber RFC structure:
- **Context & Scope**: from wiki evidence
- **Goals & Non-Goals**: from interview + wiki
- **Design**: architecture decisions grounded in wiki research
- **Alternatives Considered**: from wiki's coverage of different approaches
- **Cross-Cutting Concerns**: security, performance, backwards compatibility

### ADR Format (`--format adr`)

One ADR per major decision. MADR variant:
- **Title**: present-tense imperative
- **Status**: proposed
- **Context**: organizational situation from wiki
- **Options**: with pros/cons from wiki evidence
- **Decision**: the chosen option with rationale
- **Consequences**: effects and follow-up work

### Spec Format (`--format spec`)

Technical specification:
- **System Architecture**: diagrams in ASCII/mermaid, component descriptions
- **API Design**: endpoints, data models, request/response formats
- **Data Model**: schemas, relationships, storage decisions
- **Implementation Details**: code patterns, key algorithms (cite wiki for algorithm choices)
- **Testing Strategy**: unit, integration, e2e test outlines
- **Deployment**: migration path, rollback plan

---

## Stage 6: Save & Log

0. **Write-guard (mandatory when the target wiki is a public, non-`-PRIVATE` topic)**: apply `skills/knowmade/references/knowmade-profile.md` § Private Data Boundary: Read Freely, Write-Guard to the generated plan before saving. Every decision, risk row, phase grounding, and `Sources Consulted` entry traced to a `-PRIVATE` sibling, `--with` wiki, `goals.md`, or private inventory is either dropped or the plan is saved under `<slug>-PRIVATE/output/` instead; the saved file contains no `-PRIVATE` path string and no `sources:` entry into a private topic; if private context shaped the plan, say so in the Report and log it in the private sibling's `log.md`, not the public one.

1. **Save** to `output/plan-{slug}-{YYYY-MM-DD}.md`. Follow core principle #9 (chunked writes): Write the frontmatter + executive summary first, then use sequential Edit calls to append each phase/section. With frontmatter:
   ```
   ---
   title: "Plan: {goal}"
   type: plan
   format: roadmap|rfc|adr|spec
   sources: [wiki articles used]
   generated: YYYY-MM-DD
   ---
   ```

2. **Update indexes**:
   - `output/_index.md` — add row
   - Master `_index.md` — add to Outputs table and Recent Changes

3. **Log**: Append to `log.md`:
   `## [YYYY-MM-DD] plan | "{goal}" → output/plan-{slug}-{YYYY-MM-DD}.md (N articles consulted, M decisions, P phases)`

4. **Report**: Tell the user what was generated, where it's saved, and any open questions or suggested follow-ups.

---

## Examples

```bash
# Full pipeline with interview and gap research
/knowmade:plan "build incremental compilation for Knowmade" --wiki knowmade-dev

# Quick plan from wiki knowledge only (no interview, no research)
/knowmade:plan "add ephemeral wiki support" --wiki knowmade-dev --quick

# RFC format for team review
/knowmade:plan "migrate from full to incremental compilation" --wiki knowmade-dev --format rfc

# ADR for a specific decision
/knowmade:plan "choose between qmd and custom embedding search" --wiki knowmade-dev --format adr

# Spec for implementation
/knowmade:plan "implement confidence decay for wiki articles" --wiki knowmade-dev --format spec
```
