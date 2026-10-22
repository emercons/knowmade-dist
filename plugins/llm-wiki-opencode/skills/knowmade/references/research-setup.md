# Research Setup Protocols

The rare branches of `/knowmade:research`, moved out of the command so they are
read only when taken: the `--project` scope, archive awareness and private-goal
awareness. Read the section for the branch in hand, not the file top to bottom.
The other branches live elsewhere: `--min-time`, question, thesis and `--plan` in
[`research-modes.md`](research-modes.md); `--new-topic` in the command itself, with the manual
fallback in [`wiki-setup.md`](wiki-setup.md) § Init manual fallback. Inventory
awareness (start of Phase 1) and the end-of-run inventory follow-ups (Phase 5)
are in [`research-protocol.md`](research-protocol.md).

## Project scope (`--project <slug>`)

**--project <slug>**: Tag all new outputs with this project. The research playbook/summary artifact is saved inside `output/projects/<slug>/` instead of flat `output/`. Compiled wiki articles get `project: <slug>` frontmatter. If the project doesn't exist, fail early with a helpful error. See [`projects.md`](projects.md) for the projects architecture.

Verify the project exists at `<wiki-root>/output/projects/<slug>/WHY.md` before starting; if not, fail early with a suggestion to run `/knowmade:project new <slug> "goal"` first.

There is no ambient project focus — pass `--project` explicitly when you want project scope. The focus-session mechanism was removed in the v0.2 projects simplification; see `projects.md` § "Focus" for the rationale.

## Archive awareness

Research ignores archived topic wikis by default. If `--wiki <name>` resolves to
an archived registry entry or a path under `topics/.archive/`, stop and ask the
user to restore it or rerun with `--include-archived`. When no target wiki is
specified, archived topics may be mentioned as overlap only: read their master
`_index.md` files, warn that a related archived topic exists, and ask whether
to restore it or continue fresh. Do not ingest or compile into archived topics
through auto-classification.

**--include-archived**: Explicitly allow researching into an archived target
wiki. Keep the target archived and label the session accordingly.

## Private goals awareness

If the target wiki is a private topic wiki, read relevant private goals/desires
from `goals.md`, `config.md`, and high-priority inventory records when cheap.
Use them to prioritize research paths and practical deliverables. Do not treat
goals as factual evidence. A `-PRIVATE` sibling may also be read when
researching a public topic; what reaches the public topic's `raw/`, `wiki/`,
and `log.md` is governed by `skills/knowmade/references/private-boundary.md`, applied in Phases 4 and 5.
