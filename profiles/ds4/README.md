# DS4 Query Profile

`../query-lite/SKILL.md` is the shared compact, read-only profile used by DS4,
Codex, Claude, Pi, and portable agents. It covers common query and
inventory-lookup paths without loading research, ingestion, compilation, and
maintenance instructions.

The simplest DS4 launch is:

```bash
./scripts/pi-ds4-wiki-query
```

The launcher uses isolated Pi state, writes a DS4 provider config only when one
does not exist, disables discovery, exposes only read tools, and supports
`--dry-run` for inspection.

With Pi, load the profile and its small query adapter, then expose only
read-only tools:

```bash
pi \
  --extension profiles/ds4/pi-query-tools.ts \
  --append-system-prompt profiles/query-lite/SKILL.md \
  --tools read,grep,find,ls
```

Keep the existing full `wiki-manager` skill for write-capable research and
maintenance. Do not give a local query profile write tools merely for
convenience; switch profiles when the requested workflow changes.

The DS4 benchmark runner uses the same file and tool surface:

```bash
./scripts/benchmark-token-efficiency ds4-live \
  --pi-command "pi" \
  --output benchmarks/results/ds4-lite.json
```

The benchmark loads `pi-query-tools.ts` automatically. Do not combine this
read-only profile with a general DS4 adapter that injects editing, shell, or
scaffolding instructions. `--extension` is reserved for transport adapters
that do not change the query contract.
