---
description: "Review, capture, and promote curated user-feedback candidates detected from session hooks."
argument-hint: "list|show|capture|promote [--allow-public] [options]"
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(ls:*), Bash(mkdir:*), Bash(python3:*), Bash(scripts/llm-wiki-session:*), Bash(${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki-session:*)
---

## Your task

Manage the feedback-curator layer for Knowmade sessions. Feedback candidates are
small distilled records of user corrections, preferences, approvals, and plan
acceptance signals. They live under `HUB/.sessions/feedback/` until explicitly
promoted into a topic wiki.

Locate the deterministic helper once: use `"${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki-session"` in
Claude Code, or `scripts/llm-wiki-session` in a source checkout, and call it
`$SESSION_CLI` below. The helper resolves the hub itself (`--hub <path>`,
`LLM_WIKI_HUB`, then the configured hub; `--local` for `./.wiki`), so pass
`--hub` only to override it. Prefer the helper to reading `HUB/.sessions/` by hand.

```bash
"$SESSION_CLI" feedback <subcommand>
```

### Parse $ARGUMENTS

- `list [--unpromoted] [--type correction|preference|approval|decision|manual] [--min-confidence low|medium|high]`: show captured candidates.
- `show <candidate-id>`: display one candidate with its distilled lesson and redacted user-feedback preview.
- `capture --text "..." [--session-id <id>]`: manually add a feedback candidate.
- `promote <candidate-id> --topic <slug> [--allow-public]`: write the candidate into the target topic's `raw/notes/` layer and append the topic log (see the promote branch below).

### Policy

1. Candidate capture is allowed from trusted hooks, but promotion is explicit.
2. Do not treat generic acknowledgements (`ok`, `thanks`, `cool`) as durable lessons.
3. High-value feedback includes corrections, preferences, `always`/`never` rules, and explicit statements that the agent did the right thing.
4. Approval/decision candidates are validation of the immediately preceding context unless corroborated by a session digest.
5. Store redacted previews and hashes, not full transcripts.
6. Promotion is private-first: candidates carry cwd and git remote (URL userinfo stripped), so target a `-PRIVATE` topic or pass `--allow-public` to drop those rows.
7. Capture alone must not rewrite AGENTS.md or skills; promoted notes may later feed `wiki ll`, compilation or rule updates.

### Examples

```bash
"$SESSION_CLI" feedback list --unpromoted
"$SESSION_CLI" feedback show fb-abc123
"$SESSION_CLI" feedback capture --text "next time use the release checklist before tagging"
"$SESSION_CLI" feedback promote fb-abc123 --topic knowmade-dev-PRIVATE
"$SESSION_CLI" feedback promote fb-abc123 --topic knowmade-dev --allow-public
```

### If promote

A target slug that does not end in `-PRIVATE` is refused unless `--allow-public` is passed. Read `skills/knowmade/references/feedback.md` § Promotion (storage, the session-distillate exception, the write-guard on public topics, reporting) and the write-guard in `skills/knowmade/references/private-boundary.md` before writing.

### If the hub cannot be resolved or the helper cannot run

Resolve HUB by the standard protocol in `skills/knowmade/references/hub-resolution.md` and pass it as `--hub`; report the failure rather than reading `HUB/.sessions/` by hand.

### If a candidate is missing or looks wrong, or hooks do not capture

Read `skills/knowmade/references/sessions.md` (storage layout, config modes; § Feedback Candidates for the weak-trigger rule) and, for hooks that do not capture, `skills/knowmade/references/session-hooks.md` (hook adapters).
