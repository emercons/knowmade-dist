# Private Adapter Runs

The execution side of the private-adapter protocol, moved out of
[`adapters.md`](adapters.md) so it is read only before a `run` or before a result is
considered for the wiki. The registry, the CLI and routing are in `adapters.md`; the
manifest an operation is declared in is in [`adapter-authoring.md`](adapter-authoring.md),
whose § Security properties and limits (allowlisted environment, Windows path
normalization) also governs every run.

## Remote side effects

The control plane can also govern remote side effects. Remote resources must be
registered exactly, every operation declares `remote-read` and/or
`remote-write`, and remote writes require an approved plan hash, expected
revision, stable idempotency key, private response receipt, and adapter-supplied
read-back verification. These controls authorize a bounded operation; they do
not make the adapter process an operating-system sandbox.

## Request and response

Request:

```json
{
  "protocol": "llm-wiki-adapter/v1",
  "adapter_id": "example-analysis",
  "operation": "analyze",
  "arguments": {"case": "/absolute/private/case.json"},
  "output_dir": "/absolute/private/results/run-1",
  "options": {}
}
```

Response:

```json
{
  "protocol": "llm-wiki-adapter/v1",
  "adapter_id": "example-analysis",
  "adapter_version": "1.0.0",
  "operation": "analyze",
  "status": "ok",
  "run_id": "stable-run-id",
  "summary": {},
  "artifacts": [
    {
      "path": "evidence-packet.json",
      "sha256": "64-lowercase-hex-characters",
      "class": "wiki-safe",
      "media_type": "application/json",
      "role": "evidence-packet"
    }
  ]
}
```

Path arguments declared by the operation must be absolute, must exist, and
must stay inside registered read roots. `output_dir` and every returned
artifact must stay inside registered write roots. Artifact files and SHA-256
values are verified after execution.

### Remote-write request and receipt

Remote resource arguments contain opaque, scheme-prefixed identifiers such as
`hosted-document:<resource-id>`. Every value must exactly match one registered with
`adapter add --remote-resource`; remote-resource identifiers are never inferred
from local files or adapter output.

A remote-write request adds:

```json
{
  "arguments": {
    "document_resource": "hosted-document:<resource-id>",
    "plan": "/absolute/private/plan.json"
  },
  "remote_write": {
    "plan_sha256": "64-lowercase-hex-characters",
    "idempotency_key": "caller-stable-key-0001",
    "expected_revision": "remote-revision-from-plan"
  }
}
```

The CLI hashes the declared `approved_plan_argument` itself. Execution requires
`--approve-remote-write <same-plan-sha256>` and `--response` inside a registered
write root. A successful response must include a matching `remote_receipt` with
the exact resources, plan hash, idempotency key, before/after revisions, and
`verification.status: verified`. The complete mode-0600 response stays private;
terminal JSON is redacted and reports only status and counts.

## Wiki promotion boundary

Adapters never receive a wiki write path and must declare `writes_wiki: false`.
Execution only produces external artifacts and a verified receipt. It never
imports content into `raw/`, `wiki/`, `output/`, inventory, or datasets.
The executable may read authorized external inputs and write authorized
external runtime results, but it does not own, embed, publish, or automatically
surface that content. Inputs and outputs belong to the separately controlled
data plane.
This is a protocol and workflow boundary, not an operating-system sandbox:
explicitly registered adapter code runs with the permissions of its parent
process, so register only code you trust.

After a successful run:

1. inspect only artifacts labeled `wiki-safe`;
2. treat that label as an import candidate, not proof of correctness;
3. review content, provenance, identifiers, and privacy before writing;
4. keep `private` and `bulk` artifacts outside the wiki;
5. create a compact immutable raw note or repository/run note when useful;
6. compile bounded conclusions through the normal workflow; and
7. append the resulting wiki write to topic and hub logs.

Never auto-promote adapter output. Never infer that private repository access
authorizes redistribution, protected-content access, sensitive inference, or
publication of corpus-derived individual data.
