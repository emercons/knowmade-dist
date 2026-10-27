---
description: "Route to, register, inspect, validate, and run explicitly trusted local private adapters without putting their code or bulk data in the wiki."
argument-hint: "route --intent <effect> --resource <url>|add <path>|list|show <id>|doctor <id>|run <id> --request <json>|remove <id> --yes"
allowed-tools: Read, Write, Edit, Glob, Grep, Bash(ls:*), Bash(date:*), Bash(python3:*), Bash(scripts/llm-wiki:*), Bash(${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki:*)
---

## Your task

Manage or invoke a private llm-wiki adapter through the deterministic bundled
CLI. Read `skills/knowmade/references/adapters.md` (registry, CLI location,
routing, command list) before acting; the `If` branches below name the
references for the rarer paths.

Private-adapter management is wiki-neutral. Do not put executable registrations
or absolute machine paths in `wikis.json` or a topic wiki. Resolve a topic wiki
only when the user asks to promote a reviewed result after execution.

Private visibility is an access control, not permission to redistribute data or
perform protected-content collection, deanonymization, sensitive inference,
targeting, harassment, or other prohibited analysis.

## Declarative intent routing

Before treating an external URL as an ingestion source, normalize the requested
effect to a lowercase intent token and run:

```bash
${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki adapter route \
  --intent <effect> --resource '<external-url>' --json
```

On `matched`, read the returned adapter-owned guide, then run `show` and
`doctor` for the returned adapter id before following that workflow. The private
adapter owns all provider-specific authentication, transport, planning,
recovery, and verification instructions. On `no-match`, continue with normal
wiki routing. On `ambiguous` or `unavailable`, fail closed and repair the
registration instead of guessing an adapter or falling back to an ungoverned
write path. Route output does not echo the resource.

A URL by itself never authorizes invented edits. A bounded imperative approves
only a faithful plan; generic remote-write plan hashes, revision locks,
idempotency keys, private receipts, and read-back verification remain required.

## Locate the CLI

For Claude Code, use:

```bash
"${CLAUDE_PLUGIN_ROOT}/bin/llm-wiki" adapter <subcommand>
```

In a source checkout, use `scripts/llm-wiki`. Other runtimes should use the
bundled `bin/llm-wiki` relative to the installed plugin root described in the
adapter reference. Do not assume the command is globally installed.

## Management operations

- `add <path>`: require an existing local checkout and explicit read/write
  roots. Add exact `--remote-resource` values only when the user authorized
  them. Never clone a repo or request credentials. Use `--replace` only when the
  user intends to trust a changed manifest.
- `list`: show compact id, version, capability, network, and local-root data.
- `show <id>`: show the machine-local registration; do not print environment
  variable values.
- `route --intent <effect> --resource <url>`: match only registered declarative
  routes and return the adapter-owned guide without echoing the resource.
- `doctor <id>`: verify manifest hash, executable, and handshake before a run.
- `remove <id> --yes`: remove only the registration. Never delete adapter code,
  inputs, or outputs.

## If a route matched or the user names an adapter

Read `skills/knowmade/references/adapter-routes.md`: the interactive
authorization handoff when the adapter guide offers a local helper, and the
explicit-only named adapter (`wiki skill-factory <request>`).

## If you add, replace or review a registration

Read `skills/knowmade/references/adapter-authoring.md` before `add` or
`add --replace`: the content-free code plane, naming, the manifest and route
schema, and the security limits that bound what registering a checkout trusts.

## If you run an operation

Read `skills/knowmade/references/adapter-run.md` first: the request and
response documents, the remote-write request and receipt, and the wiki
promotion boundary.

1. Inspect the request JSON without opening referenced corpora or secrets.
2. Confirm the adapter id, operation, declared paths, and requested output
   directory match the user's intent.
3. Run `doctor`; stop on any issue.
4. For `remote-write`, confirm the user explicitly approved the exact plan,
   expected revision, target resource, and idempotency key. Invoke with
   `--approve-remote-write <plan-sha256>` and a private `--response` path inside
   the registered write root. Never infer approval from a request file alone.
5. Invoke `adapter run <id> --request <absolute-path> --json`.
6. Report the run id, bounded summary, and artifact counts by class.
7. Do not import anything automatically.

If the user asks to update the wiki, inspect only `wiki-safe` artifacts. Review
them as candidates, keep `private` and `bulk` artifacts external, write the
smallest useful raw note/evidence packet, compile bounded conclusions, update
indexes, and append the normal topic and hub logs.
