# Session Hooks

The hook-integration half of the session layer: the adapter contract, what the
Codex hooks do, Codex hook packaging and the Claude, OpenCode and Gemini
adapters. Storage layout, config modes, digests, rehydration, promotion and
privacy defaults are in [sessions.md](sessions.md); the `session` command reads
this file only for `enable`, `disable` and adapter questions.

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
