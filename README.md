# Knowmade 0.15.2.1.0 (distribution)

Generated from the release tag `v0.15.2.1.0` of the private source repository
`emercons/knowmade` (commit `d90a866`). This repository holds only packaged
release trees, one commit per release, with no development history and no
issue tracker. Do not edit it; the next release replaces the tree.

## Install

Claude Code:

```bash
claude plugin marketplace add emercons/knowmade-dist#v0.15.2.1.0
claude plugin install knowmade@knowmade
```

Codex (0.144+):

```bash
codex plugin marketplace add emercons/knowmade-dist --ref v0.15.2.1.0
codex plugin add knowmade@knowmade
```

OpenCode, Pi and other agents (single-file protocol):

```bash
curl -fsSL https://raw.githubusercontent.com/emercons/knowmade-dist/v0.15.2.1.0/AGENTS.md -o AGENTS.md
```

Deterministic CLI from this tree: `scripts/llm-wiki --help` (Python 3.10+).

## Contents

`claude-plugin/` (Claude Code plugin, canonical behaviour source),
`plugins/llm-wiki/` (Codex plugin), `plugins/llm-wiki-opencode/` (OpenCode
plugin), `plugins/llm-wiki-dsh/` (DeepSeek Harness skill), `profiles/query-lite/` (read-only query profile), `scripts/` (the
`llm-wiki` control plane and the sync/bootstrap scripts), `AGENTS.md`,
`FIRST_RUN.md`, `COMPATIBILITY.md` (the normative compatibility contract and
the release compatibility record), `LICENSE`.

Release notes: https://github.com/emercons/knowmade-dist/releases/tag/v0.15.2.1.0
