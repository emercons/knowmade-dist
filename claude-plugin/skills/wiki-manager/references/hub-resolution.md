# Hub And Wiki Resolution

This is the one hub/wiki resolution protocol. Every other copy is either a
pointer to this file or the stamped command prelude below, which is generated
from it. Change the protocol here and in the template; never edit a stamped
block by hand.

## Command prelude template

Commands that need a wiki carry this block between `hub-prelude:start` and
`hub-prelude:end` markers. `scripts/sync-command-prelude.py` (run by both plugin
sync scripts) rewrites every marked block from the template below, and
`tests/test-plugin-validate.sh` fails when a block differs. The block is
deliberately short: the common case needs no reference read.

<!-- hub-prelude-template:start -->
**Resolve the wiki.** A few file reads, not a search: never `find`, Explore, or scan the filesystem. Full protocol and edge cases: `skills/wiki-manager/references/hub-resolution.md`.
1. HUB: read `$HOME/.config/llm-wiki/config.json` and use `hub_path` (expand only a leading `~`); `resolved_path` is a fallback cache; with no config use `$HOME/wiki`. If the hub is stat-able but reads fail with `Operation not permitted`, stop and report the launcher permission denial.
2. Wiki, first match: `--local` → `.wiki/`; `--wiki <name>` → `HUB/wikis.json` entry (stale path → `HUB/topics/<name>`); `.wiki/` in CWD; else HUB. Reject archived entries (`status: archived`, `topics/.archive/`) unless the command takes `--include-archived`.
3. `<slug>` and `<slug>-PRIVATE` are sibling topics in one hub: read private freely, write under the Write-Guard in `skills/wiki-manager/references/knowmade-profile.md` § Private Data Boundary.
<!-- hub-prelude-template:end -->

What a command does when the resolved wiki is missing is command-specific and
stays in the command, next to the block (see Wiki-requirement variants).

## Resolve HUB

**Sequential file-read protocol. Do NOT use Explore agents, `find`, `ls -R`, or
any filesystem search. Each step is a single Read. Most configured sessions
resolve by step 3.**

The hub path comes from one `hub_path` config (or `~/wiki/` when no config
exists). Earlier versions checked `~/wiki/` first, which fails in sandboxed
environments where `~/wiki/` is not an allowed path.

1. Read `~/.config/llm-wiki/config.json` (expand `~` to `$HOME`).
2. If config has `hub_path`, expand the leading `~` ONLY and treat that as the
   preferred **HUB** candidate.
3. If the expanded `hub_path` exists, **HUB** = expanded `hub_path`. Done. Later
   verification still checks that `_index.md` exists before treating it as
   initialized.
4. If the expanded `hub_path` is unavailable and config has `resolved_path` with
   `_index.md`, **HUB** = `resolved_path` (backward-compatible fallback). Done.
5. If config has `hub_path` but neither path is initialized yet, **HUB** =
   expanded `hub_path`; use it for initialization. Done.
6. If config has legacy `private_hub_path`/`public_hub_path` but no `hub_path`,
   ask the user to replace them with a single `hub_path`. Never choose between
   split hubs automatically.
7. If config has only `resolved_path`, **HUB** = `resolved_path`. Done.
8. If no config exists, try `$HOME/wiki/_index.md`. If it exists, **HUB** =
   `$HOME/wiki`. Done.
9. If nothing is found, ask the user where to create the wiki before creating
   anything.

Preferred Knowmade config (an upstream-style shared path such as
`~/Library/Mobile Documents/com~apple~CloudDocs/wiki` stays compatible):

```json
{ "hub_path": "~/knowmade" }
```

> **Do not confuse directory existence with hub existence.** A leftover
> `.DS_Store`, an empty folder, or a symlink to an uninitialized path is not a
> hub. Only `_index.md` at the hub root counts as an initialized hub.

> **Config is authoritative.** When the config names `hub_path` or
> `resolved_path`, all initialization happens there. Never create a hub at
> `~/wiki/` when config points elsewhere, and never touch `~/wiki/` when config
> exists (sandboxes may not allow it).

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

Replace ONLY the leading `~` with the current user's home directory. A `~` in
the middle of a path (`com~apple~CloudDocs`) is a literal directory name.

```bash
hub_path="~/Library/Mobile Documents/com~apple~CloudDocs/wiki"  # from config
expanded="${hub_path/#\~/$HOME}"
```

Never use `eval` or unquoted expansion. Always double-quote paths in Bash
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
   command's wiki-requirement variant. A hub holds no content of its own; when
   it has no topic wiki yet, suggest creating one.

Then verify existence by reading `<wiki-root>/_index.md`. If it is missing,
follow the command's variant.

### Archived entries

A registry entry with `status: archived`, or a path under `topics/.archive/`, is
rejected by every command that reads or writes semantic content unless that
command explicitly supports archived inclusion (`--include-archived`). Archived
topic wikis are quiet by default: query, compile, ingest, research, output,
plan, assess, librarian, refresh and broad audit workflows skip them. Deep query
may report archived index matches separately; full archived reads need explicit
user intent.

### Private topics and the private goal gate

Private topic wikis (`<slug>-PRIVATE`) and local private wikis may be read for
context by any workflow. For substantial private work, `config.md`, `goals.md`
when present, and high-priority inventory records form a concise goal brief: it
is planning context, not factual evidence. What may be written back into public
files is governed by [`knowmade-profile.md`](knowmade-profile.md) § Private Data
Boundary: Read Freely, Write-Guard.

Public and private versions are sibling topic roots in the single hub:
`topics/<slug>/` and `topics/<slug>-PRIVATE/`. The suffix is exact and
uppercase. The canonical ignore rule is `topics/**/*-PRIVATE/`: it covers the
live topic and its archived form under `topics/.archive/`. The older
`topics/*-PRIVATE/` form does not reach the archive (`*` never matches `/`), so
keep it if present but add the `**` rule beside it; `llm-wiki lint --fix` on a
version-controlled hub does exactly that.

### Project scoping

Commands that accept `--project <slug>` (`ingest`, `research`, `output`,
`compile`) apply it as an explicit flag only. There is no ambient project focus;
see [`projects.md`](projects.md) § Focus.

## Wiki-requirement variants

Each command picks one variant for a missing wiki and states it beside its
prelude block.

- **A, wiki-required** (`compile`, `output`, `plan`, `assess`, `audit`,
  `librarian`, `refresh`, `dataset`, `inventory`): stop with
  "No wiki found. Run `wiki init <topic>` first." Running these on an empty
  state would silently produce nothing or corrupt derived caches.
- **B, wiki-creating** (`ingest`, `ingest-collection`, `research`): accept
  `--new-topic <name>` and create the topic wiki with the init protocol. Without
  the flag and without a wiki, stop with "No wiki found. Use `--new-topic
  <name>` to create one, or run `wiki init` first."
- **C, wiki-neutral** (`project`, `wiki` status/router/init/config): resolve
  HUB and the wiki location, then handle missing state inline with
  command-specific messaging.
- **D, hub lifecycle** (`archive`): resolves HUB, not an active topic wiki; it
  moves topic roots between `HUB/topics/<slug>/` and
  `HUB/topics/.archive/<slug>/` and updates `wikis.json`. It does not operate on
  project-local `.wiki/` directories.
- **E, hub specialist library** (`specialist`): resolves HUB and reads its
  `_index.md`, `wikis.json` and optional `.skills/_index.md`; it resolves one
  active topic only to enable/disable, apply a method, or run a targeted
  candidate scan. Definitions stay at hub scope and are never copied into topic
  content.

Command-specific parsing, flags and deviations (for example `assess` asking
which wiki to compare against) stay in the command file.

## Registry Path Resolution

`HUB/wikis.json` usually lives inside the shared hub folder, so its paths must
be portable across machines. Prefer relative paths for topic wikis:

```json
{
  "default": "<HUB>",
  "wikis": {
    "hub": { "path": "<HUB>", "description": "Hub" },
    "bitcoin": { "path": "topics/bitcoin", "description": "Bitcoin wiki" }
  },
  "local_wikis": []
}
```

Resolve an entry path in this order:

1. `<HUB>` or `HUB` → the resolved hub path
2. `<HUB>/...` or `HUB/...` → relative to the resolved hub path
3. Leading `~/...` → expand `~` on the current machine
4. Absolute path → use as written
5. Any other path → relative to HUB

If an entry's path is missing but `HUB/topics/<entry-name>/_index.md` exists,
use `HUB/topics/<entry-name>` and repair the registry on the next `wikis.json`
sync. If a command explicitly includes archived content and the active fallback
is missing but `HUB/topics/.archive/<entry-name>/_index.md` exists, use that and
ensure the entry has `status: archived`. If `wikis.json` is unreadable but
`HUB/topics/` is populated, list active topic directories by reading each
topic's `config.md` and `_index.md`; read `topics/.archive/` only for archive or
explicit archived maintenance workflows.
