# Knowmade for OpenCode

This directory is the generated OpenCode and Pi packaging of Knowmade. It keeps
upstream-compatible directory names while exposing Knowmade's one-hub,
inbox-first, public/private, and goal-directed behavior.

Install, permissions, the read-only `wiki-query` preset and the Pi launchers are
documented once, in the repository README:

- [OpenCode install and permissions](../../README.md#install) (the **OpenCode** and **Pi** entries)
- [Upgrading to a new release](../../README.md#upgrade)

From a local checkout, point OpenCode's `instructions` at
`plugins/llm-wiki-opencode/skills/wiki-manager/SKILL.md` (full protocol) or
`plugins/llm-wiki-opencode/skills/wiki-query/SKILL.md` (read-only lookups).

## Generated files

Do not edit the skill files here by hand; run
`scripts/sync-opencode-plugin.sh` to regenerate them from the canonical
upstream-compatible source. This README is hand-maintained and preserved by the
sync. `skills/wiki-manager/references` is a real copy of
`claude-plugin/skills/wiki-manager/references/` (as in the Codex mirror), so the
whole directory works from a clone, an archive or a Windows checkout without
symlink support. OpenCode and Pi need that whole directory, not only `SKILL.md`.
