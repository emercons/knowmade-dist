# Collection Setup Protocols

The rare branches of `/knowmade:ingest-collection`, moved out of the command so
they are read only when taken: the fit check against inventory and dataset
records, and the by-hand fallback when the CLI cannot run. Read the section for
the branch in hand, not the file top to bottom. The adapters are in
[`collection-adapters.md`](collection-adapters.md); `--new-topic` uses
`llm-wiki init --topic` and, if the CLI cannot run,
[`wiki-setup.md`](wiki-setup.md) § Init manual fallback.

## Fit check: inventory and dataset awareness

Before writing a large collection, be explicit about fit:

- If the user only wants to remember a possible collection for later, create or
  suggest one inventory `corpus` record instead of ingesting.
- If the collection is row-like data that should be queried in place, create or
  suggest a dataset manifest plus one linked inventory record.
- If the user is about to ingest hundreds of child sources, show the collection
  manifest shape, estimated child count, and any inventory/dataset companion
  record before asking for confirmation.

After ingesting a collection, if a matching inventory record exists, link the
raw collection manifest from that record and report the recommended
status/next action update.

## CLI unavailable: indexes and log by hand

Only when `llm-wiki` cannot run. Rebuild the affected raw indexes from the
frontmatter (never hand-edit hundreds of table rows one by one) with
[`indexing.md`](indexing.md) § Index Update Procedure and the formats in
[`wiki-format.md`](wiki-format.md), then the master `_index.md` statistics
([`ingestion.md`](ingestion.md) § Post-Ingestion Index Updates, items 4 and 5)
and the `log.md` entry:

```
## [YYYY-MM-DD] ingest-collection | <collection> via <adapter>: N new, M skipped, K total candidates
```
