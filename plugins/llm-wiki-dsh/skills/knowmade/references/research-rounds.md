# Research Rounds (between rounds)

Round scoring and reflection for multi-round research. Read only with `--min-time` (or `--plan` with more than one round), after a round completes and before the next one is planned (`research-modes.md` § Minimum Time Research).

## Progress Scoring (0-100)

### Why

Quantify research quality per round to enable principled termination and low-yield detection. Without this, multi-round research either runs until the timer expires (wasting tokens on diminishing returns) or stops too early (missing important gaps). The score is the decision signal.

### Formula

| Component | Calculation | Max Points |
|-----------|-------------|-----------|
| Sources ingested | count x 3 | 30 |
| Articles created/updated | count x 5 | 30 |
| Cross-references added | count x 2 | max(20, existing_articles x 2) — scales with wiki maturity |
| Average credibility score | avg x 4 | 20 |

**Scaling note**: The cross-reference cap starts at 20 for new wikis and grows as the wiki matures. A wiki with 10 existing articles has a cap of 20; one with 15 articles has a cap of 30. This prevents the component from saturating on Round 1 when the wiki is small, while still rewarding dense cross-linking in mature wikis.

### Interpretation

- 0-40: Minimal yield — consider changing strategy
- 41-70: Moderate — research is productive, continue
- 71-90: Strong — good coverage being built
- 91-100: Comprehensive — near-complete coverage

### Termination Decision Tree

```
progress_score >= 80?
  |-- YES -> Any high-impact gaps remaining?
  |           |-- YES -> Continue (but note quality is high)
  |           +-- NO  -> Cross-ref density > 60%?
  |                      |-- YES -> RECOMMEND EARLY COMPLETION
  |                      +-- NO  -> One more round focusing on connections
  +-- NO  -> progress_score < 40?
             |-- YES -> LOW YIELD WARNING
             |          -> Suggest: different terms, --deep, narrower topic
             +-- NO  -> Continue normally
```

### Low-Yield Response Options

When progress_score < 40 for two consecutive rounds:
1. Switch to `--deep` mode if not already
2. Try different search angle framing
3. Narrow the topic to a more specific subtopic
4. Report early completion: "Research appears exhausted for this topic"

### Trajectory-Based Triggers

In addition to per-round thresholds, monitor the round-over-round trend:

**Declining trajectory warning**: If 3 consecutive rounds show declining scores totaling 30+ points of cumulative drop (e.g., 98→95→68→58 = -40 total), warn:
> "Research yield is declining across rounds (trajectory: {scores}). Consider: narrowing topic focus, switching to --deep mode, or completing early if core gaps are filled."

**Plateau detection**: If 2 consecutive rounds score within 5 points of each other AND no new high-impact gaps are identified, recommend early completion:
> "Research has plateaued at {score}. No new high-impact gaps found. Early completion recommended."

**Stalled detection**: If any single round scores <20, immediately flag:
> "Round yielded near-zero value. Stop and reassess: is the topic too narrow, the search terms wrong, or the knowledge base already comprehensive?"

## Gap Scoring & Reflection

### Why

Between multi-round research rounds, reflect holistically on accumulated knowledge and score gaps for the next round. **Key insight from testing**: plan reflection's primary value is discovering cross-topic connections between rounds — NOT changing the research direction. Testing against a real 4-round research wiki showed the research path was already well-chosen (reflection confirmed every round's direction). But it found 5 undrawn cross-references that exist in the content but were never linked. This is the 34% improvement the literature predicts.

### Gap Scoring Formula

Each gap is scored on three dimensions (1-5 each):

| Dimension | 5 (highest) | 3 (moderate) | 1 (lowest) |
|-----------|-------------|--------------|------------|
| **Impact** | Filling this gap fundamentally changes understanding | Adds useful context | Nice-to-know but not essential |
| **Feasibility** | Likely findable with web search | May exist but hard to find | Probably requires primary research |
| **Specificity** | Well-defined, searchable question | Somewhat vague | Too broad to target effectively |

**Composite score** = Impact x Feasibility x Specificity (range: 1-125)

**Selection**: Pick top 3 gaps by composite score for the next round.

### Reflection Protocol

Between rounds, the orchestrating agent should (in priority order):

1. **Draw connections** between this round's findings and ALL prior rounds (not just the previous one) — this is the highest-value activity
2. **Update cross-references** — add See Also links between articles that share concepts across rounds
3. **Re-evaluate earlier gaps** — some gaps from round 1 may now be filled or irrelevant
4. **Score remaining gaps** using the formula above
5. **Adjust research direction** — only if findings clearly indicate a shift (rare in practice)
6. **Note reflection in session registry** — add `reflection_notes` to the round entry (`research-sessions.md`)

### Example Reflection Output

```
