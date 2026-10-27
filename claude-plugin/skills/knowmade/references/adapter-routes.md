# Private Adapter Routes And Named Adapters

The two ways a registered adapter's own guide takes over, moved out of
[`adapters.md`](adapters.md) so they are read only once `adapter route` matched or the
user named an adapter. `adapter route` itself is in `adapters.md` § Intent routing.

## Interactive authorization handoff

Follow the registered adapter's guide rather than inventing a generic login
flow. When that guide provides an agent-started authorization helper with a
short loopback or local handoff link:

1. start the helper yourself in a persistent shell process;
2. keep it running while the user completes authorization;
3. present only the short handoff link on its own line;
4. wait for the helper to finish, then retry the bounded read; and
5. keep authentication separate from write approval.

Do not ask the user to change into an adapter checkout, paste a repository path,
or reconstruct a long command containing the resource URL. Do not print, wrap,
or relay a long provider OAuth or signed URL when the adapter offers a short
local handoff; terminal wrapping and TUI padding can corrupt it. If the guide
does not provide such a helper, follow its documented fallback exactly rather
than creating an unreviewed proxy.

## Explicit named adapter invocation

A registered adapter with no URL routes may be deliberately explicit-only.
Use it only when the user names the adapter in declarative wiki syntax. The
initial supported case is:

```text
wiki skill-factory <skill request>
```

For this syntax, do not call `adapter route` with a fabricated resource. Use
`adapter show skill-factory` to resolve the machine-local registration,
run `adapter doctor skill-factory`, and read the adapter-owned workflow guide
from its registered root. Stop if the adapter is missing, its manifest has
drifted, or the guide is missing. A normal request to create, review, or discuss
a skill is not enough to select the adapter; the declarative name or an equally
explicit “use the skill-factory adapter” instruction is required.

Explicit-only availability is not ambient enablement. The adapter has no route,
receives no automatic wiki context, and gains no tool or write authority. The
`skill-factory` adapter must keep `writes_wiki: false`, use registered external
input/output roots, and emit candidates in disabled state. A successful run may
not install a package, edit `HUB/.skills/registry.json`, enable a topic, commit,
or publish. Those remain separate authorized actions after evaluation and
human acceptance.
