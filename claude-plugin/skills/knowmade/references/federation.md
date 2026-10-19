# Federation Registry

Read this only when the user asks which hubs or wikis this machine knows, or
where a wiki with a given id lives now. `<cli>` is the bundled `bin/llm-wiki`
(`scripts/llm-wiki` in a source checkout). The registry is machine state: it
never changes how `hub_path`, `LLM_WIKI_HUB`, `--hub` or the current directory
select a hub, and it is not a source of truth. A root's own `hub_id` /
`wiki_id` decides; every status is recomputed from the root. Flags:
`references/cli.md`. Design: `DESIGN.md` sections 4, 9 and 12.

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
