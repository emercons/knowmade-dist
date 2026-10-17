# Archive Lifecycle

Archive preserves knowledge while removing it from normal working context. It is
for material the user no longer wants active tools to load by default, not for
bad evidence or deleted content.

## Scope

Archive operates at the topic-wiki lifecycle level in v1:

- Hub topic wikis move from `HUB/topics/<slug>/` to
  `HUB/topics/.archive/<slug>/`.
- Project archive remains the existing `output/projects/.archive/<slug>/`
  workflow from `projects.md`.
- Inventory and dataset archive remain their existing `status: archived`
  frontmatter values.
- Individual `raw/` or `wiki/` file archive is intentionally out of scope.
  Moving single source/article files breaks exact `sources:` references,
  backlinks, and coverage checks. If enough material is no longer wanted, the
  topic boundary should be archived or split.

## Lifecycle Commands

`llm-wiki archive list|topic|restore` (flags in [cli.md](cli.md)) performs the
steps below deterministically and atomically: move, registry, hub index, logs.
Run the CLI; never perform them by hand. Only `peek` is agent work. The steps
document the contract the CLI implements.

### `archive topic <slug>`

1. Resolve HUB with the standard hub-resolution protocol.
2. Refuse to archive `hub`.
3. Locate the active topic in this order:
   - `wikis.json` entry whose `status` is not `archived`
   - `HUB/topics/<slug>/`
   Refuse if the located path is not under `HUB/topics/`. Archiving only moves
   hub-local topics; a topic whose registry entry points outside the hub stays
   where it is and must be moved under `topics/` and re-registered first.
4. Fail if `HUB/topics/.archive/<slug>/` already exists.
   For a `-PRIVATE` slug in a git-tracked hub, also apply the ignore guard
   described under [Private Topics](#private-topics) before moving anything.
5. Move `HUB/topics/<slug>/` to `HUB/topics/.archive/<slug>/`.
6. Update `wikis.json`:
   ```json
   "<slug>": {
     "path": "topics/.archive/<slug>",
     "description": "...",
     "status": "archived",
     "archived": "YYYY-MM-DD",
     "archive_reason": "optional user reason"
   }
   ```
   For a `-PRIVATE` slug the entry keeps `path`, `status`, `id` and the archive
   fields but never gets a `description`: the registry is committed with the
   hub, so the private topic's title is not copied into it.
7. Update the hub `_index.md` so active topic listings exclude this wiki and
   archived counts remain visible. A `-PRIVATE` topic is listed by slug only
   (empty Title/Description cells); its `config.md` is not read.
8. Append to both hub `log.md` and the archived topic's own `log.md`:
   `## [YYYY-MM-DD] archive | archived topic <slug>`.

### `archive restore <slug>`

1. Resolve HUB.
2. Locate the archived topic from `wikis.json` or
   `HUB/topics/.archive/<slug>/`.
3. Fail if `HUB/topics/<slug>/` already exists.
4. Move `HUB/topics/.archive/<slug>/` to `HUB/topics/<slug>/`.
5. Update `wikis.json` path to `topics/<slug>`, set `status: active`, remove
   `archived` and `archive_reason`, and set `restored: YYYY-MM-DD`.
6. Stale-check the restored wiki's master `_index.md`.
7. Append archive/restore log entries.

### `archive list`

List active topics by default and show an archived count. With `--archived`,
include archived topics in a separate table. Do not read archived topic
articles; use registry metadata and `_index.md` only when needed.

### `archive peek <query>`

Optional convenience mode for discovery. Read archived topic `_index.md` files
only, search summaries/tags for a query, and report matches separately from
active material. Do not read archived articles unless the user then asks to
restore or explicitly includes archived content in a query.

## Private Topics

A private topic (`topics/<slug>-PRIVATE/`) is normally kept out of git by the
hub's `.gitignore`. Archiving moves it to `topics/.archive/<slug>-PRIVATE/`,
and the legacy rule `topics/*-PRIVATE/` does not reach that path because `*`
never matches `/`. Without a guard, `archive topic --private` would silently
turn an ignored wiki into a committable one.

- The canonical rule is `topics/**/*-PRIVATE/`. It matches the live and the
  archived location. Hub init writes it into `HUB/.gitignore`; `lint` on a
  hub inside a git work tree reports a live or archived gap as critical and
  `lint --fix` appends it, keeping any existing `topics/*-PRIVATE/` line.
- Before `archive topic` or `archive restore` moves a `-PRIVATE` topic in a
  hub inside a git work tree (`git rev-parse --is-inside-work-tree` from the
  hub says `true`; a hub nested in a dotfiles or project repository counts),
  ask git whether the destination is ignored:
  `git check-ignore -q <destination>/_index.md`, run from the hub. Probing a
  file inside the destination lets directory-only patterns match before the
  directory exists.
- If the destination is not ignored, refuse without moving anything. The
  message names the exact rule to add (`topics/**/*-PRIVATE/`) and the repair
  command (`llm-wiki lint --fix <HUB>`).
- Hubs outside any git work tree are not checked; nothing can be committed
  from them. When git cannot answer, a `.git` in the hub or any ancestor
  counts as inside, and only the literal rule in `HUB/.gitignore` satisfies
  the guard.

## Visibility Contract

Archive is a context filter, not a deletion mechanism.

| Workflow | Default behavior |
|----------|------------------|
| `wiki status` | Show active stats and `Archived topics: N`; list archived only with `--archived` |
| `query --quick` / standard / `--list` | Exclude archived topics and archived supplementary wikis |
| `query --deep` | Peek archived topic indexes and report separate Archived Matches; do not cite them as active evidence |
| `query --include-archived` | Fully search/read archived material and label it in citations |
| `research` | Ignore archived wikis; warn when an archived topic title strongly overlaps the requested topic |
| `ingest` / `ingest-collection` | Do not route into archived wikis unless the user explicitly restores or forces archived access |
| `compile` | Active wikis only by default; explicit archived target compiles inside archive but never makes it active |
| `inventory`, `dataset`, `project`, `lessons-learned` | Reject archived targets unless explicitly included; distinguish topic archive from record/dataset/project archive states |
| `output`, `plan`, `assess` | Ignore archived unless `--include-archived`; label archived-derived context clearly |
| `init` / `--new-topic` | Treat archived slugs as collisions; restore or choose a new slug instead of creating an active duplicate |
| `librarian` / `refresh` | Skip archived by default; archived material should not create freshness chores |
| `audit` | Skip archived by default, except when the targeted artifact depends on archived material |
| `retract` | User-directed control-plane operation; selected archived data is included without another mutation gate |
| `lint` | Active structural checks by default; `--wiki <slug> --include-archived` maintains one archived topic explicitly |

## Lint Semantics

Lint is responsible for keeping archive structure coherent, but normal lint
should not resurrect archived maintenance debt.

- Default hub lint validates the active registry and reports archived topic
  counts as skipped.
- `lint --include-archived` checks archived topic structure in addition to
  active material.
- The lint command's `--archived-only` option (agent-level, not a CLI flag)
  runs that per archived slug from `archive list --archived --json`.
- `lint --fix --include-archived` may perform mechanical fixes on archived
  content: indexes, canonical placement, frontmatter aliases, dead index rows,
  and safe source-reference rewrites.
- Freshness, librarian quality pressure, orphan-source pressure, and
  uncompiled-source coverage should be skipped for archived content unless the
  user explicitly asks for a full archived maintenance pass.

## Cross-Boundary Links

Whole-topic archive preserves internal relative links. Cross-boundary links need
labels:

- active -> active: normal
- archived -> archived: normal
- archived -> active: allowed
- active -> archived: warning; active content now depends on quiet material

An active article citing an archived raw source is allowed but should be
reported by lint as a boundary-crossing provenance warning. Do not break the
source chain just because lifecycle state changed.

## Registry Repair

`wikis.json` is the lifecycle source of truth, but lint should repair common
filesystem drift:

- If `HUB/topics/.archive/<slug>/_index.md` exists and the registry points to
  `topics/<slug>` with no active directory, update the path and set
  `status: archived`.
- If a registry entry says `archived` but `HUB/topics/<slug>/_index.md` exists
  and the archived directory does not, update the path to `topics/<slug>` and
  set `status: active`.
- If both active and archived directories exist for the same slug, do not choose
  automatically. Report a collision.

## Local Wikis

Do not move project-local `.wiki/` directories in v1. A local wiki can still
contain archived inventory records, dataset manifests, or projects, but topic
archive is a hub-level lifecycle operation. If a local wiki needs to go quiet,
the user can move/rename the project or register it in the hub and archive the
registered topic later.
