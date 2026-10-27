# Private Adapter Protocol

## Purpose

Private adapters let llm-wiki invoke locally installed research tools without
publishing their source code, credentials, corpora, or generated bulk data.
The public llm-wiki project defines the `llm-wiki-adapter/v1` control-plane
contract; adapter implementations remain in their own local or private repos.

This is separate from `ingest-collection --adapter`. Collection adapters are
agent instructions for known upstream formats. Private adapters are explicitly
registered executables with a machine-local trust and path policy.

## Registry

Registrations live at:

```text
~/.config/llm-wiki/adapters.json
```

The registry is machine-local, mode `0600`, and never belongs in `wikis.json`,
the hub, a topic wiki, session context, or a public repository. It stores local
paths, argv arrays, declared capabilities, allowed environment-variable names,
local read/write roots, and exact remote resource identifiers. It never stores
environment-variable values. Normal `adapter list` output reports only the
remote-resource count; `adapter show` exposes the exact machine-local policy.

Registration is explicit and local. llm-wiki does not clone, install, update,
publish, or mirror adapter repositories.

## Locate the bundled CLI

Use the bundled `bin/llm-wiki` from the installed plugin root. Claude Code
replaces the braced placeholder `${CLAUDE_PLUGIN_ROOT}` with that root as text
when it loads a command or skill file (body and `allowed-tools`); it does not
export it to the shell and does not touch files read later, such as these
references, so carry the resolved path over from the command. Codex supplies the installed skill path, whose plugin
root is two levels above `skills/knowmade/SKILL.md`; OpenCode uses its loaded
plugin path. In a source checkout, use `scripts/llm-wiki`.

Do not assume a globally installed `llm-wiki` executable. In examples below,
`LLM_WIKI` means the resolved bundled or source-checkout executable.

## Intent routing

Route by the requested effect and target before applying normal wiki URL
rules. A URL is not automatically an ingestion request when the user is asking
an external tool to act on that resource. Normalize the requested effect to a
lowercase intent token, then ask the registry before ingestion:

```bash
$LLM_WIKI adapter route --intent edit --resource '<external-url>' --json
```

A `matched` result identifies the trusted adapter and an adapter-owned workflow
guide. Read that guide, run `adapter doctor <id>`, and follow only its bounded
workflow. This lookup applies to external-resource actions even when the user
does not mention llm-wiki explicitly. Provider authentication, browser setup,
planning constraints, recovery, and verification belong in the private adapter
guide, not in the public wiki plugin. A `no-match` result permits normal
routing. `ambiguous` or `unavailable` fails closed until the registrations are
repaired.

Route lookup never echoes the resource. A URL without a concrete requested
effect is not write authorization, and a bounded instruction authorizes only a
faithful plan. All generic remote-write controls
([`adapter-run.md`](adapter-run.md)) still apply regardless of provider.

## Commands

```bash
$LLM_WIKI adapter add /private/adapter \
  --read-root /private/input \
  --write-root /private/results \
  --remote-resource hosted-document:<resource-id>
$LLM_WIKI adapter list
$LLM_WIKI adapter show example-analysis
$LLM_WIKI adapter route --intent edit --resource '<external-url>' --json
$LLM_WIKI adapter doctor example-analysis
$LLM_WIKI adapter run example-analysis --request /private/request.json --json
$LLM_WIKI adapter run example-analysis \
  --request /private/apply-request.json \
  --response /private/results/apply-receipt.json \
  --approve-remote-write <plan-sha256> --json
$LLM_WIKI adapter remove example-analysis --yes
```

`add` trusts an existing checkout but does not fetch it. `doctor` verifies the
manifest hash, executable, and `describe` handshake. Manifest drift blocks
execution until the user explicitly re-registers with `add --replace`.

## Rest of the protocol

The rare branches are in three references, read only when the branch is taken:

- [`adapter-routes.md`](adapter-routes.md): a matched route's guide and the interactive
  authorization handoff; the explicit-only named adapter (`wiki skill-factory`).
- [`adapter-authoring.md`](adapter-authoring.md): the content-free code plane, repository
  naming, the `.llm-wiki-adapter.json` manifest and route schema, security properties and
  limits. Read it before `add`, `add --replace`, or reviewing a manifest.
- [`adapter-run.md`](adapter-run.md): the request and response documents, the remote-write
  request and receipt, and the wiki promotion boundary. Read it before `run` and before
  importing any result into a wiki.
