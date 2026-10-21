"""The one ``llm-wiki`` parser: namespace registration and dispatch.

``NAMESPACES`` is the single registration point of the deterministic CLI. Each
entry names one top-level namespace and the function that registers it:
``register_parser(subparsers)`` adds the ``name`` subparser with its arguments,
binds ``func`` (via ``set_defaults``) to the namespace's entry point, and
returns the subparser. ``build_parser`` adds them in list order, which is the
order ``llm-wiki --help`` lists them; ``main`` dispatches to ``args.func``.

Adding a namespace is one entry here plus its module. Run
``scripts/generate-cli-reference.py`` afterwards: the lane checks the generated
``references/cli.md`` against this parser.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from typing import Any, NamedTuple

from . import adapters as _adapters
from . import adopt_cli as _adopt_cli
from . import checkpoints as _checkpoints
from . import datasets as _datasets
from . import federation as _federation
from . import freshness as _freshness
from . import hub as _hub
from . import hub_index as _hub_index
from . import inbox as _inbox
from . import index_cli as _index_cli
from . import init_cli as _init_cli
from . import lint as _lint
from . import links as _links
from . import log_cli as _log_cli
from . import pins as _pins
from . import privacy as _privacy
from . import projects as _projects
from . import raw_note as _raw_note
from . import retract as _retract
from . import roots as _roots
from . import rules as _rules
from . import source_lifecycle as _source_lifecycle
from . import specialists as _specialists
from . import status as _status
from . import xref as _xref
from .fsio import permission_denied_message
from .stdio import configure_utf8_stdio


class Namespace(NamedTuple):
    """One top-level ``llm-wiki`` namespace and the function that registers it."""

    name: str
    register_parser: Callable[[Any], argparse.ArgumentParser]


def _register_source(subparsers: Any) -> argparse.ArgumentParser:
    # Late-bound so the shared roots resolver is looked up when a command runs.
    return _source_lifecycle.register_parser(
        subparsers,
        resolve_wiki_root=lambda args: _roots.resolve_wiki_root_args(args),
        extensions=(_raw_note.register_note,),
    )


NAMESPACES: tuple[Namespace, ...] = (
    Namespace("lint", _lint.register_parser),
    Namespace("archive", _hub.register_archive_parser),
    Namespace("schema", _hub.register_schema_parser),
    Namespace("identity", _hub.register_identity_parser),
    Namespace("hub", _hub_index.register_parser),
    Namespace("inbox", _inbox.register_parser),
    Namespace("registry", _federation.register_parser),
    Namespace("ref", _xref.register_parser),
    Namespace("retract", _retract.register_parser),
    Namespace("specialist", _specialists.register_parser),
    Namespace("adapter", _adapters.register_parser),
    Namespace("checkpoint", _checkpoints.register_parser),
    Namespace("privacy", _privacy.register_parser),
    Namespace("links", _links.register_parser),
    Namespace("index", _index_cli.register_parser),
    Namespace("log", _log_cli.register_parser),
    Namespace("dataset", _datasets.register_parser),
    Namespace("rules", _rules.register_parser),
    Namespace("engine", _pins.register_engine_parser),
    Namespace("pin", _pins.register_pin_parser),
    Namespace("freshness", _freshness.register_parser),
    Namespace("project", _projects.register_parser),
    Namespace("adopt", _adopt_cli.register_parser),
    Namespace("source", _register_source),
    Namespace("status", _status.register_parser),
    Namespace("init", _init_cli.register_parser),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llm-wiki",
        description="Local deterministic helpers for Knowmade.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for namespace in NAMESPACES:
        namespace.register_parser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except _roots.WikiNotResolved as exc:
        print(exc, file=sys.stderr)
        return 2
    except PermissionError as exc:
        print(permission_denied_message(None, "access wiki files", exc), file=sys.stderr)
        return 2
    except BrokenPipeError:
        return 1


if __name__ == "__main__":
    sys.exit(main())
