"""The one ``llm-wiki`` parser: namespace registration and dispatch.

``NAMESPACES`` is the single registration point of the deterministic CLI. Each
entry names one top-level namespace and, as strings, the module and the
function that register it: ``register_parser(subparsers)`` adds the ``name``
subparser with its arguments, binds ``func`` (via ``set_defaults``) to the
namespace's entry point, and returns the subparser. The module is imported
only when its namespace is registered. ``build_parser()`` registers every
namespace in list order, which is the order ``llm-wiki --help`` lists them;
``build_parser(only=name)`` registers just that one, and ``main`` uses it when
the first argument is a namespace name, so a call pays for one namespace and
not for all of them. ``main`` dispatches to ``args.func``.

Adding a namespace is one entry here plus its module. Run
``scripts/generate-cli-reference.py`` afterwards: the lane checks the generated
``references/cli.md`` against this parser.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from typing import Any, NamedTuple

from . import roots as _roots
from .fsio import permission_denied_message
from .stdio import configure_utf8_stdio


class Namespace(NamedTuple):
    """One top-level ``llm-wiki`` namespace and where its registration lives."""

    name: str
    module: str  # module of this package, imported only when the namespace registers
    attribute: str  # function in ``module``: ``attribute(subparsers)`` returns the subparser

    def register_parser(self, subparsers: Any) -> argparse.ArgumentParser:
        module = importlib.import_module(f"{__package__}.{self.module}")
        return getattr(module, self.attribute)(subparsers)


def _register_source(subparsers: Any) -> argparse.ArgumentParser:
    # Late-bound so the shared roots resolver is looked up when a command runs.
    from . import raw_note, roots, source_lifecycle

    return source_lifecycle.register_parser(
        subparsers,
        resolve_wiki_root=lambda args: roots.resolve_wiki_root_args(args),
        extensions=(raw_note.register_note,),
    )


NAMESPACES: tuple[Namespace, ...] = (
    Namespace("lint", "lint", "register_parser"),
    Namespace("archive", "hub", "register_archive_parser"),
    Namespace("schema", "hub", "register_schema_parser"),
    Namespace("identity", "hub", "register_identity_parser"),
    Namespace("hub", "hub_index", "register_parser"),
    Namespace("inbox", "inbox", "register_parser"),
    Namespace("registry", "federation", "register_parser"),
    Namespace("ref", "xref", "register_parser"),
    Namespace("retract", "retract", "register_parser"),
    Namespace("specialist", "specialists", "register_parser"),
    Namespace("adapter", "adapters", "register_parser"),
    Namespace("checkpoint", "checkpoints", "register_parser"),
    Namespace("privacy", "privacy", "register_parser"),
    Namespace("links", "links", "register_parser"),
    Namespace("index", "index_cli", "register_parser"),
    Namespace("log", "log_cli", "register_parser"),
    Namespace("dataset", "datasets", "register_parser"),
    Namespace("rules", "rules", "register_parser"),
    Namespace("engine", "pins", "register_engine_parser"),
    Namespace("pin", "pins", "register_pin_parser"),
    Namespace("freshness", "freshness", "register_parser"),
    Namespace("project", "projects", "register_parser"),
    Namespace("adopt", "adopt_cli", "register_parser"),
    Namespace("source", "cli", "_register_source"),
    Namespace("status", "status", "register_parser"),
    Namespace("compile", "compile_plan", "register_parser"),
    Namespace("init", "init_cli", "register_parser"),
)


def build_parser(only: str | None = None) -> argparse.ArgumentParser:
    """The full parser, or with ``only=<namespace>`` one that registers just that namespace."""
    parser = argparse.ArgumentParser(
        prog="llm-wiki",
        description="Local deterministic helpers for Knowmade.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for namespace in NAMESPACES:
        if only is None or namespace.name == only:
            namespace.register_parser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    # The top-level parser has no option but -h, so a first argument that names
    # a namespace selects it; anything else (no arguments, -h, a typo) gets the
    # full parser and so the full help and "invalid choice" listing.
    values = list(sys.argv[1:] if argv is None else argv)
    only = values[0] if values and values[0] in {n.name for n in NAMESPACES} else None
    if only is None:
        args = build_parser().parse_args(values)
    else:
        args, extras = build_parser(only).parse_known_args(values)
        if extras:
            # An argument the namespace does not know is reported by the full
            # parser, so its usage line lists every namespace as it always did.
            args = build_parser().parse_args(values)
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
