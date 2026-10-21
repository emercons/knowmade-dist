# Wiki Setup Protocols

The rare branches of `/knowmade:wiki`, moved out of the command so they
are read only when taken: the manual `init` fallback, the first-run welcome
flow and the `config` procedures. Read the section for the branch in hand, not
the file top to bottom. `<cli>` is the launcher the wiki command defines
(the bundled `bin/llm-wiki`, or `scripts/llm-wiki` in a source checkout).

## Init manual fallback

Only when `<cli> init` cannot run. This fallback loads `references/wiki-structure.md`
(layout, `wikis.json`), `references/wiki-format.md` (`_index.md`, `log.md`) and
`references/wiki-articles.md` (`config.md`, topic guide). In order:

1. **Collision check.** Look at `HUB/topics/<slug>/`, `HUB/topics/.archive/<slug>/` and any `wikis.json` entry with that slug. If an archived topic exists, stop and ask whether to restore it with `/knowmade:archive restore <slug>` or choose a different slug; never silently create an active topic over an archived topic boundary.
2. **Hub, if missing** (layout: `wiki-structure.md` § Hub; formats: `wiki-format.md`): `HUB/wikis.json` (empty registry), `_index.md` (empty topic wiki table), `log.md`, `topics/`, and optionally `inbox/` with `.routed/`, `.needs-human/`, `.skipped/` for global intake. No `raw/`, `wiki/`, `inventory/`, `datasets/`, `output/`, `config.md` or `.obsidian/` at the hub. `HUB/.gitignore` holds `topics/**/*-PRIVATE/` (private topics stay out of git, live or under `topics/.archive/`) and the machine-local lines `/.sessions/`, `/.skills/registry.json`, `**/.audit/`, `**/.librarian/`, `**/.session-events.jsonl`, `**/.session-checkpoint.json`, `**/.obsidian/` and `**/.wiki-local/`. Write it even if the hub is not a git repository yet, or sits inside a larger one; lint tolerates it and `lint --fix` appends whatever is missing once the hub is in a git work tree.
3. **Topic directories** (`wiki-structure.md` § Topic Sub-Wiki): `inbox/` with `.processed/`; `raw/` with `articles/ papers/ repos/ notes/ data/`; `wiki/` with `concepts/ topics/ references/ theses/`; `output/`. Not `inventory/` or `datasets/`: `/knowmade:inventory`, `/knowmade:dataset` or lint create those lazily. For `--local`, ignore `.wiki/` in a git project (`wiki-structure.md` § Local Wiki).
4. **`.obsidian/`** with `app.json` `{"showFrontmatter": true, "alwaysUpdateLinks": true, "newLinkFormat": "relative", "useMarkdownLinks": false}`, `appearance.json` `{"accentColor": ""}` and `graph.json` `{"collapse-filter": false, "search": "", "showTags": true, "showAttachments": false, "showOrphans": true, "collapse-color-groups": false, "collapse-display": false, "showArrow": true, "textFadeMultiplier": 0, "nodeSizeMultiplier": 1, "lineSizeMultiplier": 1}`.
5. **Indexes and log.** An empty `_index.md` (today's date, all counts 0; `wiki-format.md` § _index.md Format) only in the directories created above, none for optional layers that do not exist yet; `log.md` with the entry `## [YYYY-MM-DD] init | Wiki initialized`.
6. **`config.md` and `schema.md`.** Ask "What is this wiki about?" and write `config.md` (title, description, scope, today's date; `wiki-articles.md` § config.md Format) plus a starter `schema.md` in `schema_state: advisory` with a small default vocabulary (`wiki-articles.md` § Topic Guide); it is human-owned and can be trimmed later.
7. **Register** (`wiki-structure.md` § wikis.json Format) in `HUB/wikis.json` with the portable relative path `topics/<slug>` and update the hub `_index.md` table; a `-PRIVATE` topic is registered by slug and path only, never with its description or title (`wikis.json` and the hub index are committed). For a local wiki outside the hub run `<cli> identity assign <wiki-root>` first, then add a `local_wikis` entry with its `id` (the `wiki_id`) and a path relative to HUB (`../<project>/.wiki`). Never write a path with a drive letter, `/home/...` or `/Users/...` into a tracked `wikis.json`; if no relative path reaches the wiki, ask the user instead of recording an absolute one.

## Welcome flow

For a user who has no wiki yet and gave no `init` argument. Instead of
dumping a command list, walk them through getting started.

**Step 1: Welcome and orient.** Explain what Knowmade does in one sentence, then ask what they want to research or accomplish:

> **Welcome to Knowmade** — a practical knowledge system that captures material once, routes it to focused public or private topic wikis, and turns sourced knowledge into useful decisions and outputs.
>
> To get started, what topic would you like to research? For example:
> - Quantum computing
> - Nutrition and supplements
> - Kubernetes deployment patterns
>
> Just tell me the topic, and I'll set everything up.

**Step 2: On user response,** derive a slug from their topic (lowercase, hyphens, max 40 chars) and run `<cli> init --topic <slug> --description "<their topic>"` (add `--hub <path>`; with no configured hub, ask the user where to create it — never assume `~/wiki/`). If the CLI is unavailable, run § Init manual fallback above.

**Step 3: After init completes,** suggest the immediate next action based on what's most likely useful:

> **Wiki created at `HUB/topics/<slug>/`**
>
> What would you like to do first?
>
> 1. **Research** — I'll search the web and build your knowledge base automatically
>    → Just say: `/knowmade:research "<your topic>" --wiki <slug>`
>
> 2. **Add a specific source** — paste a URL or file path
>    → Just say: `/knowmade:ingest <url>`
>
> 3. **Import existing notes** — drop files into `HUB/topics/<slug>/inbox/`
>    → Then run: `/knowmade:ingest --inbox`
>
> 4. **Import an existing wiki/repo** — ingest a Git docs repo or MediaWiki dump
>    → Just say: `/knowmade:ingest-collection <url-or-path>`
>
> 5. **Collect examples or artifacts** — find many things, dedupe them, and save a provenance-rich catalog
>    → Just say: `/knowmade:collect "<things to collect>"`
>
> 6. **Capture an Idea** — save a rough proposal, research it, and shape it before committing to a Project
>    → Just say: `I have an idea for ...`

Do NOT show the full command reference, config options, or advanced flags during onboarding. Keep it to these starter options. The user can discover the rest via `/wiki` (status view) once they have a wiki.

**Permission hint (one-time):** If this is the first wiki being created, also append:

> **Tip:** Research sessions fetch many URLs. To skip approval prompts, add this to your project's `.claude/settings.local.json`:
> ```json
> "WebFetch", "WebSearch"
> ```
> in the `permissions.allow` array.

## Config

`config` reads `~/.config/llm-wiki/config.json` and no other reference file.
How the file is read (which key wins, the `resolved_path` fallback, tilde
expansion, the optional `~/wiki` symlink) is owned by `hub-resolution.md`
§ Resolve HUB; this section is only the procedure that writes and shows it.

### `config hub-path <path>`

Set a custom hub location in `~/.config/llm-wiki/config.json`.

1. If `<path>` is provided:
   - Expand only a leading `~` to validate it on this machine; check it exists as a directory, else offer to create it.
   - Write `~/.config/llm-wiki/config.json` (create `~/.config/llm-wiki/` if needed) with the portable path as the user typed it:
     ```json
     {
       "hub_path": "<path as the user typed it>"
     }
     ```
     Do not write `resolved_path` for normal shared hubs (`hub-resolution.md` § Resolve HUB: new writes keep only `hub_path`; older configs that already have it stay readable as a fallback).
   - Optionally suggest `ln -s "<expanded path>" ~/wiki` so `~/wiki/` resolves immediately even without reading config.
   - If a wiki already exists at the OLD hub location (previous config path or `~/wiki/` fallback), ask "Move existing wiki data from `<old>` to `<new>`? (y/n)". If yes: `mv <old>/* <new>/` and update hub-owned `wikis.json` topic paths to relative `topics/<slug>` entries. If no: only update the config; the user moves data manually.
   - Report: "Hub path set to `<path>`. All wiki commands now use this location."

2. If no `<path>` provided: run `<cli> hub where` (it names the winning hub and where it came from), read the config if it exists, and report "Current hub path: `<path>`" (prefer `hub_path`; mention `resolved_path` only if it is the only value) or "No hub configured. Run `config hub-path <path>` to set one."

### `config` (no subcommand)

Show all configuration:
- **Hub path**: the hub `<cli> hub where` names (and whether it is from config or default)
- **Config file**: `~/.config/llm-wiki/config.json` (exists / not found)
- **Topic wikis**: count from wikis.json
