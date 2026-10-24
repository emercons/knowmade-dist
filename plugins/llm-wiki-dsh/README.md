# Knowmade for DeepSeek Harness

This generated package provides the `/knowmade` filesystem skill for DeepSeek
Harness. Install it with `scripts/install-deepseek-harness.sh`; the installer
copies the self-contained bundle to `${DSH_HOME:-~/.dsh}/skills/knowmade`.

`skills/knowmade/` holds `SKILL.md`, a real copy of the references and the
deterministic helper in `bin/` (`llm-wiki`, `llm-wiki-session`, the adoption
launchers and the `knowmade_control` package). Nothing outside that directory
is needed at run time.

DeepSeek Harness is still a developer preview. This integration deliberately
uses only its documented filesystem-skill contract and does not install a
custom runtime plugin. If session hooks are desired, use the harness's official
Codex-hook compatibility bridge separately.

Do not edit the files here by hand; run `scripts/sync-dsh-plugin.sh`.
