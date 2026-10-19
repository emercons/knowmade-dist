---
description: "Deep multi-agent research on a topic, question, practical problem, or thesis. Launches parallel agents to search the web, stages sources through inbox-first ingest, and compiles them into active wiki articles. Thesis mode provides for/against evidence framing with a verdict."
argument-hint: "<topic|question> [--plan] [--mode thesis \"<claim>\"] [--new-topic] [--sources <N>] [--deep] [--retardmax] [--min-time <duration>] [--specialist <name>] [--no-specialists] [--wiki <name>] [--local] [--project <slug>] [--include-archived]"
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(ls:*), Bash(wc:*), Bash(date:*), Bash(mkdir:*), Bash(python3:*), Bash(scripts/llm-wiki:*), Bash(${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki:*), WebFetch, WebSearch, Agent
---

## Your task

Conduct deep research on the topic in $ARGUMENTS. This is an automated pipeline: search → stage in `inbox/` → ingest into `raw/` → compile.

Research is inbox-first, like every other Knowmade intake path. No research agent writes to `raw/` directly: every captured source is first staged as an `inbox/` capture by the agent that found it, and the batch is then processed into `raw/` by the coordinator using the same protocol `/knowmade:ingest` uses (`references/ingestion.md` § Inbox Processing, `references/knowmade-profile.md` § Inbox-First Source Lifecycle). The research-specific staging and batch rules live in Phase 3 below and in `references/research-staging.md`.

### Parse $ARGUMENTS

- **topic/question**: The research subject — everything that is not a flag. Auto-detected as topic vs question (see Input Detection below).
- **--mode thesis "<claim>"**: Activate thesis mode. The claim is the scope constraint — sources that don't relate to it are skipped. Agents evaluate evidence for/against. A verdict is rendered at the end. See Thesis Research Mode below.
- **--new-topic**: Create a new topic wiki from the topic name and start researching into it. Works from any directory. Derives the wiki slug from the topic (e.g., "quantum error correction" → `quantum-error-correction`).
- **--sources <N>**: Target sources PER ROUND (default: 5, max: 20)
- **--deep**: 8 parallel agents with broader search angles
- **--retardmax**: 10 agents, skip planning, ingest aggressively
- **--min-time <duration>**: Minimum research time. Keep running research rounds until this duration is reached. Formats: `30m`, `1h`, `2h`, `4h`. Default: single round (no minimum).
- **--wiki <name>**: Target a specific existing topic wiki
- **--local**: Use project-local `.wiki/`
- **--plan**: Decompose the topic into 3-5 independent research paths, present the plan for confirmation, then execute all paths in parallel. Each path gets its own agent group (5 agents standard, 8 with `--deep`). Ingest runs in parallel across paths (each path stages unique inbox captures and writes unique raw files); compilation runs once after all paths complete (single pass sees all sources for better cross-referencing). Without `--plan`, research runs a single path as before. See Plan Mode below.
- **--project <slug>**: Tag all new outputs with this project. The research playbook/summary artifact is saved inside `output/projects/<slug>/` instead of flat `output/`. Compiled wiki articles get `project: <slug>` frontmatter. If the project doesn't exist, fail early with a helpful error. See `references/projects.md` for the projects architecture.
- **--include-archived**: Explicitly allow researching into an archived target
  wiki. Keep the target archived and label the session accordingly.
- **--specialist <name>**: Explicitly apply an enabled personal specialist;
  repeat up to three times. For project-local `.wiki/`, explicit selection is
  required because v1 has no persisted local-project allowlist.
- **--no-specialists**: Run the baseline research workflow without reading
  specialist package bodies. Mutually exclusive with `--specialist`.

### `--project <slug>` flag

If `--project <slug>` is set, the research playbook/summary artifact is saved inside `output/projects/<slug>/` instead of flat `output/`, and compiled wiki articles get `project: <slug>` frontmatter. Verify the project exists at `<wiki-root>/output/projects/<slug>/WHY.md` before starting; if not, fail early with a suggestion to run `/knowmade:project new <slug> "goal"` first.

There is no ambient project focus — pass `--project` explicitly when you want project scope. The focus-session mechanism was removed in the v0.2 projects simplification; see `references/projects.md` § "Focus" for the rationale.

### Inventory awareness

At the start, read inventory indexes when cheap. Active `p0`/`p1` items,
candidates, corpora, questions, and watch items can seed research paths or
explain why a topic matters. Do not treat inventory records as evidence; they
are operational state.

### Private goals awareness

If the target wiki is a private topic wiki, read relevant private goals/desires
from `goals.md`, `config.md`, and high-priority inventory records when cheap.
Use them to prioritize research paths and practical deliverables. Do not treat
goals as factual evidence. A `-PRIVATE` sibling may also be read when
researching a public topic; what reaches the public topic's `raw/`, `wiki/`,
and `log.md` is governed by `skills/knowmade/references/knowmade-profile.md` § Private Data Boundary: Read Freely, Write-Guard, applied in Phases 4 and 5.

At the end, propose inventory records only for durable follow-ups: important
unresolved gaps, item/part decisions, candidate sources/corpora to revisit,
watch items, or blocked tasks. Do not create a backlog for every minor search tangent. For larger
pivots, show a 1-3 row sample of proposed records and ask before writing.

### Archive awareness

Research ignores archived topic wikis by default. If `--wiki <name>` resolves to
an archived registry entry or a path under `topics/.archive/`, stop and ask the
user to restore it or rerun with `--include-archived`. When no target wiki is
specified, archived topics may be mentioned as overlap only: read their master
`_index.md` files, warn that a related archived topic exists, and ask whether
to restore it or continue fresh. Do not ingest or compile into archived topics
through auto-classification.

### Resolve HUB and wiki

<!-- hub-prelude:start -->
**Resolve the wiki.** A few file reads, not a search: never `find`, Explore, or scan the filesystem. Full protocol and edge cases: `skills/knowmade/references/hub-resolution.md`.
1. HUB: `$LLM_WIKI_HUB` or read `$HOME/.config/llm-wiki/config.json` and use `hub_path` (expand only a leading `~`); `resolved_path` is a fallback cache; with no config use `$HOME/wiki`. If the hub is stat-able but reads fail with `Operation not permitted`, stop and report the launcher permission denial.
2. Wiki, first match: `--local` → `.wiki/`; `--wiki <name>` → `HUB/wikis.json` entry (`wikis.local.json` path first; stale path → `HUB/topics/<name>`); `.wiki/` in CWD; else HUB. Reject archived entries (`status: archived`, `topics/.archive/`) unless the command takes `--include-archived`.
3. `<slug>` and `<slug>-PRIVATE` are sibling topics in one hub: read private freely, write under the Write-Guard in `skills/knowmade/references/knowmade-profile.md` § Private Data Boundary.
<!-- hub-prelude:end -->
4. Read `<wiki>/_index.md` to verify. If missing and `--new-topic` is set → create the topic wiki (see below). If missing and no `--new-topic` → stop with "No wiki found. Use `--new-topic` to create one, or run `/knowmade:wiki init <topic>` first."

**When `--new-topic` is set**, override the standard resolution:
1. Derive a slug from the topic: lowercase, hyphens, no special chars, max 40 chars
2. If HUB doesn't exist, create it (wikis.json + _index.md + log.md + topics/)
3. Create the new topic wiki at `HUB/topics/<slug>/` following the full init protocol (directory structure, .obsidian/, empty _index.md files, config.md, log.md)
4. Register in `HUB/wikis.json` using a portable relative path (`topics/<slug>`) and update hub `_index.md`
5. Target this new wiki for all research that follows

**When `--new-topic` is NOT set**, the standard prelude resolution applies, with one deviation: step 4 (fallback to HUB) becomes "ask which topic wiki to target" — research against an empty hub doesn't make sense.

### Input Detection: Topic vs Question vs Thesis

Before starting research, detect the input mode:

- **Thesis** (explicit): `--mode thesis` flag is set → enter Thesis Research Mode (see below).
- **Thesis** (auto-detected): input contains "prove that", "is it true that", "verify", "test the claim", "test the hypothesis" → enter Thesis Research Mode. The claim text is the input minus the signal words.
- **Question**: starts with what/why/how/when/where/who, contains a "?", or is phrased as a goal ("how to...", "what makes...", "why does...") → enter Question Research Mode.
- **Topic**: a noun/phrase naming a subject area → proceed with standard research protocol.

**If topic** → proceed with standard research protocol below.

**If question** → enter Question Research Mode:


### Protocol map (read lazily)

The procedural sections this command refers to as "below" (Specialist selection, Minimum Time Research, Question Research Mode, Thesis Research Mode, Plan Mode, Research Protocol Phases 1-5, Agent Prompt Template, Gap-Closing Offer) moved verbatim into two reference files. Read only what the invocation needs, at the step that needs it:

| Step | Read |
|------|------|
| After the target wiki is resolved, before decomposing the question | `skills/knowmade/references/research-protocol.md` § Specialist selection |
| `--min-time` | `skills/knowmade/references/research-modes.md` § Minimum Time Research |
| Question input | `skills/knowmade/references/research-modes.md` § Question Research Mode |
| Thesis input (`--mode thesis` or auto-detected) | `skills/knowmade/references/research-modes.md` § Thesis Research Mode |
| `--plan` | `skills/knowmade/references/research-modes.md` § Plan Mode |
| Phase 1 (existing knowledge check) | `skills/knowmade/references/research-protocol.md` § Phase 1 |
| Phases 2 and 2b (agent swarm, credibility review) | `skills/knowmade/references/research-protocol.md` § Phase 2, § Agent Prompt Template, § Phase 2b |
| Phase 3 (stage and ingest, inbox-first) | `skills/knowmade/references/research-protocol.md` § Phase 3 |
| Phases 4 and 5 (compile, report and log), Gap-Closing Offer | `skills/knowmade/references/research-protocol.md` § Phase 4, § Phase 5, § Gap-Closing Offer |

A mode section modifies the phases; it does not replace them, so read the matching phase sections as well. Templates, scoring formulas and session schemas live in one small file per phase (agents, credibility, staging, rounds, sessions, plan); each section names the one it needs at the step that needs it.
