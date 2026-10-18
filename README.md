# Knowmade 0.7.0.25.1 (distribution)

Generated from the release tag `v0.7.0.25.1` of the private source repository
`emercons/knowmade` (commit `4a0c025`). This repository holds only packaged
release trees, one commit per release, with no development history and no
issue tracker. Do not edit it; the next release replaces the tree.

## Install

Claude Code:

```bash
claude plugin marketplace add emercons/knowmade-dist#v0.7.0.25.1
claude plugin install wiki@knowmade
```

Codex (0.144+):

```bash
codex plugin marketplace add emercons/knowmade-dist --ref v0.7.0.25.1
codex plugin add wiki@knowmade
```

OpenCode, Pi and other agents (single-file protocol):

```bash
curl -fsSL https://raw.githubusercontent.com/emercons/knowmade-dist/v0.7.0.25.1/AGENTS.md -o AGENTS.md
```

Deterministic CLI from this tree: `scripts/llm-wiki --help` (Python 3.10+).

## Contents

`claude-plugin/` (Claude Code plugin, canonical behaviour source),
`plugins/llm-wiki/` (Codex plugin), `plugins/llm-wiki-opencode/` (OpenCode
plugin), `profiles/query-lite/` (read-only query profile), `scripts/` (the
`llm-wiki` control plane and the sync/bootstrap scripts), `AGENTS.md`,
`FIRST_RUN.md`, `COMPATIBILITY.md` (the normative compatibility contract and
the release compatibility record), `LICENSE`.

Release notes: https://github.com/emercons/knowmade-dist/releases/tag/v0.7.0.25.1
