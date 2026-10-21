---
description: "Remove user-selected data or a source from wiki-controlled local files, including archives and session context. Dry-run first, apply explicitly, then verify."
argument-hint: "[<wiki-root>] [--source <raw-path>] [--everywhere] [--stdin] [--apply] [--remove-from-logs] [--variants exact|common] [--allow-short] [--max-file-bytes <n>] [--delete-binary-matches] [--wiki <name>] [--local]"
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(ls:*), Bash(wc:*), Bash(date:*), Bash(rm:*), Bash(grep:*), Bash(python3:*), Bash(scripts/llm-wiki:*), Bash(${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki:*)
---

## Your task

Read `skills/knowmade/references/retract-protocol.md` and follow it with
$ARGUMENTS. It holds the whole workflow; do not act from this file alone.
