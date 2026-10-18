# Research Plan (`--plan`)

The plan-mode extension of `.research-session.json`, path resume and file ownership. Read only with `--plan` (`research-modes.md` § Plan Mode).

### Why

The `--plan` flag decomposes a research topic into 3-5 independent paths that execute in parallel. The plan is stored in the session registry (`research-sessions.md`) so it persists across crashes and can be resumed path-by-path. The plan is ephemeral — it lives only in `.research-session.json` and is deleted on completion.

The architectural insight: parallel ingest is safe (each path stages unique inbox captures and writes unique raw files with path-prefixed slugs), but parallel compilation is not (multiple agents updating the same `_index.md` and creating overlapping articles). So the pipeline splits: search + ingest run in parallel across paths, then a single sequential compilation pass runs after all paths complete. This gives the compiler full visibility across all paths for better cross-referencing.

### Schema Extension

When `mode: "plan"` is set in `.research-session.json`, the following fields are added:

| Field | Type | Purpose |
|-------|------|---------|
| `mode` | `"plan"` | Distinguishes plan-mode sessions from single-path (`"single"`) |
| `paths` | array | Research paths with scope and execution status |
| `paths[].name` | string | Human-readable path name |
| `paths[].focus` | string | One-line description of what this path investigates |
| `paths[].search_angles` | string[] | 2-3 specific search strategies for this path |
| `paths[].status` | enum | `pending`, `in_progress`, `completed`, `failed` |
| `paths[].sources_ingested` | number | Sources ingested by this path (updated on completion) |
| `paths[].agent_mode` | string | `standard`, `deep`, or `retardmax` (inherited from session flags) |

### Example

```json
{
  "session_id": "2026-04-16-143022",
  "topic": "quantum computing threats to Bitcoin",
  "mode": "plan",
  "start_time": "2026-04-16T14:30:22Z",
  "paths": [
    {
      "name": "Cryptographic foundations",
      "focus": "Shor's algorithm vs ECDLP, key sizes, quantum gate counts",
      "search_angles": ["shor algorithm elliptic curve", "quantum gate count ECDLP", "NIST post-quantum standards"],
      "status": "completed",
      "sources_ingested": 4,
      "agent_mode": "standard"
    },
    {
      "name": "Hardware timeline",
      "focus": "IBM/Google roadmaps, logical qubit milestones, error correction overhead",
      "search_angles": ["IBM quantum roadmap 2026", "logical qubit error correction overhead", "Google Willow scaling"],
      "status": "completed",
      "sources_ingested": 3,
      "agent_mode": "standard"
    },
    {
      "name": "Migration proposals",
      "focus": "BIP proposals, hash-based signatures, precommitment schemes",
      "search_angles": ["bitcoin post-quantum BIP", "hash-based signature bitcoin", "PQC precommitment soft fork"],
      "status": "in_progress",
      "sources_ingested": 0,
      "agent_mode": "standard"
    }
  ],
  "current_round": 1,
  "rounds_completed": [],
  "cumulative_sources": 7,
  "cumulative_articles": 0,
  "status": "in_progress"
}
```

### Resume Protocol (plan mode)

On resume, check `paths[].status`:

- **All `completed`** → skip to compilation (all sources are ingested, just need to compile)
- **Some `pending`** → re-launch only pending paths (completed paths are not repeated)
- **Some `in_progress`** → treat as `pending` (agent died mid-execution; raw files from partial execution are fine — deduplication handles any overlap. Captures the dead agent left in `inbox/` stay pending; the re-launched path processes or re-stages them, deduplicating by `source` URL)
- **Some `failed`** → ask user: "Path '<name>' failed. Retry or skip?"

### File Ownership

Each path prefixes its inbox capture and raw file slugs with the path index to prevent filename collisions between parallel agents:

```
inbox/YYYY-MM-DD-p<N>-<agent-slug>-<source-slug>.md      # staged by the path's sub-agents
raw/<type>/YYYY-MM-DD-p<N>-<source-slug>.md              # written by the path-agent after review
```

Where `N` is the 1-indexed path number. Example: `inbox/2026-04-16-p2-technical-ibm-quantum-roadmap.md` is staged by path 2's technical agent and becomes `raw/articles/2026-04-16-p2-ibm-quantum-roadmap.md` once the path-agent processes it (the capture then lives in `inbox/.processed/`).

Index updates are skipped during parallel ingest. The Derived Index Protocol (`indexing.md`) rebuilds them on the next read. This is safe because indexes are derived caches, not source of truth.

### Interaction with Other Flags

| Flag | Behavior with `--plan` |
|------|----------------------|
| `--deep` | Each path-agent launches 8 sub-agents instead of 5 |
| `--retardmax` | Each path-agent launches 10 sub-agents, lower quality threshold |
| `--sources <N>` | Target N sources per path (not total) |
| `--min-time` | Round 1 executes the full plan; subsequent rounds generate new plans targeting remaining gaps |
| `--mode thesis` | Plan decomposes the thesis into evidence paths (supporting, opposing, mechanistic, etc.) |
| `--project <slug>` | All paths tag outputs with the same project |
| `--new-topic` | Creates the topic wiki first, then generates and executes the plan |
