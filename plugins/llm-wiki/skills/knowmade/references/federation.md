# Federation Registry

Read this only when the user asks which hubs or wikis this machine knows, or
where a wiki with a given id lives now. `<cli>` is the bundled `bin/llm-wiki`
(`scripts/llm-wiki` in a source checkout). The registry is machine state: it
never changes how `hub_path`, `LLM_WIKI_HUB`, `--hub` or the current directory
select a hub, and it is not a source of truth. A root's own `hub_id` /
`wiki_id` decides; every status is recomputed from the root. Flags:
`references/cli.md`. Design: `DESIGN.md` sections 4 and 9-13.

## The file

`~/.config/llm-wiki/registry.json`, next to `config.json`. Never inside a
corpus, never tracked. A missing file is an empty registry; a file that is
unreadable, not JSON, has another `schema_version` or a malformed entry stops
every verb with one line and exit 2, and is never overwritten. Keys this
version does not know, top-level or per entry, are kept when it rewrites the
file. `add`, `refresh` and `remove` take a lock file (`registry.json.lock`) around
their read-modify-write, so parallel callers lose nothing; a lock held for 10 s
(`LLM_WIKI_REGISTRY_LOCK_TIMEOUT`) is one line and exit 2, a stale one (dead
holder, or older than 30 s) is reclaimed, and readers never wait.

```json
{"schema_version": 1,
 "roots": [{"id": "hb-… | wk-…", "kind": "hub | wiki", "path": "<absolute; ~ kept as typed>",
            "alias": null, "added": "YYYY-MM-DD", "last_seen": "YYYY-MM-DD | null"}]}
```

## Verbs

- `<cli> registry add <path> [--alias <name>] [--assign]`: `<path>` is a hub
  (`wikis.json`) or a wiki (`config.md`); a wiki nested as `topics/<slug>/.wiki`
  is refused (exit 2): pass the `.wiki` path. The id comes from the root. A root with
  no id exits 1 with `run llm-wiki identity assign [--hub] first`; `--assign` does
  that for you and is the only write into a root this namespace makes. The same
  id at another path is a second replica (added, with a notice); the same id at
  the same path is a no-op. An alias is stripped of surrounding whitespace; a
  duplicate, blank, or id-shaped one exits 1. With `--assign` the id is written
  only after the registry file has been validated and its directory can be written.
- `<cli> registry list [--json]`: one line per entry: status, kind, id, alias,
  path, and a detail. `--json` is `{"roots": [{id, kind, path, alias, added,
  last_seen, status, declared_id?, forked_to?, reason?}]}`.
- `<cli> registry refresh`: sets `last_seen` to today on every `available` entry;
  the only writer besides `add` and `remove`.
- `<cli> registry remove (<id> | <alias> | <path>)`: removes one entry and
  touches nothing on disk. An id with several replicas, or a path shared by
  several entries, exits 1 and lists them; name the path.
- `<cli> registry locate (<id> | <alias>) [--all] [--json]`: identity to path.
  Ids and aliases match exactly; a slug is a label, not a key.
- `<cli> registry inventory [--json] [--public]` and
  `<cli> registry peek (<id> | <alias> | <knowmade:// uri>) [--json] [--public]`:
  the two read-only views described under "Inventory and peek".

## Statuses

| Status | Meaning |
|---|---|
| `available` | path exists and declares the registered id |
| `unavailable` | path missing or unreadable, or its `wikis.json` / `config.md` exists but cannot be read (the line and `reason` say why); the entry stays registered |
| `mismatch` | path declares another id; `forked -> <new id>` when that root records the registered id as `wiki_forked_from` / `hub_forked_from` |
| `undeclared` | path exists and declares no id |

## Locate and ambiguity

Only `available` replicas count. Exactly one: its path, exit 0. None: exit 1,
with the last known paths and statuses; do not guess a path. Several: exit 1
`ambiguous: N replicas of <id>` and the list. Anything that could lead to a write
must stop there and ask which replica; `--all` prints every available replica,
exit 0, for read-only use. An alias names one registered entry, so it selects
that replica. An unknown id or alias exits 2. `--json`:
`{"id", "kind", "status": "available | ambiguous | unavailable", "path", "replicas": [{"path", "status", "alias", "last_seen"}]}`
(`path` is null unless exactly one replica is available).

## Inventory and peek

Two read-only views over the registry. Both are derived on every call, with no
cache file and no store of their own: each fact is read from the root that owns
it, and nothing is written. Use them to answer "what do I have" and "what is at
the top of that root"; they never replace `ref resolve` for a path or a file.

`<cli> registry inventory [--json] [--public]` is the one view that walks every
registered root. Per root: id, kind, alias, status (the table above), the
registered path and `last_seen`; an unavailable root keeps its last known path
and shows no counts. An available hub lists its topics: slug, `wiki_id` (the
topic's own `config.md` first, else the registry row, else none), `private`,
`archived`, `status` (`available`, `missing`, `uninitialized`), counts of
`raw/` sources and `wiki/` articles (the master-index statistics rule) and the
newest `log.md` date. An available wiki shows the same counts and log date. A
**private topic is slug-only**: slug, id and counts, never a title, a
description, a path or a log date. `--json` is `{public, roots: [{id, kind,
alias, status, path, last_seen, private, declared_id?, forked_to?, reason?,
topics? | topics_error? | counts?, last_log?}], omitted?}`. `--public` leaves
private roots and private topics out entirely, drops `private`, and ends with
`omitted by --public: R private root(s), T private topic(s)` (`omitted:
{roots, topics}` in JSON).

`<cli> registry peek <target> [--json] [--public]` is target-first: it resolves
exactly one target and prints one index; it never scans the other roots. The
target is an id or alias, or a `knowmade://` reference.

| Target | Resolved by | Fail-closed outcomes |
|---|---|---|
| `hb-` / `wk-` id, or alias | the registry, as `registry locate` (a `wk-` id that only a registered hub carries, as `inventory` lists, is peeked as its reference) | none available: exit 1 with last known paths; several available: exit 1 `ambiguous` (peek by alias to pick one); unknown: exit 2 |
| `knowmade://<id>[/<path>]` | `ref resolve`'s replica rules (hub topics are found too) | unresolved, missing-target or divergent: exit 1; invalid reference or a file target: exit 2 |

A root or a directory prints its `_index.md` (a wiki's master index, a hub's
topic index); a hub without one prints the topic list of its `wikis.json`; a
directory without `_index.md` is exit 1. An `_index.md` that is a link leading
outside the root is refused. `--json` is `{target, id, kind (hub | wiki |
directory), status, root, index, source (_index.md | wikis.json), content,
truncated, public, topics? (hub), omitted? (--public)}`. Reading is capped at
1 MiB. Other roots are touched only for their identity marker and hub registry
(the privacy judgement and hub-topic discovery); their indexes and content are
never opened.

`--public` is judged over every replica of the target before anything is
resolved, as `ref resolve --public` does: a private target (a `-PRIVATE`
component of its real path, or a private topic of any available registered hub)
is exit 3 with nothing on stdout and no path on stderr. A hub's `_index.md` is
free text (titles, link targets and registry paths can name a private topic
without spelling `-PRIVATE` or its slug), so redacting its lines would fail
open: when the hub has any private topic, `--public` does not print that text at
all. It prints the topic list built from `wikis.json` with the private topics
left out and the omitted count, as `inventory --public` does (`source` is
`wikis.json`, and the JSON `topics` list only public topics). A hub whose
registry cannot be read is exit 3. A root that cannot be judged is treated as
private, and so is a registry row without a usable path. Below the hub root the
same holds for any directory outside a public topic (`topics/` itself, say):
when the hub has a private topic it is exit 3, because its index may list one.
Whatever a file holds, a control character never reaches the terminal raw; it
is printed escaped (`\x1b` and the like).

## Cross-root references

`knowmade://<root-id>/<path>#<fragment>` names a file or directory inside a
root by the root's stable id and not by a path, so the reference survives a
move. `<cli> ref` resolves it through the registry and the registered hubs. It
answers where an object is; it never rewrites a reference, writes the registry
or grants publication rights. Design: `DESIGN.md` section 4 and sections 11-13.

### URI grammar

- Scheme `knowmade://`, lowercase. The root is a `wk-` or `hb-` id plus 16
  lowercase hex digits, never a slug, alias or path. `knowmade://<id>` and
  `knowmade://<id>/` name the root itself.
- The path is `/`-separated, percent-encoded UTF-8, relative to the root.
  Refused with exit 2 and never normalised: an empty, `.` or `..` segment (also
  encoded), a trailing slash after a segment, a backslash, a `:`, a control
  character or an encoded `/` inside a segment, a `?` query, whitespace, and
  invalid UTF-8.
- The fragment is everything after the first `#`, carried unchanged and never
  used to select a file.
- In Markdown a reference is found in the frontmatter (raw text) and in the body
  outside code fences and inline code, as a link target, an autolink or a bare URI.

### Resolution

`<cli> ref resolve <uri> [--all | --for-write] [--replica <root path>] [--public] [--json]`
collects every replica of the id: registry entries with that id, then, for a
`wk-` id, the topics of every available registered hub whose registry row or
`config.md` declares it (the `wikis.local.json` overlay and nested `.wiki` are
honoured, archived topics included). One path reached two ways is one replica.
Each replica gets the registry statuses (`available`, `unavailable`, `mismatch`
with `forked -> <new id>`, `undeclared`); only `available` ones count. A slug is
never a key: two topics named `alpha` with different ids are unrelated.

| Result | Meaning | Exit |
|---|---|---|
| `resolved` | exactly one usable replica has the target | 0 |
| `equivalent` | several have it with the same `sha256:` digest (file bytes; a directory is its sorted relative paths and file hashes, symlinks and `.git` ignored) | 0 for a read; the first replica in registry order is printed |
| `divergent` | several have it, digests differ | 1 (`--all` lists them, exit 0) |
| `missing-target` | an available replica exists, none has the target (or a symlink leaves the root) | 1 |
| `unresolved` | no available replica, or an unknown id | 1, with the last known paths and `last_seen`, never a guess |
| `ambiguous` | `--for-write` with more than one usable replica | 1 |

For a read, a replica without the target is dropped (it shows as `missing-target`
in `--json` and `--all`). Stdout is the local path plus `#fragment`, or with
`--json` the object `{uri, id, kind, path, fragment, status, resolved, private,
replicas: [{root, status, target, digest, private, detail}]}`. A refusal prints
nothing on stdout and one reason on stderr. A malformed URI, a corrupt registry
or an unreadable hub is exit 2.

### Write rule

`--for-write` fails closed: more than one usable replica is `ambiguous` even when
they are equivalent, and a divergent target is refused. With exactly one usable
replica the target may be a file that does not exist yet. `--replica <root path>`
names the replica and overrides both (exit 2 when it is not an available replica
of the id). The git "fast-forward" lineage case, choosing a newer replica by
history, is not implemented: an equal digest is the only equivalence and anything
else is divergence.

### Privacy

A replica is private when its root lies in a `-PRIVATE` directory or is a hub
topic that is private by name, slug or the hub's private-topic rules (the
`privacy_boundary` resolver). `--public` exits 3 with nothing on stdout when any
known replica, available or not, is private; `--json` reports `private`.
`<cli> ref check` reports `private-target` (error) for a reference into a
private location from a file that is not itself inside one.

### Checking files

`<cli> ref check [paths...] [--root <dir>] [--fail-on error|warning] [--json]`
scans Markdown files (the given paths, or every `.md` under `--root` or the
resolved wiki root) and prints `path:line: severity code uri detail`, then
`ref check: N error, N warning (F files, R references)`.

| Code | Severity | Cause |
|---|---|---|
| `invalid-ref` | error | the URI does not parse |
| `private-target` | error | see Privacy |
| `divergent-ref` | error | the replicas of the target differ |
| `unresolved-ref` | warning | no available replica (offline is not broken) |
| `missing-target` | warning | replicas exist, none has the target |

Exit 1 when a finding reaches `--fail-on` (default `error`). `links check` and
`lint` treat `knowmade://` as an external link and never judge it; this check
does. `--json` is `{root, files, references, findings: [{path, line, severity,
code, uri, detail}], counts: {error, warning}}`.
