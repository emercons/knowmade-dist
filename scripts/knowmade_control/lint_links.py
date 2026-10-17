"""Link resolution and the C4 link-integrity checks.

Local Markdown links in ``wiki/`` and ``inventory/`` (``check_links``),
``[[wiki-links]]`` resolved by path relative to the linking file and then by
slug or alias (``check_wikilinks``, emercons/knowmade#107), and footnote
integrity (``check_footnotes``). ``resolve_link_target`` and
``markdown_relative_link`` are the two link primitives the index and coverage
repairs in ``lint_docs`` use too. Link destinations are normalised by
``indexing`` (``link_path_variants``, ``is_local_markdown_link``,
``index_links_to``), which the index renderers share. Split out of ``lint``
(emercons/knowmade#105 R3).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .common import as_string_list, markdown_link_destination
from .indexing import extract_link_destinations, is_local_markdown_link, link_path_variants
from .lint_report import LintContext, document_text, markdown_files
from .markdown import split_markdown_frontmatter
from .roots import windows_absolute_path


def resolve_link_target(base_dir: Path, link: str) -> Path:
    """Resolve a link destination to the file it names.

    The destination may carry a title, angle brackets, a fragment, ``./`` or
    percent-encoding (see ``indexing.link_path_variants``). The decoded
    spelling is tried first and the literal one second, so a file really named
    ``my%20note.md`` still resolves; when neither exists the decoded spelling
    is returned.
    """
    windows_target = windows_absolute_path(link.split("#", 1)[0])
    if windows_target is not None:
        return windows_target
    candidates = [
        Path(variant) if variant.startswith("/") else (base_dir / variant).resolve()
        for variant in link_path_variants(link)
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if candidates else (base_dir / link.split("#", 1)[0]).resolve()


def check_links(ctx: LintContext) -> None:
    for doc in sorted(ctx.documents.values(), key=lambda item: str(item.path)):
        try:
            rel = doc.path.resolve().relative_to(ctx.root)
        except ValueError:
            continue
        if rel.parts[0] not in {"wiki", "inventory"}:
            continue
        for link in extract_link_destinations(document_text(doc)):
            if not is_local_markdown_link(link):
                continue
            target = resolve_link_target(doc.path.parent, link)
            if not target.exists():
                ctx.issue("warning", f"Markdown link points to missing file: {link}.", doc.path, check="C4")


FOOTNOTE_DEF_RE = re.compile(r"^\[\^([^\]\s]+)\]:", re.M)
FOOTNOTE_REF_RE = re.compile(r"\[\^([^\]\s]+)\](?!:)")
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:\|[^\]]*)?\]\]")


INLINE_CODE_RE = re.compile(r"`+[^`\n]*`+")


def strip_code_blocks(text: str) -> str:
    """Blank out code so prose checks do not fire inside syntax examples.

    Both fenced blocks and inline spans are removed: a wiki-link or footnote
    marker shown as an example of the syntax is not a link or a citation.
    """
    out: list[str] = []
    fence: str | None = None
    for line in text.split("\n"):
        stripped = line.lstrip()
        if fence is None:
            if stripped.startswith("```") or stripped.startswith("~~~"):
                fence = stripped[:3]
                out.append("")
                continue
            out.append(line)
        else:
            if stripped.startswith(fence):
                fence = None
            out.append("")
    return INLINE_CODE_RE.sub("", "\n".join(out))


def compiled_article_bodies(ctx: LintContext):
    """Yield (doc, prose) for compiled articles, with code blocks removed."""
    for doc in sorted(ctx.documents.values(), key=lambda item: str(item.path)):
        try:
            rel = doc.path.resolve().relative_to(ctx.root)
        except ValueError:
            continue
        if not rel.parts or rel.parts[0] != "wiki" or doc.path.name == "_index.md":
            continue
        text = document_text(doc)
        parts = split_markdown_frontmatter(text)
        body = parts[1] if parts else text
        yield doc, strip_code_blocks(body)


def check_footnotes(ctx: LintContext) -> None:
    """Footnotes are the portable way to bind a claim to its evidence.

    A reference with no definition silently drops the evidence in every
    renderer; a definition with no reference is a citation orphaned by an edit.
    Both are structural, so neither needs an LLM or an editorial judgement.
    """
    for doc, prose in compiled_article_bodies(ctx):
        defined = {match.group(1) for match in FOOTNOTE_DEF_RE.finditer(prose)}
        # FOOTNOTE_REF_RE's negative lookahead already excludes definition lines.
        referenced = {match.group(1) for match in FOOTNOTE_REF_RE.finditer(prose)}
        for key in sorted(referenced - defined):
            ctx.issue(
                "warning",
                f"Footnote [^{key}] is referenced but never defined; the claim it "
                "supports has no evidence in the rendered article.",
                doc.path,
                check="C4",
            )
        for key in sorted(defined - referenced):
            ctx.issue(
                "suggestion",
                f"Footnote [^{key}] is defined but never referenced; it supports "
                "no claim in the article.",
                doc.path,
                check="C4",
            )


# Directories whose files a wiki-link may legitimately name without the file
# being a compiled article. ``wiki/`` articles are resolved separately.
NON_ARTICLE_LINK_DIRS = ("inventory", "raw", "output", "datasets")


INDEX_LINK_KEY = "_index"


def link_key(stem: str) -> str:
    """Lookup key of a wiki-link target: the slug, except ``_index`` stays itself."""
    return INDEX_LINK_KEY if stem == INDEX_LINK_KEY else slugify(stem)


def non_article_targets(root: Path) -> dict[str, list[Path]]:
    """Map link keys of Markdown files outside ``wiki/`` articles to their paths.

    ``_index`` maps to every index file (the master one included), so a
    ``[[_index]]`` link resolves to something that exists.
    """
    targets: dict[str, list[Path]] = {}
    candidates = [root / "_index.md"]
    for name in NON_ARTICLE_LINK_DIRS:
        candidates.extend(markdown_files(root / name))
    candidates.extend(
        path
        for path in markdown_files(root / "wiki")
        if path.name == "_index.md"
    )
    for path in candidates:
        if path.is_file():
            targets.setdefault(link_key(path.stem), []).append(path)
    return targets


def wikilink_file_target(doc_dir: Path, root: Path, target: str) -> Path | None:
    """Resolve a path-style wiki-link relative to the linking file, or None.

    Only a target that stays inside the wiki root and names an existing
    Markdown file counts; anything else falls back to the basename lookup.
    """
    if "/" not in target:
        return None
    relative = target[:-3] if target.lower().endswith(".md") else target
    candidate = Path(os.path.normpath(doc_dir / (relative + ".md")))
    try:
        candidate.resolve().relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def check_wikilinks(ctx: LintContext) -> None:
    """Resolve [[slug]] / [[slug|text]] targets against compiled articles.

    Markdown links are already validated. Wiki-links are permitted alongside
    them, so a wiki-link to a slug that does not exist looks exactly like a
    working cross-reference while pointing nowhere.

    A dir-qualified target (``[[../topics/x]]``) resolves by its path relative
    to the linking file and, failing that, by its last segment. A target that
    exists but is not a compiled article (a file in ``inventory/``, ``raw/``,
    ``output/`` or an ``_index.md``) is reported as ``info``, not as a defect.
    """
    slugs: dict[str, list[Path]] = {}
    for doc in ctx.documents.values():
        try:
            rel = doc.path.resolve().relative_to(ctx.root)
        except ValueError:
            continue
        if not rel.parts or rel.parts[0] != "wiki" or doc.path.name == "_index.md":
            continue
        names = {slugify(doc.path.stem)}
        for alias in as_string_list(doc.frontmatter.get("aliases")):
            names.add(slugify(alias))
        for name in names:
            if name:
                slugs.setdefault(name, []).append(doc.path)

    others: dict[str, list[Path]] | None = None
    article_paths = {path.resolve() for owners in slugs.values() for path in owners}
    for doc, prose in compiled_article_bodies(ctx):
        seen: set[str] = set()
        for match in WIKILINK_RE.finditer(prose):
            written = match.group(1).strip()
            target = written.replace("\\", "/")
            stem = target.rstrip("/").rsplit("/", 1)[-1]
            if stem.lower().endswith(".md"):
                stem = stem[:-3]
            basename = link_key(stem)
            if not basename or written in seen:
                continue
            seen.add(written)

            by_path = wikilink_file_target(doc.path.parent, ctx.root, target)
            if by_path is not None:
                if by_path.resolve() in article_paths:
                    continue
                owners: list[Path] = [by_path]
                article = False
            else:
                owners = slugs.get(basename, [])
                article = bool(owners)
            if not owners:
                if others is None:
                    others = non_article_targets(ctx.root)
                found = others.get(basename, [])
                nearest = doc.path.parent / "_index.md"
                if basename == INDEX_LINK_KEY and nearest.is_file():
                    found = [nearest]
                if found:
                    listed = ", ".join(sorted(ctx.rel(path) for path in found)[:3])
                    ctx.issue(
                        "info",
                        f"Wiki-link [[{written}]] points to an existing file that is "
                        f"not a compiled article: {listed}.",
                        doc.path,
                        check="C4",
                    )
                else:
                    ctx.issue(
                        "warning",
                        f"Wiki-link [[{written}]] resolves to no compiled article.",
                        doc.path,
                        check="C4",
                    )
            elif not article:
                ctx.issue(
                    "info",
                    f"Wiki-link [[{written}]] points to an existing file that is "
                    f"not a compiled article: {ctx.rel(owners[0])}.",
                    doc.path,
                    check="C4",
                )
            elif len(owners) > 1:
                listed = ", ".join(sorted(ctx.rel(owner) for owner in owners))
                ctx.issue(
                    "suggestion",
                    f"Wiki-link [[{written}]] is ambiguous: {listed}.",
                    doc.path,
                    check="C4",
                )


def slugify(value: str) -> str:
    value = value.lower().replace("_", "-")
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"[^a-z0-9-]", "", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value


def markdown_relative_link(base_dir: Path, target: Path) -> str:
    rel = Path(os.path.relpath(target, base_dir)).as_posix()
    return markdown_link_destination(rel)
