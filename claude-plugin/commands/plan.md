---
description: "Generate an implementation plan grounded in active wiki research. Reads the knowledge base, interviews you about requirements, fills gaps with targeted research, and produces a phased plan with architecture decisions citing wiki articles as evidence."
argument-hint: "<what to build> [--wiki <name>] [--with <wiki>...] [--include-archived] [--local] [--quick] [--no-interview] [--no-research] [--format rfc|adr|roadmap|spec]"
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(ls:*), Bash(wc:*), Bash(date:*), WebFetch, WebSearch, Agent
---

## Your task

<!-- hub-prelude:start -->
**Resolve the wiki.** A few file reads, not a search: never `find`, Explore, or scan the filesystem. Full protocol and edge cases: `skills/knowmade/references/hub-resolution.md`.
1. HUB: `$LLM_WIKI_HUB` or read `$HOME/.config/llm-wiki/config.json` and use `hub_path` (expand only a leading `~`); `resolved_path` is a fallback cache; with no config use `$HOME/wiki`. If the hub is stat-able but reads fail with `Operation not permitted`, stop and report the launcher permission denial.
2. Wiki, first match: `--local` → `.wiki/`; `--wiki <name>` → `HUB/wikis.json` entry (`wikis.local.json` path first; stale path → `HUB/topics/<name>`); `.wiki/` in CWD; else HUB. Reject archived entries (`status: archived`, `topics/.archive/`) unless the command takes `--include-archived`.
3. `<slug>` and `<slug>-PRIVATE` are sibling topics in one hub: read private freely, write under the Write-Guard in `skills/knowmade/references/private-boundary.md`.
<!-- hub-prelude:end -->

Then read `skills/knowmade/references/plan-protocol.md` and follow it from step 4
with $ARGUMENTS. It holds the whole workflow; do not act from this file alone.
