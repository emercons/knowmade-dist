"""Deterministic topic-guide (``schema.md``) control-plane domain.

This module owns schema status/adoption behavior. Markdown parsing, logging,
and starter schema rendering come from the shared ``markdown`` leaf module and
root selection from the shared ``roots`` resolver, so it does not depend on the
compatibility module ``legacy_cli`` (which imports ``run_schema`` from here).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .markdown import (
    append_log,
    default_schema_text,
    parse_frontmatter_block,
    split_markdown_frontmatter,
)
from .roots import is_hub, resolve_wiki_root_args


def schema_state_from_text(text: str) -> str:
    parts = split_markdown_frontmatter(text)
    if parts is None:
        return "present"
    frontmatter, _ = parts
    data = parse_frontmatter_block(frontmatter)
    state = data.get("schema_state") or data.get("status")
    return str(state or "present")


def schema_status_payload(root: Path) -> dict[str, Any]:
    schema = root / "schema.md"
    proposals = (
        sorted((root / "output").glob("schema-proposal-*.md"))
        if (root / "output").exists()
        else []
    )
    articles = (
        [
            path
            for path in (root / "wiki").glob("**/*.md")
            if path.name != "_index.md"
        ]
        if (root / "wiki").exists()
        else []
    )
    if schema.exists():
        try:
            state = schema_state_from_text(
                schema.read_text(encoding="utf-8", errors="replace")
            )
        except OSError:
            state = "present"
        status = "present"
    elif proposals:
        state = "proposed"
        status = "missing-with-proposal"
    else:
        state = "missing"
        status = "missing"
    return {
        "root": str(root),
        "status": status,
        "state": state,
        "schema": str(schema),
        "schema_exists": schema.exists(),
        "article_count": len(articles),
        "proposal_count": len(proposals),
        "proposals": [str(path) for path in proposals],
    }


def schema_migrate(
    root: Path,
    apply: bool,
    state: str,
    json_output: bool = False,
    command: str = "migrate",
) -> int:
    if state not in {"advisory", "strict"}:
        raise SystemExit("schema state must be advisory or strict")
    schema = root / "schema.md"
    payload = schema_status_payload(root)
    changed = False
    if schema.exists():
        action = "already-present"
    elif apply:
        schema.write_bytes(default_schema_text(root, state=state).encode("utf-8"))
        append_log(root, "schema", f"created starter {state} topic guide schema.md")
        changed = True
        action = "created"
        payload = schema_status_payload(root)
    else:
        action = "would-create"

    result = {
        "root": str(root),
        "schema": str(schema),
        "action": action,
        "changed": changed,
        "command": command,
        "state": payload["state"] if schema.exists() else state,
        "apply": apply,
    }
    if json_output:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    print(f"llm-wiki schema {command}: {root}")
    if action == "already-present":
        print(f"Topic guide already exists: {schema}")
        print("No changes applied.")
    elif action == "created":
        print(f"Created {state} topic guide: {schema}")
        print(
            "Next: review schema.md, trim unused starter conventions, then run "
            "librarian with topic-guide advice."
        )
    else:
        print(f"Would create default {state} topic guide: {schema}")
        if command == "adopt":
            print("Dry run only. Re-run without --dry-run to write schema.md.")
        else:
            print("Dry run only. Re-run with --apply to write schema.md.")
    return 0


def run_schema(args: Any) -> int:
    root = resolve_wiki_root_args(args)
    if is_hub(root):
        raise SystemExit("schema operations target a topic wiki, not the hub")
    subcommand = args.schema_command
    if not (root / "config.md").is_file():
        # A typo path or a directory that is not a wiki: one message and exit
        # 2 (a usage error, as `links` and `privacy` do), never a traceback or a
        # successful "missing" status (emercons/knowmade#103).
        message = f"not an initialized wiki: {root}"
        if args.json:
            error = {"command": subcommand, "error": message, "ok": False, "root": str(root)}
            print(json.dumps(error, indent=2, sort_keys=True))
        else:
            print(f"llm-wiki schema: {message}", file=sys.stderr)
        return 2
    if subcommand == "status":
        payload = schema_status_payload(root)
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"llm-wiki topic guide: {root}")
            print(f"State: {payload['state']}")
            print(
                "Topic guide: "
                f"{'present' if payload['schema_exists'] else 'missing'} "
                f"({payload['schema']})"
            )
            print(f"Articles: {payload['article_count']}")
            print(f"Topic-guide proposals: {payload['proposal_count']}")
            if not payload["schema_exists"]:
                print("Next: llm-wiki schema adopt")
        return 0
    if subcommand == "migrate":
        return schema_migrate(
            root,
            apply=args.apply,
            state=args.state,
            json_output=args.json,
            command="migrate",
        )
    if subcommand == "adopt":
        return schema_migrate(
            root,
            apply=not args.dry_run,
            state=args.state,
            json_output=args.json,
            command="adopt",
        )
    raise SystemExit("unknown schema subcommand")
