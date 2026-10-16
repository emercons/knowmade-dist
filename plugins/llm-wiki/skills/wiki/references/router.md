# Fuzzy Router

The one routing table for free-text requests. `commands/wiki.md` and the
`wiki` skill both read this file; do not keep a second copy.

`wiki x` is the `x` workflow (`/wiki:x` where slash commands exist). Pass the
user's text on minus the signal words, plus any `--wiki`, `--local`,
`--project` flags.

## Table

Scan top to bottom; the first row whose signals match wins. Signals are
case-insensitive phrases or whole words, never substrings.

| # | Intent | Signals | Route |
|---|--------|---------|-------|
| 1 | Retract | "retract", "remove source", "remove this everywhere", "forget this data", "wipe references" | `wiki retract`, before loading wiki context |
| 2 | Hub Inbox Dispatch | "dispatch/triage/route hub inbox", "sort global inbox", "разбери общий инбокс", "распредели материалы по вики" | Hub Inbox Dispatch workflow (`references/ingestion.md`) |
| 3 | External Adapter Route | an action verb (edit, revise, update, proofread, replace, sync, publish, validate, analyze) plus an external URL | `wiki adapter`: `adapter route`, read the returned guide; no-match falls through |
| 4 | Skill Factory Adapter | starts with `wiki skill-factory` or `skill-factory`, or "use the skill-factory adapter" | `wiki adapter` named `skill-factory`; doctor, read its guide, keep candidates disabled |
| 5 | Private Adapter | "private/registered adapter", "adapter list/doctor/run", "use the <name> adapter"; not `ingest-collection --adapter` | `wiki adapter` |
| 6 | Personal Specialist | "specialist skill/agent", "expert reviewer/lens", "personal/local skill", "specialist list/show/enable/suggest/apply" | `wiki specialist` |
| 7 | Project Knowledge Checkpoint | "knowledge checkpoint", "knowledge handoff", "export project knowledge", "dump wiki knowledge into the repo", "checkpoint this project", "refresh/verify/import checkpoint" | `wiki checkpoint` (preview by default; privacy review plus seal/verify) |
| 8 | Collection Ingest | "import/mirror wiki", "bulk ingest", "ingest/import collection/repo"; a URL or path with `dump.xml(.bz2/.gz)`, `api.php`, `MediaWiki`, or `github.com/*/*` plus "all", "repo", "docs", "collection" | `wiki ingest-collection` |
| 9 | Collect | "collect", "catalog", "curate", "gather examples", "find all", "make a list of", "inventory all", "find and inventory" | `wiki collect` |
| 10 | Portfolio | "portfolio", "all my ideas and projects", "across every wiki", hub-wide "what should I work on next" | `wiki portfolio` (read-only) |
| 11 | Idea (promote) | "turn this idea into a project", "promote this idea", "approve the brief", "use the narrow/minimal version" plus project/build/ship | `wiki idea promote` |
| 12 | Idea (shape) | "shape this idea", "poke holes in this", "smallest useful version", "make this approvable", "scope this idea" | `wiki idea shape` |
| 13 | Idea (develop) | "research/develop/validate this idea", "does this idea hold water", "weakest assumptions" of an Idea | `wiki idea develop` |
| 14 | Idea (list/show) | "my ideas", "list/show ideas", "what ideas do I have", "show the <name> idea", "closest to becoming projects" | `wiki idea list` or `show` |
| 15 | Idea (capture) | "I have an idea", "idea for", "save this idea/thought", "park this thought" | `wiki idea new` |
| 16 | Project (new) | "new project", "start a project", "create/make a project" (+ slug and goal) | `wiki project new <slug> "goal"` |
| 17 | Project (list/show) | "list/show projects", "my projects", "show/open project X", "what's in project X" | `wiki project list` or `show <slug>` |
| 18 | Project (archive) | "archive project", "I'm done with project", "close project" | `wiki project archive <slug>` |
| 19 | Topic Archive | "archive/restore wiki", "archive/restore topic", "list archived wikis", "show archived topics" | `wiki archive` |
| 20 | Inventory | "inventory", "ingest/source queue", "candidate list", "backlog", "track this", "what should become inventory" | `wiki inventory` |
| 21 | Dataset | "dataset", "large data", "too big for the wiki", "index this data", "data registry", "external data" | `wiki dataset` |
| 22 | Session | "session capture/digest/hooks", "capture this session", "rehydrate", "list/show/promote session" | `wiki session` |
| 23 | Lessons Learned | `ll` as a whole word at the very start, "learn this/that", "lessons learned", "what did we learn", "session takeaways" | `wiki ll` with the topic hint |
| 24 | Feedback | "feedback", "capture correction", "that worked", "promote feedback" | `wiki feedback` |
| 25 | Ingest | a URL (`http://`, `https://`), a file path (`/`, `~/`), "add", "save", "ingest", "read this", "grab this" | `wiki ingest` with the URL/path/text |
| 26 | Resume | "where was I", "pick up where", "resume", "catch me up", "what was I working on", "continue where I left off" | `wiki query --resume` |
| 27 | Audit | "audit", "can I trust", "verify this output/report", "fact-check this artifact", "provenance", "drift report" | `wiki audit` |
| 28 | Query | starts with what/why/how/when/where/who, contains "?", "tell me about", "explain", "what do we know about" | `wiki query` with the question |
| 29 | Research | "research", "find out about", "look into", "deep dive", "investigate", "continue the research" | `wiki research` with the topic |
| 30 | Thesis | "prove that", "is it true that", "verify that <claim>", "test the claim", "test the hypothesis" | `wiki research --mode thesis "<claim>"` |
| 31 | Compile | "compile", "process sources", "synthesize", "continue compiling" | `wiki compile` |
| 32 | Lint | "check health", "fix wiki", "broken", "cleanup" | `wiki lint` |
| 33 | Librarian | "librarian", "quality scan", "article quality", "review articles", "quality report", "stale articles" | `wiki librarian` |
| 34 | Refresh | "freshness", "still current", "up to date", "outdated", "refresh" | `wiki refresh` |
| 35 | Output | "write a summary", "generate a report", "slides", "write up", "draft a brief/article/report from the wiki" | `wiki output` |
| 36 | Assess | "compare to", "assess", "gap analysis" | `wiki assess` |
| 37 | Plan | "plan for", "implementation plan", "architecture for" | `wiki plan` |

## Precedence

- Rows 1 to 24 are specific workflows and outrank generic Ingest, Query,
  Research, Output and Resume; row order is the tie-break.
- Project (new) outranks Output: "create a project for X" is a Project. Bare
  "create a" and "write a" are not Output signals.
- Explicit Idea signals outrank generic inventory, research, output and
  Project (new): "turn this Idea into a Project" is promotion, not
  `project new`. Hub-wide or Ideas-and-Projects scope is Portfolio.
- Checkpoint outranks Thesis ("verify checkpoint"), Refresh ("refresh
  checkpoint"), Output, Project and Collection Ingest. Writing needs
  `--apply`, every bundle is sealed and verified, and no request disables the
  privacy scan or implies a blanket override.
- Inventory and Dataset outrank generic questions and URLs ("what should
  become inventory?", "track this URL as a candidate"). Collect outranks plain
  inventory when many objects must be discovered first.
- External action intent outranks URL ingestion only when `adapter route
  --intent <effect> --resource <url> --json` returns one healthy match; read
  the adapter-owned guide. `ambiguous` and `unavailable` fail closed. A URL
  without a concrete action authorizes nothing (`references/adapters.md`).
- `wiki skill-factory` is an explicit named-adapter call, never ambient; its
  output is a disabled candidate, never installed, enabled or published.
- Project archive outranks Topic archive when "project" is present. Topic
  archive needs "wiki" or "topic". "Wayback/message/dataset archive" goes to
  ingest-collection, dataset or inventory.
- "continue" alone is not a Resume signal. "Continue the research / compiling
  / the ingest" goes to that workflow. Bare "continue" with no referent is
  low confidence: offer Resume, Research and Compile.
- `ll` routes only as a whole word at the start of the request, never as a
  substring or mid-sentence token ("I'll", "all", "will").
- `--project <slug>` must be explicit; "work on project X" alone means
  `project show`.

## Confidence

- High (one strong signal: a URL, a "?", an exact keyword): route directly
  and say so, e.g. "Detected: **ingest** (found URL)".
- Low (several rows fit, or no clear signal): offer the top 2 or 3 as a
  numbered menu and wait; never guess.
- No match: show wiki status and the subcommands.

## Negative controls

| Request | Routes to | Never to |
|---------|-----------|----------|
| "create a project for the tax archive" | Project (new) | Output |
| "continue the research on lightning fees" | Research | Resume |
| "continue" (no referent) | menu: Resume, Research, Compile | silent Resume |
| "I'll archive project foo" | Project (archive) | Lessons Learned |
| "verify checkpoint" | Checkpoint | Thesis |
| "refresh checkpoint" | Checkpoint | Refresh |
| "show project alpha" | Project (show) | Query, Ingest |
| "show me the session digest list" | Session | Query |
| "what makes a good SKILL.md description?" | Query | any adapter |
| "write a python function that parses CSV" | no wiki route | Output |
| "testing" | menu | any single command |
