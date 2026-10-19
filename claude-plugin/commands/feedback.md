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

First read `references/feedback.md` and `references/sessions.md`, then resolve
HUB by the standard hub-resolution protocol from `references/hub-resolution.md`.
Prefer the deterministic helper when available.

Locate the helper once: use `"${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki-session"` in
Claude Code, or `scripts/llm-wiki-session` in a source checkout, and call it
`$SESSION_CLI` below.

```bash
"$SESSION_CLI" --hub "$HUB" feedback <subcommand>
```

### Parse $ARGUMENTS

- `list [--unpromoted] [--type correction|preference|approval|decision|manual] [--min-confidence low|medium|high]`: show captured candidates.
- `show <candidate-id>`: display one candidate with its distilled lesson and redacted user-feedback preview.
- `capture --text "..." [--session-id <id>]`: manually add a feedback candidate.
- `promote <candidate-id> --topic <slug> [--allow-public]`: write the candidate into the target topic's `raw/notes/` layer and append the topic log. A target slug that does not end in `-PRIVATE` is refused unless `--allow-public` is passed; with it, the CWD and git remote rows are dropped from the note. Promotion writes `raw/notes/` without an `inbox/` item: that is the session-distillate exception in `skills/knowmade/references/knowmade-profile.md` (Inbox-First Source Lifecycle), and the write-guard still applies.

### Policy

1. Candidate capture is allowed from trusted hooks, but promotion is explicit.
2. Do not treat generic acknowledgements (`ok`, `thanks`, `cool`) as durable lessons.
3. High-value feedback includes corrections, preferences, `always`/`never` rules, and explicit statements that the agent did the right thing.
4. Approval/decision candidates are validation of the immediately preceding context unless corroborated by a session digest.
5. Store redacted previews and hashes, not full transcripts.
6. Promotion is private-first: candidates carry cwd and git remote (URL userinfo stripped), so target a `-PRIVATE` topic or pass `--allow-public` to drop those rows.

### Examples

```bash
"$SESSION_CLI" --hub "$HUB" feedback list --unpromoted
"$SESSION_CLI" --hub "$HUB" feedback show fb-abc123
"$SESSION_CLI" --hub "$HUB" feedback capture --text "next time use the release checklist before tagging"
"$SESSION_CLI" --hub "$HUB" feedback promote fb-abc123 --topic knowmade-dev-PRIVATE
"$SESSION_CLI" --hub "$HUB" feedback promote fb-abc123 --topic knowmade-dev --allow-public
```

Report absolute paths for every promoted note. Append topic `log.md` when
promoting into a topic wiki.

Write-guard on promote: when `--topic <slug>` is a public, non-`-PRIVATE`
topic, the promoted note and log entry must not carry a `-PRIVATE` path
string, a private goal, or a lesson derived from private material; drop those
parts or promote into `<slug>-PRIVATE` instead; see `skills/knowmade/references/knowmade-profile.md` § Private Data Boundary: Read Freely, Write-Guard.
