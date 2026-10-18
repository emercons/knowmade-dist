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

## Hook Adapter Contract

In the Claude plugin there are no automatic hooks: `bin/llm-wiki-session` is
only a command-driven helper, and session capture there happens when the user
explicitly runs `wiki session capture` or `wiki feedback capture`. The Codex
plugin registers the hooks below, but capture is opt-in: they do nothing until
the user runs `llm-wiki-session enable` (decision 3a for emercons/knowmade#76,
"Codex session hooks never explained, default ON"; see "What the Codex hooks do").

Each runtime adapter should call the same deterministic helper shape:

```bash
llm-wiki-session hook --harness <codex|claude|opencode|gemini> --if-enabled
```

The command reads the harness hook JSON object from stdin and should:

1. resolve HUB by the same rules as every other Knowmade command (`--hub`, or
   `hub_path` in `~/.config/llm-wiki/config.json`; `--local` uses `.wiki`). The
   hook only writes into a hub directory that already exists: it never creates
   one, never falls back to creating `~/wiki`, and if the hub cannot be resolved
   (unreadable config, leftover `private_hub_path`/`public_hub_path`) it warns on
   stderr and exits 0 without writing. Explicit commands (`enable`, `capture`,
   ...) instead fail with a message when the hub is missing, unless the hub was
   named with `--hub`/`--local`;
2. exit 0 without writing if `--if-enabled` is set and `.sessions/config.json`
   has `enabled: false`, if the config is unreadable (see above), or if there is
   no config at all: capture is opt-in, so a hub that never ran `enable` records
   nothing and no `.sessions/` directory appears;
3. take the `.sessions/.lock` lock, then, as one transaction: append a redacted
   event to `queue/YYYY-MM-DD.jsonl`, update `state/<harness>/<session_id>.json`
   (one write), write a digest if a capture trigger fired, and optionally append
   a redacted feedback candidate for high-signal user turns. Parallel tool calls
   fire parallel hooks; the lock is what keeps their counters and checkpoints
   exact. A hook that cannot get the lock within a few seconds warns and exits 0
   (that one event is not recorded); a lock left by a dead process is reclaimed;
4. optionally emit a short `additionalContext` block only for rehydration hooks
   when config enables it.

Hooks must be fast. If future versions do LLM distillation, enqueue work for a
background worker instead of doing it inline inside every tool hook.

## What the Codex hooks do

The Codex plugin registers six hooks. All run the same bundled helper
(`llm-wiki-session hook --harness codex --if-enabled`):

| Event | Fires | What the helper does |
|---|---|---|
| `SessionStart` | every new Codex session | emits a short context block from the hub's session digests (rehydration) |
| `UserPromptSubmit` | every prompt you send | appends a redacted event (a preview, never the raw prompt) and may note a feedback candidate |
| `PostToolUse` | every tool call | appends the tool name and file path; tool inputs are reduced to a hash |
| `PreCompact`, `PostCompact` | when Codex compacts context | writes a digest checkpoint |
| `Stop` | end of a turn | finalises the session record |

**Capture is off until you turn it on.** With no `<hub>/.sessions/config.json`
every hook exits 0 immediately and writes nothing, including `SessionStart`
rehydration. `llm-wiki-session enable` creates the config (balanced mode) and,
when the hub is inside a git work tree, appends `/.sessions/` to the hub
`.gitignore` once. `llm-wiki-session disable` writes `enabled: false`; the hooks
then no-op again without touching `hooks.json`. `llm-wiki-session status` shows
whether capture is on.

**Where it writes.** Only `<hub>/.sessions/` (`queue/`, `state/`, `digests/`,
`config.json`). Nothing else is written until you run `session promote` or
`feedback promote`, which copies a distilled note into a topic's `raw/notes/`
(public topics need `--allow-public`; machine-identity rows are scrubbed).
Raw transcripts are never stored.

## Codex Hook Packaging

Codex plugins can bundle hooks in `hooks/hooks.json`. Plugin-bundled hooks still
require the user to review/trust them. The bundled llm-wiki Codex hook should
call the copied helper in the plugin root and use `--if-enabled`, so users turn
capture on with `session enable` and off with `session disable` without editing
hook manifests.

Useful Codex events:

- `SessionStart` and `UserPromptSubmit` for soft rehydration;
- `PostToolUse` for observed tool counters;
- `PreCompact` and `PostCompact` for compaction checkpoints;
- `Stop` for final checkpoints.

## Claude/OpenCode/Gemini Adapters

For Claude Code, use command hooks on `SessionStart`, `UserPromptSubmit`,
`PostToolUse`, `PostToolBatch`, `PreCompact`, `PostCompact`, `Stop`, and
`SessionEnd` where available. Keep `SessionEnd` especially short or detached.

For OpenCode, use plugin events such as session/message/tool execution and the
compaction hook surface. For Gemini CLI, use `AfterTool`, `PreCompress`,
`AfterAgent`, `SessionStart`, and related hook events. All adapters normalize to
the same `.sessions/` files.

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
