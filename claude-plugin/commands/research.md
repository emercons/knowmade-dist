---
description: "Deep multi-agent research on a topic, question, practical problem, or thesis. Launches parallel agents to search the web, stages sources through inbox-first ingest, and compiles them into active wiki articles. Thesis mode provides for/against evidence framing with a verdict."
argument-hint: "<topic|question> [--plan] [--mode thesis \"<claim>\"] [--new-topic] [--sources <N>] [--deep] [--retardmax] [--min-time <duration>] [--specialist <name>] [--no-specialists] [--wiki <name>] [--local] [--project <slug>] [--include-archived]"
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(ls:*), Bash(wc:*), Bash(date:*), Bash(mkdir:*), Bash(python3:*), Bash(scripts/llm-wiki:*), Bash(${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki:*), WebFetch, WebSearch, Agent
---

## Your task

Conduct deep research on the topic in $ARGUMENTS. This is an automated pipeline: search → stage in `inbox/` → ingest into `raw/` → compile.

Research is inbox-first, like every other Knowmade intake path. No research agent writes to `raw/` directly: every captured source is first staged as an `inbox/` capture by the agent that found it, and the batch is then processed into `raw/` by the coordinator using the same protocol `/knowmade:ingest` uses (Phase 3 of the research protocol names its sections). The research-specific staging and batch rules live in Phase 3.

### Parse $ARGUMENTS

- **topic/question**: The research subject — everything that is not a flag. Auto-detected as topic vs question vs thesis (see Input Detection below).
- **--mode thesis "<claim>"**: Thesis mode: the claim is the scope constraint, agents weigh evidence for and against, a verdict is rendered (thesis branch below).
- **--new-topic**: Create a new topic wiki from the topic name and start researching into it. Works from any directory (branch below).
- **--sources <N>**: Target sources PER ROUND (default: 5, max: 20)
- **--deep**: 8 parallel agents with broader search angles
- **--retardmax**: 10 agents, skip planning, ingest aggressively
- **--min-time <duration>**: Keep running research rounds until this duration is reached (`30m`, `1h`, `2h`, `4h`; default: single round; branch below).
- **--wiki <name>**: Target a specific existing topic wiki
- **--local**: Use project-local `.wiki/`
- **--plan**: Decompose the topic into 3-5 independent research paths, confirm the plan, then run all paths in parallel (branch below).
- **--project <slug>**: Tag all new outputs with this project (branch below).
- **--include-archived**: Explicitly allow researching into an archived target wiki (branch below).
- **--specialist <name>**: Explicitly apply an enabled personal specialist;
  repeat up to three times. For project-local `.wiki/`, explicit selection is
  required because v1 has no persisted local-project allowlist.
- **--no-specialists**: Run the baseline research workflow without reading
  specialist package bodies. Mutually exclusive with `--specialist`.

### Resolve HUB and wiki

<!-- hub-prelude:start -->
**Resolve the wiki.** A few file reads, not a search: never `find`, Explore, or scan the filesystem. Full protocol and edge cases: `skills/knowmade/references/hub-resolution.md`.
1. HUB: `$LLM_WIKI_HUB` or read `$HOME/.config/llm-wiki/config.json` and use `hub_path` (expand only a leading `~`); `resolved_path` is a fallback cache; with no config use `$HOME/wiki`. If the hub is stat-able but reads fail with `Operation not permitted`, stop and report the launcher permission denial.
2. Wiki, first match: `--local` → `.wiki/`; `--wiki <name>` → `HUB/wikis.json` entry (`wikis.local.json` path first; stale path → `HUB/topics/<name>`); `.wiki/` in CWD; else HUB. Reject archived entries (`status: archived`, `topics/.archive/`) unless the command takes `--include-archived`.
3. `<slug>` and `<slug>-PRIVATE` are sibling topics in one hub: read private freely, write under the Write-Guard in `skills/knowmade/references/private-boundary.md`.
<!-- hub-prelude:end -->
4. Read `<wiki>/_index.md` to verify. If missing and `--new-topic` is set → create the topic wiki (branch below). If missing and no `--new-topic` → stop with "No wiki found. Use `--new-topic` to create one, or run `/knowmade:wiki init <topic>` first."

When `--new-topic` is NOT set, the standard prelude resolution applies, with one deviation: step 4 (fallback to HUB) becomes "ask which topic wiki to target" — research against an empty hub doesn't make sense.

### Input Detection: Topic vs Question vs Thesis

Before starting research, detect the input mode:

- **Thesis** (explicit): `--mode thesis` flag is set → thesis branch below.
- **Thesis** (auto-detected): input contains "prove that", "is it true that", "verify", "test the claim", "test the hypothesis" → thesis branch. The claim text is the input minus the signal words.
- **Question**: starts with what/why/how/when/where/who, contains a "?", or is phrased as a goal ("how to...", "what makes...", "why does...") → question branch below.
- **Topic**: a noun/phrase naming a subject area → the standard research protocol.

### Protocol

Read `skills/knowmade/references/research-protocol.md` after the target wiki is resolved: Specialist selection first (before decomposing the question), then Phases 1-5 (existing knowledge check; agent swarm and credibility review; stage and ingest, inbox-first; compile; report and log), the Agent Prompt Template and the Gap-Closing Offer. A mode section modifies the phases; it does not replace them. Templates, scoring formulas and session schemas live in one small file per phase, each named by the protocol at the step that needs it.

### If --new-topic

Derive a slug from the topic (lowercase, hyphens, no special characters, at most 40 characters; "quantum error correction" → `quantum-error-correction`) and run `"${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki" init --topic <slug> --description "<topic>"` (`scripts/llm-wiki` in a source checkout) (it registers the portable path `topics/<slug>`; pass `--hub <path>` when no hub is configured, and ask the user where to create it rather than assuming `~/wiki`). If the CLI cannot run, follow `skills/knowmade/references/wiki-setup.md` § Init manual fallback. Target the new wiki for all research that follows.

### If --project <slug>

Verify the project exists and fail early otherwise; where outputs and frontmatter go, and why there is no ambient focus, is in `skills/knowmade/references/research-setup.md` § Project scope.

### If --include-archived is given, or the target or a related topic is archived

Stop and ask the user to restore the topic or rerun with `--include-archived`, and never ingest or compile into an archived topic through auto-classification; the full rule, including the overlap warning when no wiki is named, is in `skills/knowmade/references/research-setup.md` § Archive awareness.

### If the target wiki is a private topic, or has a -PRIVATE sibling

Read the private goals and high-priority inventory to prioritize research paths (never as evidence); the read rules and the public-topic write-guard are in `skills/knowmade/references/research-setup.md` § Private goals awareness.

### If --min-time <duration>

Read `skills/knowmade/references/research-modes.md` § Minimum Time Research: the flag, rounds, session state and termination.

### If the input is a question

Read `skills/knowmade/references/research-modes.md` § Question Research Mode: decompose into sub-questions, one agent per sub-question, a structured compile and a playbook.

### If the input is a thesis (--mode thesis, or a signal phrase above)

Read `skills/knowmade/references/research-modes.md` § Thesis Research Mode: claim decomposition, for/against agents, evidence strength and a verdict.

### If --plan

Read `skills/knowmade/references/research-modes.md` § Plan Mode: the plan phase between Phases 1 and 2, parallel path dispatch, cross-path compilation and the plan report.
