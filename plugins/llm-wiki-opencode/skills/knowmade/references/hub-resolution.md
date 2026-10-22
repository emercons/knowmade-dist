# Hub And Wiki Resolution

This is the one hub/wiki resolution protocol; every other copy is a pointer to
it or the stamped command prelude generated from it. Commands that delegate
resolution to the `llm-wiki` CLI (`lint`, `archive`, and `wiki`
init/status/config) carry no block; the CLI implements this protocol, and these
steps are their CLI-unavailable fallback.

## Command prelude template

`hub-prelude:start`/`end` markers in a command hold this block; `hub-only-prelude`
markers (`session`, `specialist`) hold item 1 alone. `scripts/sync-command-prelude.py`
stamps both from the template below and `tests/test-plugin-validate.sh` fails on
drift: never hand-edit a stamped block.

<!-- hub-prelude-template:start -->
**Resolve the wiki.** A few file reads, not a search: never `find`, Explore, or scan the filesystem. Full protocol and edge cases: `skills/knowmade/references/hub-resolution.md`.
1. HUB: `$LLM_WIKI_HUB` or read `$HOME/.config/llm-wiki/config.json` and use `hub_path` (expand only a leading `~`); `resolved_path` is a fallback cache; with no config use `$HOME/wiki`. If the hub is stat-able but reads fail with `Operation not permitted`, stop and report the launcher permission denial.
2. Wiki, first match: `--local` → `.wiki/`; `--wiki <name>` → `HUB/wikis.json` entry (`wikis.local.json` path first; stale path → `HUB/topics/<name>`); `.wiki/` in CWD; else HUB. Reject archived entries (`status: archived`, `topics/.archive/`) unless the command takes `--include-archived`.
3. `<slug>` and `<slug>-PRIVATE` are sibling topics in one hub: read private freely, write under the Write-Guard in `skills/knowmade/references/private-boundary.md`.
<!-- hub-prelude-template:end -->

A command states its own missing-wiki behaviour beside its block; the inventory
of variants is in `DESIGN.md` § Missing-wiki variants.

## Resolve HUB

**Sequential file reads: no Explore agents, `find`, `ls -R` or other filesystem
search. Most configured sessions resolve by step 3.**

HUB order: `--hub`, then `$LLM_WIKI_HUB`, then (only with `"hub_discovery":
"cwd"` in config) the nearest enclosing directory holding `wikis.json` and
`topics/`, then the steps below; `llm-wiki hub where` names the winner.

1. Read `~/.config/llm-wiki/config.json` (expand `~` to `$HOME`).
2. If config has `hub_path`, expand the leading `~` ONLY: the preferred **HUB**
   candidate.
3. If it exists, **HUB** = it. Done (`_index.md` is still checked before the hub
   counts as initialized).
4. If it is unavailable and config has `resolved_path` with `_index.md`,
   **HUB** = `resolved_path` (backward-compatible fallback). Done.
5. If config has `hub_path` but neither path is initialized, **HUB** = expanded
   `hub_path`, used for initialization. Done.
6. If config has legacy `private_hub_path`/`public_hub_path` but no `hub_path`,
   ask the user to replace them with a single `hub_path`; never choose between
   split hubs.
7. If config has only `resolved_path`, **HUB** = `resolved_path`. Done.
8. If no config exists, try `$HOME/wiki/_index.md`; if it exists, **HUB** =
   `$HOME/wiki`. Done.
9. If nothing is found, ask the user where to create the wiki before creating
   anything; the preferred config is in `SKILL.md` § Hub Path.

> **Do not confuse directory existence with hub existence.** A leftover
> `.DS_Store`, an empty folder, or a symlink to an uninitialized path is not a
> hub. Only `_index.md` at the hub root counts as an initialized hub.

> **Config is authoritative.** When the config names `hub_path` or
> `resolved_path`, all initialization happens there; never create a hub at, or
> touch, `~/wiki/` while config exists (sandboxes may not allow it).

> **Do not write `resolved_path` into shared configs.** Older configs may carry
> it, but new writes keep only `hub_path`, so one Mac's `/Users/<name>/...` path
> cannot break another Mac sharing the folder through iCloud.

> **Permission-denied is not path-missing.** On macOS/iCloud, `stat` can
> succeed for `HUB`, `HUB/wikis.json` and `HUB/topics` while reads or listings
> fail with `Operation not permitted` (`errno=1`). The configured `hub_path` is
> correct: tell the user to grant Full Disk Access or iCloud Drive access to the
> exact app launching the agent and restart it. Do not fall back to `~/wiki`,
> do not use `resolved_path`, and do not suggest moving the wiki.

### Tilde expansion and spaces

Replace ONLY the leading `~` with the current user's home directory; a `~` in
the middle of a path (`com~apple~CloudDocs`) is a literal directory name. Never
use `eval` or unquoted expansion. Always double-quote paths in Bash
(`ls "$HUB/topics/"`); Read, Write, Edit, Glob and Grep handle spaces natively.

A symlink such as `ln -s "<real hub>" ~/wiki` is an optional shell convenience;
config-based resolution never needs it.

## Resolve the target wiki

With HUB known, the target wiki is the first match of:

1. `--local` → `.wiki/` in the current directory.
2. `--wiki <name>` → the entry in `HUB/wikis.json`, resolved as described under
   Registry Path Resolution. If the registered path is stale, fall back to
   `HUB/topics/<name>`.
3. The current directory contains `.wiki/` → use it.
4. Otherwise HUB itself: use the hub's active topic wiki, or fail per the
   command's own missing-wiki behaviour. A hub holds no content of its own; when
   it has no topic wiki yet, suggest creating one.

Then verify existence by reading `<wiki-root>/_index.md`. If it is missing,
follow the command's missing-wiki behaviour.

### Archived entries

A registry entry with `status: archived`, or a path under `topics/.archive/`, is
rejected by every command that reads or writes semantic content unless that
command explicitly supports archived inclusion (`--include-archived`). Archived
topics are quiet by default: principle 11 in [`principles.md`](principles.md)
states the rule and how deep query may report archived index matches, and
[`archive.md`](archive.md) lists which workflows skip them.

### Private topics and the private goal gate

Private topic wikis (`<slug>-PRIVATE`) and local private wikis may be read for
context by any workflow; what may be written back into public files is governed
by [`private-boundary.md`](private-boundary.md). Substantial private work
assembles a goal brief first ([`knowmade-profile.md`](knowmade-profile.md)
§ Goal-Directed Workflow). The sibling layout and the `.gitignore` rule are in
[`wiki-structure.md`](wiki-structure.md) § Public And Private Topic Pairs.

### Project scoping

Commands that accept `--project <slug>` (`ingest`, `research`, `output`,
`compile`) apply it as an explicit flag only. There is no ambient project focus;
see [`projects.md`](projects.md) § Focus.

## Registry Path Resolution

Tracked `HUB/wikis.json` paths ([format](wiki-structure.md#wikisjson-format))
must be portable: `<HUB>`-relative. A machine-specific path goes in the
gitignored `HUB/wikis.local.json`, same shape, only `path` read.

Resolve an entry path in this order:

1. `HUB/wikis.local.json` path of the same slug, for an active entry only
2. `<HUB>` or `HUB` → the resolved hub path
3. `<HUB>/...` or `HUB/...` → relative to the resolved hub path
4. Leading `~/...` → expand `~` on the current machine
5. Absolute path → use as written
6. Any other path → relative to HUB
7. A directory with no `_index.md` but a `.wiki/_index.md` → that `.wiki/`

`llm-wiki hub registry check --portable` fails on a non-portable tracked path.

If an entry's path is missing but `HUB/topics/<entry-name>/_index.md` exists,
use `HUB/topics/<entry-name>` and repair the registry on the next `wikis.json`
sync. If a command explicitly includes archived content and the active fallback
is missing but `HUB/topics/.archive/<entry-name>/_index.md` exists, use that and
ensure the entry has `status: archived`. If `wikis.json` is unreadable but
`HUB/topics/` is populated, list active topic directories by reading each
topic's `config.md` and `_index.md`; read `topics/.archive/` only for archive or
explicit archived maintenance workflows.
