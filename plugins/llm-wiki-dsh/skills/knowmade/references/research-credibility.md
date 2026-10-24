# Research Credibility (Phase 2b)

The independent credibility review of staged captures. Read at `research-protocol.md` § Phase 2b, before any capture is processed into `raw/`.

### Why

Independent assessment of source credibility before ingestion. Prevents the "fox guarding the henhouse" problem where agents self-rate their own source quality. The review runs over the staged `inbox/` captures and writes `credibility_score`/`credibility_tier` back into each capture (`research-staging.md`); only captures at or above the ingest threshold are processed into `raw/`. Credibility scores carry forward into article `confidence:` frontmatter tags during compilation.

### Scoring Rubric

| Signal | Points | How to Detect |
|--------|--------|---------------|
| Peer-reviewed | +2 | DOI present, journal/conference name, PubMed ID, arxiv with venue |
| Recent (<=3 years) | +1 | Publication date check |
| Older (>3 years) | 0 | — |
| Very old (>10 years) | -1 | Unless it's a foundational/landmark paper |
| Known author/institution | +1 | Recognized university, major lab, cited expert |
| Unknown author | 0 | — |
| Potential bias detected | -1 | Industry-sponsored without disclosure, activist org, predatory journal |
| Vendor primary source | -1 | First-party vendor docs or blog about own product (authoritative for facts, but inherent promotional framing) |
| Corroborated by other agents | +1 per agent (max +2) | Multiple agents found similar claims from independent sources |

**Non-stacking rule**: Bias signals do not stack. If a source triggers both "potential bias" and "vendor primary source," apply only the more specific one (-1 total, not -2). These are refinements of the same concern (promotional framing), not independent dimensions.

### Credibility Tiers

| Tier | Score Range | Action | Confidence Tag |
|------|------------|--------|---------------|
| High | 4-6 | Ingest | confidence: high |
| Medium | 2-3 | Ingest | confidence: medium |
| Low | 0-1 | Ingest only if unique angle | confidence: low |
| Reject | <0 | Skip | — |

### Bias Detection Signals

- Industry whitepaper with no independent validation
- Press release disguised as news article
- Predatory journal (check Beall's List indicators: rapid acceptance, broad scope, aggressive solicitation)
- Affiliate/sponsored content without disclosure
- Single-perspective advocacy org
- First-party vendor documentation or engineering blog about own product (authoritative for facts, but inherent promotional framing — e.g., Anthropic writing about Claude Code, LangChain surveying their own users, Factory.ai benchmarking their own compression algorithm)

### Retardmax Mode

Lower rejection threshold — accept Medium and above. Still score everything; scores carry forward into article confidence tags.
