#!/usr/bin/env python3
"""Compatibility shim over the deterministic Knowmade CLI.

The parser and dispatch live in ``cli`` (``NAMESPACES`` is the one
registration point); every namespace lives in its own bounded module. This
module keeps ``python -m knowmade_control.legacy_cli`` working, re-exports
``build_parser`` and ``main`` from ``cli``, and re-exports exactly the names
that callers outside the package still reach through it. Everything else is
imported from its owning module.
"""

from __future__ import annotations

import sys

from .cli import build_parser, main

# Compatibility re-exports, referenced by:
#   tests/test-control-plane-roots.sh  the roots names below, including the
#       legacy aliases resolve_hub, resolve_wiki_root and scoped_wiki_slug
#       (asserted identical to the roots functions)
#   tests/test-local-cli-lint.sh  through ``runpy.run_path(scripts/llm-wiki)``,
#       whose ``from knowmade_control.legacy_cli import *`` exposes __all__:
#       normalize_windows_absolute_path, markdown_relative_link, LintContext,
#       read_registry, write_registry, index_links_to, resolve_link_target,
#       is_local_markdown_link
#   tests/test_cli_subcommands.py  read_registry, write_registry,
#       archive_topic, restore_topic
# scripts/llm-wiki-session imports nothing from the package (it vendors its
# helpers), and the launchers and scripts/generate-cli-reference.py use ``cli``.
# Remove a name here only together with its last caller.
from .common import read_registry, write_registry
from .hub import archive_topic, restore_topic
from .indexing import index_links_to, is_local_markdown_link
from .lint_links import markdown_relative_link, resolve_link_target
from .lint_report import LintContext
from .roots import (
    expand_leading_tilde,
    home_directory,
    initialized_wiki_root,
    is_archived_registry_entry,
    is_hub,
    is_permission_denied,
    normalize_windows_absolute_path,
    permission_denied_message,
    resolve_hub_args as resolve_hub,
    resolve_registry_path,
    resolve_wiki_root_args as resolve_wiki_root,
    scoped_wiki_slug_args as scoped_wiki_slug,
    windows_absolute_path,
)

__all__ = [
    "LintContext",
    "archive_topic",
    "build_parser",
    "expand_leading_tilde",
    "home_directory",
    "index_links_to",
    "initialized_wiki_root",
    "is_archived_registry_entry",
    "is_hub",
    "is_local_markdown_link",
    "is_permission_denied",
    "main",
    "markdown_relative_link",
    "normalize_windows_absolute_path",
    "permission_denied_message",
    "read_registry",
    "resolve_hub",
    "resolve_link_target",
    "resolve_registry_path",
    "resolve_wiki_root",
    "restore_topic",
    "scoped_wiki_slug",
    "windows_absolute_path",
    "write_registry",
]


if __name__ == "__main__":
    sys.exit(main())
