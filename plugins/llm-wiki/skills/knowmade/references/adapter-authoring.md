# Private Adapter Authoring

The registration and manifest side of the private-adapter protocol, moved out of
[`adapters.md`](adapters.md) so it is read only when an adapter is added, replaced or
reviewed. The registry, the CLI and routing are in `adapters.md`; the request and response
documents are in [`adapter-run.md`](adapter-run.md).

## Code plane

An adapter repository is always a **tool-only, content-free code plane**. It
may contain executable code, manifests, schemas, documentation, tests, and
synthetic fixtures generated in temporary test directories. It must never
contain real source content, source-specific case configuration, corpora,
captures, documents, recordings, transcripts, indexes, credentials, generated
results, evidence packets, or source-specific identifiers. Private visibility
is defense in depth, not permission to mix code and content.

## Repository naming

Private adapter repositories use the prefix convention:

```text
llm-wiki-adapter-<domain>-<capability>
```

The manifest id is the stable lowercase kebab-case portion after the prefix:

```text
<domain>-<capability>
```

For example, repository `llm-wiki-adapter-x-community-intelligence` declares
manifest id `x-community-intelligence`. Do not put `private`, an owner name, or
a version in either name. Reserve `intelligence` for substantive analysis;
use narrower terms such as `sync`, `indexing`, `validation`, or
`transcription` when they describe the tool more accurately. Content
repositories remain separately named, for example `bitcoin-wiki`.

## Adapter manifest

Every adapter root contains `.llm-wiki-adapter.json`:

```json
{
  "protocol": "llm-wiki-adapter/v1",
  "id": "example-analysis",
  "version": "1.0.0",
  "distribution": "private",
  "entrypoint": [".venv/bin/python", "-m", "example.adapter"],
  "capabilities": ["corpus-validation", "analysis"],
  "routes": [
    {
      "id": "edit-hosted-document",
      "intents": ["edit", "revise", "update"],
      "resource": {
        "kind": "url",
        "schemes": ["https"],
        "hosts": ["editor.example.invalid"],
        "path_prefixes": ["/document/"]
      },
      "guide": "AGENT_WORKFLOW.md",
      "priority": 50
    }
  ],
  "network": "none",
  "writes_wiki": false,
  "operations": {
    "analyze": {
      "read_arguments": ["case"],
      "requires_output_dir": true
    },
    "apply": {
      "read_arguments": ["plan"],
      "remote_resource_arguments": ["document_resource"],
      "requires_output_dir": true,
      "approved_plan_argument": "plan",
      "effects": ["remote-read", "remote-write"]
    }
  },
  "output_classes": ["wiki-safe", "private", "bulk"]
}
```

The entrypoint must implement:

```text
<entrypoint> describe
<entrypoint> execute --request <json-path> --response <json-path>
```

`describe` prints one JSON object matching the manifest identity, version, and
capabilities. `execute` writes exactly one response document. Diagnostic output
may go to stderr, but llm-wiki does not persist or echo it by default.

`routes` is optional. Each route declares one or more lowercase intent tokens,
an exact URL matcher, a relative adapter-owned Markdown guide, and an integer
priority from 0 to 1000. URL matching supports exact schemes and hosts plus
path prefixes; manifests do not get arbitrary regular expressions. The guide
must exist inside the adapter root. Registrations at the same highest priority
are ambiguous and fail closed rather than choosing by accident.

## Security properties and limits

- Execution uses an argv array with `shell=False` and a timeout.
- Only a small base environment plus explicitly allowlisted variable names is
  passed; secret values are not stored in the registry. On Windows the base
  set also carries the non-secret variables the platform needs to start
  programs and place per-user state (`PATHEXT`, `COMSPEC`, `SYSTEMDRIVE`,
  `WINDIR`, `USERPROFILE`, `HOMEDRIVE`, `HOMEPATH`, `APPDATA`, `LOCALAPPDATA`,
  `PROGRAMDATA`, `ALLUSERSPROFILE`); credentials are still dropped unless
  registered with `--env`.
- On Windows a relative entrypoint may use `\` or `/` and must stay inside the
  adapter directory, and registered or requested paths written MSYS-style
  (`/c/Users/...`) or with backslashes are read as the drive paths they spell.
  On POSIX a backslash remains an ordinary filename character.
- Manifest drift, path escape, missing artifacts, and hash mismatch fail closed.
- Remote-resource escape, missing explicit approval, plan-hash mismatch, missing
  idempotency/revision controls, unscoped receipt paths, and unverified remote
  success responses fail closed.
- Adapter code, installation, dependencies, authentication, and updates remain
  the adapter owner's responsibility.
- The outer operating-system sandbox remains the actual enforcement boundary;
  registry scopes are an additional validation layer, not a sandbox escape.
- Network use must be declared as `none`, `optional`, or `required` and still
  requires the runtime's normal network permissions.
