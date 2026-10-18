# Research Sessions (session state)

The crash-recovery session files and the durable provenance trail. Read when a `--min-time` or thesis session starts, ends or resumes (`research-modes.md` § Minimum Time Research). The `--plan` extension of the session file is in [`research-plan.md`](research-plan.md).

### Why

Persistent state for multi-round research and thesis sessions, enabling crash recovery and round-to-round continuity. Without this, a crashed `--min-time` session loses all round state and the user has to start over. The file is ephemeral (never committed to git, never indexed), cheap to lose (worst case: user is asked "continue or start fresh?"), but valuable to have.

### Research Session Schema (.research-session.json)

```json
{
  "session_id": "2026-04-06-143022",
  "topic": "research topic",
  "start_time": "2026-04-06T14:30:22Z",
  "min_time_budget": "2h",
  "specialists": [
    {
      "name": "research-methodologist",
      "version": "0.1.0",
      "sha256": "<content-sha256>"
    }
  ],
  "current_round": 2,
  "rounds_completed": [
    {
      "round": 1,
      "start_time": "2026-04-06T14:30:22Z",
      "end_time": "2026-04-06T15:02:45Z",
      "sources_ingested": 5,
      "articles_compiled": 3,
      "gaps": ["gap1 description", "gap2 description"],
      "progress_score": 65,
      "reflection_notes": "Initial broad coverage complete. Gap1 is highest priority."
    }
  ],
  "cumulative_sources": 5,
  "cumulative_articles": 3,
  "status": "in_progress"
}
```

### Thesis Session Schema (.thesis-session.json)

```json
{
  "session_id": "2026-04-06-143022",
  "thesis": "claim statement",
  "specialists": [],
  "current_round": 2,
  "rounds_completed": [
    {
      "round": 1,
      "evidence_for": 4,
      "evidence_against": 2,
      "verdict_direction": "partially-supported",
      "next_round_focus": "opposing"
    }
  ],
  "status": "in_progress"
}
```

### Durable Provenance Files

The session registry files above are for **live recovery**. They are not the
best long-term provenance format because they are overwritten in place and
deleted on normal completion.

Research, thesis, audit, and related long-running wiki workflows should also
maintain two durable provenance artifacts in the wiki root:

- `.session-events.jsonl` — append-only event log
- `.session-checkpoint.json` — latest replayable summary

These files persist after normal completion and are what the audit layer uses
to classify provenance as `replayable` instead of merely `partial`.

### Event Log Schema (.session-events.jsonl)

Each line is one JSON object. Append only; never rewrite prior entries.

```json
{"ts":"2026-04-29T12:00:00Z","command":"research","phase":"start","event":"research_started","session_id":"2026-04-29-120000","topic":"cerebral amyloid angiopathy","mode":"single","min_time_budget":"2h","specialists":[{"name":"research-methodologist","version":"0.1.0","sha256":"abc123..."}]}
{"ts":"2026-04-29T12:38:00Z","command":"research","phase":"round","event":"research_round_completed","session_id":"2026-04-29-120000","round":1,"sources_ingested":5,"articles_compiled":3,"progress_score":65}
{"ts":"2026-04-29T12:42:00Z","command":"research","phase":"reflection","event":"research_reflection_completed","session_id":"2026-04-29-120000","round":1,"top_gaps":["gap1","gap2","gap3"]}
{"ts":"2026-04-29T14:05:00Z","command":"research","phase":"finish","event":"research_completed","session_id":"2026-04-29-120000","rounds_completed":3,"cumulative_sources":14,"cumulative_articles":9}
```

Recommended fields:

| Field | Type | Purpose |
|-------|------|---------|
| `ts` | string | ISO 8601 timestamp |
| `command` | string | `research`, `audit`, `output`, `refresh`, etc. |
| `phase` | string | `start`, `round`, `reflection`, `scan`, `finish`, etc. |
| `event` | string | Stable event name |
| `session_id` | string | Correlates all entries from one run |
| `topic` / `thesis` / `scope` | string | Human-readable target |
| `round` | number | Research/thesis round when applicable |
| `sources_ingested` | number | Per-round or cumulative count when relevant |
| `articles_compiled` | number | Per-round or cumulative count when relevant |
| `progress_score` | number | Round quality signal when relevant |
| `artifacts` | array | Paths written in that step |
| `specialists` | array | Selected specialist name, version, and content hash; empty when none |
| `notes` | string | Short freeform summary, optional |

### Checkpoint Schema (.session-checkpoint.json)

The checkpoint is the latest compact summary of the most recent important run.
Rewrite atomically after each meaningful milestone.

```json
{
  "updated_at": "2026-04-29T14:05:00Z",
  "command": "research",
  "session_id": "2026-04-29-120000",
  "status": "completed",
  "topic": "cerebral amyloid angiopathy",
  "current_round": 3,
  "specialists": [
    {
      "name": "research-methodologist",
      "version": "0.1.0",
      "sha256": "abc123..."
    }
  ],
  "summary": {
    "cumulative_sources": 14,
    "cumulative_articles": 9,
    "last_progress_score": 82,
    "top_open_gaps": ["gap4", "gap5"]
  },
  "artifacts": [
    {
      "path": "output/2026-04-29-caa-summary.md",
      "sha256": "abc123..."
    }
  ]
}
```

Recommended fields:

| Field | Type | Purpose |
|-------|------|---------|
| `updated_at` | string | ISO 8601 timestamp |
| `command` | string | Command that owns the checkpoint |
| `session_id` | string | Correlates with event log and ephemeral session file |
| `status` | string | `in_progress`, `completed`, `interrupted`, `failed` |
| `topic` / `thesis` / `scope` | string | Human-readable target |
| `current_round` | number | Most recent completed round, when applicable |
| `specialists` | array | Selected specialist name, version, and content hash; empty when none |
| `summary` | object | Compact state for resume briefings |
| `artifacts` | array | Written artifact paths and hashes, when available |

### Lifecycle

| Event | Action |
|-------|--------|
| --min-time research starts | Create `.research-session.json`; append `research_started`; write `.session-checkpoint.json` |
| Round N completes | Update `.research-session.json`; append round event(s); refresh checkpoint |
| Research completes normally | Append completion event; refresh checkpoint; delete `.research-session.json` |
| Session interrupted | `.research-session.json` persists with `status: "in_progress"`; durable files remain |
| Next invocation detects file | Ask: continue or start fresh? |
| File > 7 days old | Structural Guardian warns about stale session |

### Resume Protocol

1. Detect `.research-session.json` or `.thesis-session.json` in wiki root
2. If found, read it first and extract the last completed round
3. If no active session exists, read `.session-checkpoint.json` and the tail of `.session-events.jsonl` for the latest durable context
4. Ask user: "Found interrupted session (Round N, M sources). Continue or start fresh?"
5. If continue: use round N's gaps/reflection as starting point for round N+1
6. If fresh: delete only the ephemeral session file, preserve durable provenance

### Notes

- Session files are ephemeral — they are for crash recovery only
- `.session-events.jsonl` and `.session-checkpoint.json` are durable provenance
  artifacts and should normally persist after completion
- Never include in index counts or structural health checks
- One session per wiki at a time (new session overwrites old)
