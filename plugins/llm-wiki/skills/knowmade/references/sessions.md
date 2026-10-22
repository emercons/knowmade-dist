# Session Context Capture

Use this reference for `session`, `session capture`, `rehydrate`, "look at the
last session", automated hook capture, and promotion of session learnings into a
topic wiki.

## Purpose

Session capture preserves agent-session context without polluting curated topic
wikis. The session layer records redacted harness metadata and markdown digests
under the hub-level operational directory `HUB/.sessions/`. Topic wikis receive
session knowledge only through explicit promotion into `raw/notes/`. That direct
write is the documented session-distillate exception to inbox-first intake
(`knowmade-profile.md` § Inbox-First Source Lifecycle); the private write-guard
still applies.

This layer is separate from existing research-session files:

| Layer | Path | Meaning |
|---|---|---|
| Research crash state | `.research-session.json`, `.thesis-session.json` | In-progress wiki research/thesis runs |
| Durable research provenance | `.session-events.jsonl`, `.session-checkpoint.json` | Replayable provenance for wiki workflows |
| Harness sessions | `HUB/.sessions/` or `.wiki/.sessions/` | Cross-runtime Codex/Claude/OpenCode/Gemini session context |

## Storage Layout

```text
HUB/.sessions/
├── config.json
├── registry.jsonl
├── .lock                        (transient; held only while a command writes)
├── state/<harness>/<session_id>.json
├── queue/YYYY-MM-DD.jsonl
├── digests/YYYY/MM/<harness>-<session_id>.md
├── feedback/
│   ├── candidates.jsonl
│   └── status.json
└── indexes/
    ├── by-cwd.json
    ├── by-topic.json
    ├── feedback.json
    └── sessions.json
```

Use the same layout under `.wiki/.sessions/` for local project wikis.

- `config.json` controls automation and rehydration. If it is absent, capture defaults to `balanced`; `enabled: false` is the opt-out switch.
  If it exists but cannot be read (invalid JSON, not an object), capture fails
  closed: the hook warns on stderr and records nothing, `status` reports the
  error, and `llm-wiki-session enable` rewrites the file (keeping the bad one as
  `config.json.corrupt`).
- `registry.jsonl` is append-only lifecycle metadata such as `session_seen` and
  `digest_written`.
- `queue/*.jsonl` stores small redacted hook events. Do not store full prompts,
  tool outputs, or transcript bodies by default. Input-shaped keys
  (`tool_input`, `arguments`, `input`, `args`, `command`, `old_string`,
  `new_string`, and any `content` nested in them) are replaced by
  `{"sha256": ..., "length": ...}` summaries; only `tool_name`, `file_path`,
  and `file_paths` stay in clear so rehydration can name the touched files.
- `state/` is the latest per-session machine state. File names are a
  filesystem-safe form of the id: an id made only of letters, digits, `_`, `.`,
  `+` and `-` is used as is; anything else (`:` and `@` are illegal or special on
  Windows, as are device names such as `CON`) is replaced by `-` and suffixed with
  `-<first 8 hex of the id's sha256>` so `a:b` and `a-b` never share a file. The
  real id is always kept inside the JSON (`native_session_id`).
- `digests/` contains human/LLM-readable markdown checkpoints with YAML
  frontmatter.
- `feedback/` stores redacted, reviewable user-feedback candidates such as
  corrections, preferences, approvals, and plan acceptance.
- `indexes/` are derived caches rebuilt from `state/` and feedback candidates.
  They are rebuilt when a digest is written, on promotion, and by
  `llm-wiki-session index`; ordinary hook events do not touch them, so a hook
  between checkpoints costs one state write, not a rewrite of every index.

`.sessions/` is hidden because it is operational, cross-topic, and potentially
private. It should not be compiled as ordinary topic content. A future generated
`HUB/_sessions.md` can expose a human-friendly index, but the canonical store is
`.sessions/`.

## Config Modes

Default mode is `balanced`, even before a config file exists:

```json
{
  "schema_version": 1,
  "enabled": true,
  "mode": "balanced",
  "auto_capture": {
    "tool_events": 50,
    "pre_compact": true,
    "post_compact": true,
    "session_end": true,
    "stop": true
  },
  "rehydrate": {
    "session_start": true,
    "user_prompt": true,
    "strict": false
  },
  "raw_transcripts": false,
  "privacy": "redacted",
  "feedback": {
    "enabled": true,
    "capture_approvals": true,
    "min_confidence": "medium"
  }
}
```

Modes:

| Mode | Capture | Rehydrate | Use when |
|---|---|---|---|
| `off` | none | none | Disabled / user opt-out |
| `capture-only` | automatic checkpoints | no injected context | Maximum privacy/least surprise |
| `balanced` | automatic checkpoints | SessionStart/UserPromptSubmit soft context | Recommended default |
| `aggressive` | more frequent checkpoints | soft context | Long, high-context work |

## Capture Triggers

Hooks should write deterministic checkpoints for:

1. every configured number of observed tool events, default 50;
2. pre-compaction and post-compaction;
3. stop/session-end events;
4. manual `session capture` requests.

Manual captures without `--session-id` get a unique `manual-<timestamp>-<random>` id,
so two captures in the same second are two sessions.

"Observed tool events" means events the current harness adapter exposes. Codex,
Claude, OpenCode, and Gemini have different hook coverage, so do not promise an
exact total count across all tool classes.

## Digest Schema

Session digest files are markdown with YAML frontmatter:

```yaml
title: "Session Digest: codex:abc123"
type: session-digest
schema_version: 1
harness: "codex"
native_session_id: "abc123"
llm_wiki_session_id: "codex:abc123"
cwd: "/path/to/project"
git_remote: "https://github.com/org/repo.git"
git_branch: "feature/example"
transcript_path: "/path/to/transcript.jsonl"
started_at: "2026-06-13T17:00:00Z"
last_seen_at: "2026-06-13T18:10:00Z"
tool_event_count: 50
event_count: 61
capture_trigger: "tool-count-50"
topics: []
topic_candidates: []
privacy: "redacted"
raw_transcripts: false
promoted_to: []
summary: "Automated checkpoint for codex:abc123."
```

The body should include session identity, summary, manual notes if any, recent
observed events, distillation notes, and open loops. It should not include full
transcript text by default. `git_remote` is stored with URL userinfo stripped
(`https://user:token@host/x.git` becomes `https://host/x.git`) in state,
digests, and feedback candidates; the raw remote is never written.

## Hook Adapters

Hook adapters (the `llm-wiki-session hook` contract, what the six Codex hooks do,
Codex hook packaging, and the Claude, OpenCode and Gemini adapters) are in
[session-hooks.md](session-hooks.md). Capture is opt-in: nothing is recorded
until `session enable`.

## Rehydration

Rehydration is a compact context block, not a bulk transcript paste:

```text
llm-wiki session context: review these distilled session digests before continuing.
- codex:abc123 — Automated checkpoint summary. Digest: /abs/path/HUB/.sessions/digests/2026/06/codex-abc123.md
Do not treat session digests as canonical topic knowledge until promoted into a topic wiki.
```

Soft rehydration can be injected on `SessionStart` or `UserPromptSubmit` in
balanced/aggressive modes. Manual rehydration should be available with:

```bash
llm-wiki-session rehydrate --cwd "$PWD"
llm-wiki-session rehydrate --session-id codex:abc123
llm-wiki-session rehydrate --topic knowmade-dev
```

Strict forced continuation is not the default. If implemented later, it must be
opt-in and guard against loops with per-turn counters and stop-hook-active flags.

## Feedback Candidates

User-prompt hooks may create feedback candidates under `.sessions/feedback/`
when the user gives a correction, preference, explicit approval, or plan
acceptance. Generic acknowledgements such as `ok`, `thanks`, and `cool` are
ignored by default, and so is a reply of fewer than three words whose only
trigger is a weak one (`no`, `actually`, `instead`, `correct`, `exactly`,
`perfect`): a bare "no" answers a question, it is not a lesson. Review candidates with:

```bash
llm-wiki-session feedback list --unpromoted
llm-wiki-session feedback show fb-abc123
```

Promote selected feedback with:

```bash
llm-wiki-session feedback promote fb-abc123 --topic knowmade-dev-PRIVATE
```

Feedback promotion writes a distilled raw note under the target topic and logs a
`feedback` entry. It follows the same private-first gating as digest promotion
below. See [feedback.md](feedback.md) for taxonomy and policies.

## Promotion

Session digest promotion is explicit and private-first:

```bash
llm-wiki-session promote codex:abc123 --topic knowmade-dev-PRIVATE
llm-wiki-session promote codex:abc123 --topic knowmade-dev --allow-public
```

Promotion writes a topic raw note:

```text
HUB/topics/<topic>/raw/notes/YYYY-MM-DD-session-<harness>-<session>.md
```

The raw note references the digest by its `<HUB>/`-relative POSIX path in
`source:` and copies only the distilled digest body, not the raw transcript.
Digests carry machine identity (cwd, transcript pointer, git remote), so a
target slug that does not end in `-PRIVATE` is refused unless `--allow-public`
is passed. With `--allow-public`, the CWD, transcript pointer, and git remote
rows are dropped and their literal values are scrubbed from the promoted body;
user-written manual summaries are copied as-is, so review them before a public
promotion. Append the topic `log.md` entry. Compilation into `wiki/` articles
remains a separate, explicit wiki compile/update step.

## Privacy Defaults

- Redact obvious token/password/authorization fields in event previews.
- Store tool inputs (shell commands, Write/Edit payloads) only as hash
  summaries; keep `tool_name` and `file_path` for rehydration.
- Strip URL userinfo from git remotes before storing them anywhere.
- Refuse to promote into non-`-PRIVATE` topics unless `--allow-public` is
  passed; then drop the machine-identity rows from the promoted note.
- Store transcript pointers and hashes, not transcript bodies.
- Keep raw transcript archiving disabled unless the user explicitly opts in.
- Do not auto-promote session material or feedback candidates into topic wikis.
- Do not let hook failures block normal agent work; fail closed to no capture,
  not to broken tool execution.
- To opt out, run `llm-wiki-session disable` or ask `@knowmade session disable`; this writes `enabled: false`.
