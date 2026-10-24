"""UTF-8 standard streams for piped consumers.

On Windows a Python process whose stdin/stdout/stderr are pipes inherits the
ANSI code page (for example cp1251). Codex and Claude hooks, the app-server
and CI all consume these commands through pipes and speak UTF-8, so a
non-ANSI filename or an emoji prompt raised ``UnicodeEncodeError`` /
``UnicodeDecodeError`` that nothing caught (emercons/knowmade#41).

Every ``main`` that prints calls :func:`configure_utf8_stdio` first. The
single-file ``scripts/llm-wiki-session`` hook inlines an equivalent because it
ships verbatim as a plugin hook and cannot import this module.
"""

from __future__ import annotations

import sys


def configure_utf8_stdio() -> None:
    """Switch stdin/stdout/stderr to UTF-8 when the streams support it.

    ``errors="replace"`` keeps machine JSON and human text from ever raising:
    undecodable input bytes become U+FFFD and lone surrogates on output are
    replaced instead of crashing. Streams without ``reconfigure`` (custom
    wrappers, closed or detached streams) are left untouched.
    """
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # Already read from, closed, or otherwise not reconfigurable.
            continue
