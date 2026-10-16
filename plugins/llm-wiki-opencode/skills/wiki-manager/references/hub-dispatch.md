# Hub Inbox Dispatch

The Global Hub Inbox Dispatch workflow, moved verbatim out of `ingestion.md` so a
plain `wiki ingest` does not load it. Topic-level ingestion (the worker side of
this workflow) stays in [`ingestion.md`](ingestion.md).

## Global Hub Inbox Dispatch

Use this workflow when the user wants a single place to dump research results
before the agent decides which topic wiki should own them.

### Human Workflow

Drop one source or research result per file into `HUB/inbox/`. Markdown is best
for ChatGPT deep-research exports because titles, headings, links, and notes are
visible to the dispatcher. URLs may be saved as `.url` files or small markdown
captures. PDFs and other files are acceptable; the dispatcher may need to use
filename and nearby notes when content extraction is expensive.

Optional filename hints help but are not required:

- `medicine - gut-brain deep research.md`
- `business - pricing benchmark.md`
- `unknown - chatgpt export.md`

The human does not need to choose the topic wiki up front. The whole point of
`HUB/inbox/` is fast capture first, careful routing later.

### Dispatcher Workflow

The dispatcher is a lightweight first pass. It should classify and route; it
should not compile articles directly from the hub inbox.

1. Resolve **HUB** and list `HUB/inbox/`, ignoring `.routed/`,
   `.needs-human/`, `.skipped/`, and hidden/system files.
2. Read `HUB/wikis.json`, active topic `_index.md` files, and relevant
   `config.md` scopes. Read private `goals.md` only to decide whether an item
   belongs in a private sibling, and never quote private goals in public routing
   notes.
3. For each inbox item, inspect enough content to identify subject matter,
   privacy level, source type, and whether it spans multiple topics.
4. Produce a routing table with: item, candidate topic(s), public/private
   target, confidence, reason, and action (`route`, `copy-to-multiple`,
   `needs-human`, `skip`, or `create-topic-candidate`).
5. High-confidence single-topic items may be moved or copied to
   `HUB/topics/<slug>/inbox/` or `HUB/topics/<slug>-PRIVATE/inbox/`.
   Ambiguous, sensitive, or personal items default to the private sibling when
   it exists.
6. If an item materially belongs to multiple unrelated topics, copy it to each
   target topic inbox and preserve the same original provenance note.
7. If no suitable topic exists, leave the item in `HUB/inbox/` or move it to
   `.needs-human/`, and optionally create an inventory candidate in the most
   plausible private topic or propose a new topic wiki.
8. After routing, move the original to `HUB/inbox/.routed/` only after every
   target copy exists. Append a `dispatch` entry to `HUB/log.md`.

When copying routed material, preserve provenance. For markdown captures, add
or preserve frontmatter such as `routed_from`, `route_reason`, and
`route_confidence` when doing so does not damage the capture. For binary or
opaque files, write a small sidecar note next to the copied inbox item.

### Worker Workflow

After dispatch, worker agents operate inside one topic wiki at a time. They run
the normal topic `inbox/` ingestion flow: fetch/read, extract metadata, write
immutable `raw/` artifacts, move topic inbox items to `inbox/.processed/`, then
compile selectively. Workers should keep topic context isolated; medicine and
business sources do not share context just because they entered through the same
hub inbox.

Public/private routing stays conservative. If a routed item includes personal
goals, private constraints, private research questions, unpublished business
context, or uncertain privacy, route to `<slug>-PRIVATE`. Public topic inboxes
receive only material that is clearly safe to publish or explicitly intended
for publication.
