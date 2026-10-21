"""The ``ref`` namespace: cross-root references (KM-HUB-006, KM-HUB-008, KM-HUB-009).

``knowmade://<root-id>[/<path>][#fragment]`` names a place by the stable id of
the root that holds it (``wk-`` wiki, ``hb-`` hub) and a POSIX path inside that
root, never by a filesystem path or a slug. This module parses the form
(``ref parse``), finds where it lives on this machine (``ref resolve``) and
checks the references of Markdown files (``ref check``).

Resolution is read-only and answers *where*, never *may it be published*:

- the candidates of an id are the registry entries that carry it
  (``federation.load``) and, for a ``wk-`` id, the topics of every available
  registered hub whose registry row or ``config.md`` carries it (the overlay and
  nested ``.wiki`` rules of ``roots.registry_entry_path``), deduplicated by real
  path; each one's status is ``federation.status_of``, so only an ``available``
  root (it exists and declares the id) is a replica;
- the target is the file or directory ``<path>`` names inside each replica. One
  replica with it: ``resolved``. Several with identical bytes: ``equivalent``.
  Several that differ: ``divergent``. A replica without it is reported
  ``missing-target`` and dropped; a target that a symlink leads out of its root
  is ``outside-root`` and dropped the same way;
- a write wants exactly one available replica (``ambiguous`` otherwise, even when
  they are equivalent) unless ``--replica`` names one;
- a target is *private* when its real path has a ``*-PRIVATE`` component or lies
  in a private topic of a hub registry (``privacy_boundary``). ``--public`` then
  refuses it (exit 3, nothing printed), and ``ref check`` reports a reference
  from a file outside every private location as ``private-target``.

Nothing here copies, inlines or rewrites content, and an unresolved reference is
durable: it is never turned into another id or path. Exit codes (``DESIGN.md``
sections 4, 11 to 13; ``references/federation.md``): 0 resolved or equivalent,
1 a refusal (unresolved, divergent, ambiguous write, findings), 2 an unusable
input (invalid URI, corrupt registry, ``--replica`` that is not a replica), 3 a
private target under ``--public``. One line on stderr, never a traceback.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import federation as _federation
from . import fsio as _fsio
from . import identity as _identity
from . import roots as _roots
from .common import read_registry
from .lint_links import strip_code_blocks
from .links import read_markdown
from .markdown import frontmatter_bounds
from .privacy_boundary import (
    BoundaryChecker,
    BoundaryRegistryError,
    private_topic_dirs,
    split_real,
)

SCHEME = "knowmade://"
_ROOT_ID = re.compile(r"^(?:hb|wk)-[0-9a-f]{16}$")

RESOLVED = "resolved"
EQUIVALENT = "equivalent"
DIVERGENT = "divergent"
AMBIGUOUS = "ambiguous"
UNRESOLVED = "unresolved"
MISSING_TARGET = "missing-target"
OUTSIDE_ROOT = "outside-root"

# Where a replica's target stands, after its root is known to be available.
PRESENT = "present"
MISSING = "missing"
OUTSIDE = "outside"

INVALID_REF = "invalid-ref"
PRIVATE_TARGET = "private-target"
DIVERGENT_REF = "divergent-ref"
UNRESOLVED_REF = "unresolved-ref"
SEVERITIES = {
    INVALID_REF: "error",
    PRIVATE_TARGET: "error",
    DIVERGENT_REF: "error",
    UNRESOLVED_REF: "warning",
    MISSING_TARGET: "warning",
}
FAIL_ON_LEVELS = ("error", "warning")

# A reference inside Markdown text: the scheme at a word boundary up to the first
# character that ends a link destination, then trailing prose punctuation dropped.
_URI_RE = re.compile(r"(?<![A-Za-z0-9+.\-])knowmade://[^\s<>()\[\]\"'`\x00-\x1f\x7f-\x9f]+")
_TRAILING_PUNCTUATION = ".,;:!?*_~"
_TREE_SALT = b"knowmade-tree-v1\n"


class RefError(Exception):
    """A user-facing failure: one message, the exit code to use."""

    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(message)
        self.code = code


class InvalidRef(RefError):
    """A reference that does not parse; ``reason`` is why, without the URI."""

    def __init__(self, uri: str, reason: str) -> None:
        super().__init__(f"invalid {SCHEME} reference {uri}: {reason}", 2)
        self.reason = reason


@dataclass(frozen=True)
class Ref:
    """A parsed reference: ``path`` is the decoded POSIX path, ``""`` for the root itself."""

    uri: str
    id: str
    path: str
    fragment: str | None

    @property
    def kind(self) -> str:
        return "hub" if self.id.startswith("hb-") else "wiki"

    @property
    def segments(self) -> list[str]:
        return self.path.split("/") if self.path else []


# --- the URI ------------------------------------------------------------------


def _invalid(uri: str, why: str) -> InvalidRef:
    return InvalidRef(uri, why)


def parse_uri(uri: str) -> Ref:
    """Parse ``uri`` or raise :class:`RefError` (exit 2).

    The root id must be ``wk-`` or ``hb-`` plus 16 lowercase hex characters. The
    path is split on ``/`` and percent-decoded per segment; an empty, ``.`` or
    ``..`` segment, a backslash, a ``:`` or a control character in a decoded
    segment, a raw space, and a ``?`` are refused, so no accepted path can name
    anything outside its root. Everything after the first ``#`` is the fragment,
    carried unchanged.
    """
    if not uri.startswith(SCHEME):
        raise _invalid(uri, f"it does not start with {SCHEME}")
    body, hash_mark, fragment = uri[len(SCHEME) :].partition("#")
    if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in uri):
        raise _invalid(repr(uri), "it contains a control character")
    if any(char.isspace() for char in body):
        raise _invalid(uri, "it contains whitespace (percent-encode it)")
    if "?" in body:
        raise _invalid(uri, "a query is not part of the form (percent-encode a literal ?)")
    if "\\" in body:
        raise _invalid(uri, "a backslash is not a separator (use /)")
    root_id, slash, raw_path = body.partition("/")
    if not _ROOT_ID.match(root_id):
        raise _invalid(uri, "the root must be a stable wk-<16 hex> or hb-<16 hex> id, not a slug, alias or path")
    segments: list[str] = []
    if raw_path:
        for raw in raw_path.split("/"):
            if not raw:
                raise _invalid(uri, "the path has an empty segment")
            try:
                segment = urllib.parse.unquote(raw, errors="strict")
            except UnicodeDecodeError:
                raise _invalid(uri, "the path is not valid percent-encoded UTF-8") from None
            if segment in {".", ".."}:
                raise _invalid(uri, f"the path has a {segment} segment")
            if "/" in segment or "\\" in segment or ":" in segment:
                raise _invalid(uri, "a path segment holds a separator or a drive colon")
            if any(ord(char) < 32 or ord(char) == 127 for char in segment):
                raise _invalid(uri, "a path segment holds a control character")
            segments.append(segment)
    return Ref(uri, root_id, "/".join(segments), fragment if hash_mark else None)


# --- digests -------------------------------------------------------------------


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError as exc:
        raise RefError(f"cannot read {path}: {exc.strerror or exc}", 2) from exc
    return digest.hexdigest()


def target_digest(target: Path) -> str:
    """``sha256:<hex>`` of a file's bytes, or of a directory's regular files.

    A directory digest covers the sorted ``(relative POSIX path, file sha256)``
    pairs of its regular files (symbolic links and ``.git/`` are ignored), behind
    a fixed salt so no directory ever equals a file.
    """
    if not target.is_dir():
        return f"sha256:{_file_digest(target)}"
    entries: list[tuple[str, str]] = []
    for current, dirs, names in os.walk(target):
        dirs[:] = [name for name in dirs if name != ".git"]
        for name in names:
            path = Path(current) / name
            if path.is_symlink() or not path.is_file():
                continue
            entries.append((path.relative_to(target).as_posix(), _file_digest(path)))
    digest = hashlib.sha256(_TREE_SALT)
    for relative, file_hash in sorted(entries):
        digest.update(relative.encode("utf-8") + b"\0" + file_hash.encode("ascii") + b"\n")
    return f"sha256:{digest.hexdigest()}"


def _within(parent: str, child: str) -> bool:
    parent, child = os.path.normcase(parent), os.path.normcase(child)
    if child == parent:
        return True
    return child.startswith(parent.rstrip(os.sep) + os.sep)


# --- candidates and replicas -------------------------------------------------------


@dataclass
class Replica:
    """One candidate root of an id, with the target ``<path>`` names inside it."""

    root: Path
    root_status: str
    detail: str
    last_seen: str | None
    target: Path
    target_state: str = ""
    private: bool = False
    digest: str | None = None

    @property
    def available(self) -> bool:
        return self.root_status == _federation.AVAILABLE

    @property
    def status(self) -> str:
        """The root status, with ``available`` replaced by what the target did."""
        if not self.available:
            return self.root_status
        return {PRESENT: _federation.AVAILABLE, MISSING: MISSING_TARGET, OUTSIDE: OUTSIDE_ROOT}[
            self.target_state
        ]

    def to_json(self) -> dict[str, Any]:
        return {
            "root": _federation.display(self.root),
            "status": self.status,
            "target": _federation.display(self.target),
            "digest": self.digest,
            "private": self.private,
            "detail": self.detail,
        }


@dataclass
class Candidate:
    root: Path
    status: dict[str, Any]
    last_seen: str | None = None


@dataclass
class Resolution:
    """The outcome for one reference: a status, every replica, and the one selected."""

    ref: Ref
    status: str
    replicas: list[Replica] = field(default_factory=list)
    selected: Replica | None = None

    @property
    def private(self) -> bool:
        if self.selected is not None:
            return self.selected.private
        return any(replica.private for replica in self.known())

    def known(self) -> list[Replica]:
        """The replicas that name the id: available or, for last known paths, unavailable."""
        return [r for r in self.replicas if r.root_status in (_federation.AVAILABLE, _federation.UNAVAILABLE)]


class Resolver:
    """Resolve references against the registry on this machine, with per-run caches."""

    def __init__(self) -> None:
        self._registry: _federation.Registry | None = None
        self._hubs: list[Path] | None = None
        self._candidates: dict[str, list[Candidate]] = {}
        self._hub_private: dict[str, list[Path]] = {}
        self._digests: dict[str, str] = {}
        self._resolutions: dict[tuple[str, str], Resolution] = {}

    # -- the registry and its hubs

    def registry(self) -> _federation.Registry:
        if self._registry is None:
            try:
                self._registry = _federation.load()
            except _federation.RegistryError as exc:
                raise RefError(str(exc), exc.code) from exc
        return self._registry

    def available_hubs(self) -> list[Path]:
        if self._hubs is None:
            self._hubs = [
                _federation.expanded(entry["path"])
                for entry in self.registry().entries
                if entry["kind"] == "hub" and _federation.status_of(entry)["status"] == _federation.AVAILABLE
            ]
        return self._hubs

    def candidates(self, root_id: str) -> list[Candidate]:
        """The roots that may hold ``root_id``: registry order, then hub-topic discovery order."""
        if root_id in self._candidates:
            return self._candidates[root_id]
        found: list[Candidate] = []
        seen: set[str] = set()

        def add(entry: dict[str, Any], last_seen: str | None) -> None:
            root = Path(os.path.abspath(_federation.expanded(entry["path"])))
            key = os.path.normcase(os.path.realpath(root))
            if key not in seen:
                seen.add(key)
                found.append(Candidate(root, _federation.status_of(entry), last_seen))

        for entry in self.registry().entries:
            if entry["id"] == root_id:
                add(entry, entry.get("last_seen"))
        if root_id.startswith("wk-"):
            for hub in self.available_hubs():
                for root in self._hub_topics_of(hub, root_id):
                    add({"id": root_id, "kind": "wiki", "path": root.as_posix()}, None)
        self._candidates[root_id] = found
        return found

    @staticmethod
    def _hub_topics_of(hub: Path, wiki_id: str) -> list[Path]:
        """Topic roots of ``hub`` whose registry row or ``config.md`` carries ``wiki_id``."""
        try:
            wikis = read_registry(hub).get("wikis") or {}
            overlay = _roots.read_local_registry(hub)
            found: list[Path] = []
            for slug, entry in wikis.items():
                if not isinstance(entry, dict):
                    continue
                root = Path(os.path.abspath(_roots.registry_entry_path(hub, str(slug), entry, overlay)))
                if _roots.same_path(root, hub):
                    continue  # the hub's own row is the hub, not a topic
                if entry.get("id") == wiki_id or _identity.read_wiki_id(root) == wiki_id:
                    found.append(root)
            return found
        except SystemExit as exc:
            raise RefError(f"cannot scan hub {_federation.display(hub)}: {exc.code}", 2) from None

    # -- privacy

    def _private_dirs(self, start: Path) -> list[Path]:
        hub = _roots.enclosing_hub(start)
        if hub is None:
            return []
        key = os.path.normcase(os.path.realpath(hub))
        if key not in self._hub_private:
            try:
                self._hub_private[key] = private_topic_dirs(hub)
            except BoundaryRegistryError as exc:
                raise RefError(f"cannot judge privacy: {exc}", 2) from exc
        return self._hub_private[key]

    def is_private(self, path: Path) -> bool:
        """A ``*-PRIVATE`` component of the real path, or inside a private topic of a hub registry.

        The hubs consulted are the one enclosing ``path`` and every available
        registered hub, so a private topic is private wherever it is reached from.
        """
        dirs = list(self._private_dirs(path))
        for hub in self.available_hubs():
            dirs.extend(self._private_dirs(hub))
        return BoundaryChecker(path, dirs).is_private_location(split_real(path))

    # -- replicas and resolution

    def replicas(self, ref: Ref) -> list[Replica]:
        found: list[Replica] = []
        for candidate in self.candidates(ref.id):
            target = candidate.root.joinpath(*ref.segments)
            replica = Replica(
                candidate.root,
                candidate.status["status"],
                _federation.status_detail(candidate.status),
                candidate.last_seen,
                target,
            )
            if replica.available:
                if not _within(os.path.realpath(candidate.root), os.path.realpath(target)):
                    replica.target_state = OUTSIDE
                    replica.detail = "the target resolves outside the root"
                else:
                    replica.target_state = PRESENT if os.path.exists(target) else MISSING
            replica.private = self.is_private(target)
            found.append(replica)
        return found

    def _digest(self, replica: Replica) -> None:
        key = os.path.realpath(replica.target)
        if key not in self._digests:
            self._digests[key] = target_digest(replica.target)
        replica.digest = self._digests[key]

    def resolve(
        self,
        ref: Ref,
        *,
        for_write: bool = False,
        replica: Path | None = None,
        digests: bool = False,
    ) -> Resolution:
        """Resolve ``ref``; ``digests`` fills the digest of every replica that has the target."""
        cache_key = (ref.id, ref.path)
        plain = not for_write and replica is None and not digests
        if plain and cache_key in self._resolutions:
            cached = self._resolutions[cache_key]
            return Resolution(ref, cached.status, cached.replicas, cached.selected)
        replicas = self.replicas(ref)
        usable = [r for r in replicas if r.available]
        present = [r for r in usable if r.target_state == PRESENT]
        if digests or len(present) > 1:
            for item in present:
                self._digest(item)

        def done(status: str, selected: Replica | None = None) -> Resolution:
            return Resolution(ref, status, replicas, selected)

        if replica is not None:
            chosen = [r for r in usable if _roots.same_path(r.root, replica)]
            if not chosen:
                listing = "".join(f"\n  {_federation.display(r.root)}" for r in usable) or " (none available)"
                raise RefError(
                    f"{_federation.display(replica)} is not an available replica of {ref.id}; available:{listing}", 2
                )
            picked = chosen[0]
            if picked.target_state == PRESENT or (for_write and picked.target_state == MISSING):
                return done(RESOLVED, picked)
            return done(MISSING_TARGET)
        if not usable:
            result = done(UNRESOLVED)
        elif for_write:
            if len(usable) > 1:
                result = done(AMBIGUOUS)
            elif usable[0].target_state == OUTSIDE:
                result = done(MISSING_TARGET)
            else:
                result = done(RESOLVED, usable[0])
        elif not present:
            result = done(MISSING_TARGET)
        elif len(present) == 1:
            result = done(RESOLVED, present[0])
        elif len({item.digest for item in present}) == 1:
            result = done(EQUIVALENT, present[0])
        else:
            result = done(DIVERGENT)
        if plain:
            self._resolutions[cache_key] = result
        return result


# --- verbs ------------------------------------------------------------------------


def _json(payload: Any) -> None:
    print(json.dumps(payload, indent=2))


def cmd_parse(args: argparse.Namespace) -> int:
    ref = parse_uri(args.uri)
    if args.json:
        _json({"uri": ref.uri, "id": ref.id, "kind": ref.kind, "path": ref.path or None, "fragment": ref.fragment})
        return 0
    print(f"id: {ref.id}")
    print(f"kind: {ref.kind}")
    print(f"path: {ref.path or '-'}")
    print(f"fragment: {ref.fragment if ref.fragment is not None else '-'}")
    return 0


def _located(replica: Replica, ref: Ref) -> str:
    """The local path, with the fragment appended as written."""
    text = _federation.display(replica.target)
    return f"{text}#{ref.fragment}" if ref.fragment is not None else text


def _refusal(resolution: Resolution) -> None:
    """The stderr lines of a refused resolution."""
    ref, replicas = resolution.ref, resolution.replicas
    if resolution.status == UNRESOLVED:
        if not replicas:
            print(f"unresolved: no root on this machine is known for {ref.id}", file=sys.stderr)
            return
        print(f"unresolved: no available replica of {ref.id}; last known:", file=sys.stderr)
        for replica in replicas:
            extras = [replica.detail, f"last seen {replica.last_seen}" if replica.last_seen else ""]
            text = ", ".join(extra for extra in extras if extra)
            print(f"  {replica.root_status}  {_federation.display(replica.root)}" + (f" ({text})" if text else ""), file=sys.stderr)
    elif resolution.status == MISSING_TARGET:
        where = "/" + ref.path if ref.path else ""
        print(f"missing-target: {ref.id}{where} cannot be used in any available replica:", file=sys.stderr)
        for replica in resolution.replicas:
            if replica.available:
                note = f" ({replica.detail})" if replica.detail else ""
                print(f"  {replica.status}  {_federation.display(replica.root)}{note}", file=sys.stderr)
    elif resolution.status == DIVERGENT:
        present = [r for r in replicas if r.available and r.target_state == PRESENT]
        print(f"divergent: {len(present)} replicas of {ref.id} differ at {ref.path or 'the root'}:", file=sys.stderr)
        for replica in present:
            print(f"  {replica.digest}  {_federation.display(replica.target)}", file=sys.stderr)
        print("pass --all to list them, or --replica <path> to read one", file=sys.stderr)
    elif resolution.status == AMBIGUOUS:
        usable = [r for r in replicas if r.available]
        print(f"ambiguous: {len(usable)} replicas of {ref.id}; a write would make them diverge:", file=sys.stderr)
        for replica in usable:
            print(f"  {_federation.display(replica.root)}", file=sys.stderr)
        print("pass --replica <path> to write to exactly one", file=sys.stderr)


def cmd_resolve(args: argparse.Namespace) -> int:
    ref = parse_uri(args.uri)
    replica = _federation.expanded(args.replica) if args.replica else None
    resolver = Resolver()
    # A public output never materializes a private target, whatever else the
    # resolution would find: judged before resolving, over every replica any
    # output could name, so no listing (not even a --replica error) prints one.
    if args.public and any(r.private for r in resolver.replicas(ref)):
        print(f"private target: {ref.uri} resolves into a private location; refused by --public", file=sys.stderr)
        return 3
    resolution = resolver.resolve(
        ref, for_write=args.for_write, replica=replica, digests=args.all or args.json
    )

    selected = resolution.selected
    listing = args.all and resolution.status in (RESOLVED, EQUIVALENT, DIVERGENT)
    code = 0 if resolution.status in (RESOLVED, EQUIVALENT) or listing else 1
    if args.json:
        _json(
            {
                "uri": ref.uri,
                "id": ref.id,
                "kind": ref.kind,
                "path": ref.path or None,
                "fragment": ref.fragment,
                "status": resolution.status,
                "resolved": _federation.display(selected.target) if selected is not None else None,
                "private": resolution.private,
                "replicas": [item.to_json() for item in resolution.replicas],
            }
        )
        return code
    if listing:
        for item in resolution.replicas:
            print(f"{item.status:<14}  {item.digest or '-'}  {_federation.display(item.target)}")
    elif selected is not None:
        print(_located(selected, ref))
    else:
        _refusal(resolution)
    return code


# --- check ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    code: str
    uri: str
    detail: str

    @property
    def severity(self) -> str:
        return SEVERITIES[self.code]

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "line": self.line,
            "severity": self.severity,
            "code": self.code,
            "uri": self.uri,
            "detail": self.detail,
        }

    def line_text(self) -> str:
        text = f"{self.path}:{self.line}: {self.severity} {self.code} {self.uri}"
        return f"{text} {self.detail}" if self.detail else text


def references_of(text: str) -> list[tuple[int, str]]:
    """Every ``knowmade://`` reference of a Markdown text with its line number.

    Inline links, reference definitions, autolinks, bare URIs and frontmatter
    values are all found by the same scan; fenced and inline code is blanked
    first (as ``links check`` does), so a reference shown as an example is not
    one. A reference that is wrapped in prose punctuation loses that punctuation.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    bounds = frontmatter_bounds(text)
    if bounds is None:
        scanned = strip_code_blocks(text)
    else:
        scanned = text[: bounds[2]] + strip_code_blocks(text[bounds[2] :])
    found: list[tuple[int, str]] = []
    for number, line in enumerate(scanned.split("\n"), start=1):
        if SCHEME not in line:
            continue
        for match in _URI_RE.finditer(line):
            found.append((number, match.group(0).rstrip(_TRAILING_PUNCTUATION)))
    return found


def _check_one(
    resolver: Resolver, uri: str, path: str, line: int, file_is_private: bool
) -> list[Finding]:
    try:
        ref = parse_uri(uri)
    except InvalidRef as exc:
        return [Finding(path, line, INVALID_REF, uri, exc.reason)]
    resolution = resolver.resolve(ref)
    findings: list[Finding] = []
    if not file_is_private and any(r.private for r in resolution.known()):
        findings.append(
            Finding(path, line, PRIVATE_TARGET, uri, "resolves into a private location; a file outside one must not refer to it")
        )
    if resolution.status == UNRESOLVED:
        known = len(resolution.replicas)
        why = f"no available replica ({known} known root(s))" if known else "no known root on this machine"
        findings.append(Finding(path, line, UNRESOLVED_REF, uri, why))
    elif resolution.status == MISSING_TARGET:
        usable = sum(1 for r in resolution.replicas if r.available)
        findings.append(
            Finding(path, line, MISSING_TARGET, uri, f"{ref.path or 'the root'} is not usable in {usable} available replica(s)")
        )
    elif resolution.status == DIVERGENT:
        count = sum(1 for r in resolution.replicas if r.available and r.target_state == PRESENT)
        findings.append(Finding(path, line, DIVERGENT_REF, uri, f"{count} replicas differ"))
    return findings


def _markdown_files(paths: list[Path]) -> list[Path]:
    chosen: set[Path] = set()
    for path in paths:
        if path.is_dir():
            for current, dirs, names in os.walk(path):
                dirs[:] = sorted(name for name in dirs if name != ".git")
                chosen.update(Path(current) / name for name in names if name.lower().endswith(".md"))
        elif path.is_file():
            chosen.add(path)
        else:
            raise RefError(f"not a file or directory: {path}", 2)
    return sorted(chosen)


def _display_path(path: Path, root: Path | None) -> str:
    if root is not None:
        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            pass
    return path.as_posix()


def check_files(resolver: Resolver, files: list[Path], root: Path | None) -> tuple[list[Finding], int]:
    """The findings of ``files`` and how many references they hold."""
    findings: list[Finding] = []
    references = 0
    for file in files:
        text = read_markdown(file)
        found = references_of(text)
        if not found:
            continue
        shown = _display_path(file, root)
        private = resolver.is_private(file)
        for line, uri in found:
            references += 1
            findings.extend(_check_one(resolver, uri, shown, line, private))
    findings.sort(key=lambda item: (item.path, item.line, item.code, item.uri))
    return findings, references


def cmd_check(args: argparse.Namespace) -> int:
    root: Path | None = _roots.expand_leading_tilde(args.root) if args.root else None
    if root is not None and not root.is_dir():
        raise RefError(f"ref root is not a directory: {args.root}", 2)
    targets = [_roots.expand_leading_tilde(item) for item in args.paths]
    if not targets:
        if root is None:
            try:
                root = _roots.resolve_wiki_root_args(argparse.Namespace())
            except SystemExit as exc:
                raise RefError(str(exc.code), 2) from None
            if not root.is_dir():
                raise RefError(f"ref root is not a directory: {root}", 2)
        targets = [root]
    files = _markdown_files(targets)
    resolver = Resolver()
    findings, references = check_files(resolver, files, root)
    counts = {
        "error": sum(1 for item in findings if item.severity == "error"),
        "warning": sum(1 for item in findings if item.severity == "warning"),
    }
    failing = {"error"} if args.fail_on == "error" else {"error", "warning"}
    code = 1 if any(item.severity in failing for item in findings) else 0
    if args.json:
        _json(
            {
                "root": _federation.display(root) if root is not None else None,
                "files": len(files),
                "references": references,
                "findings": [item.to_json() for item in findings],
                "counts": counts,
            }
        )
        return code
    for item in findings:
        print(item.line_text())
    print(
        f"ref check: {counts['error']} error, {counts['warning']} warning "
        f"({len(files)} files, {references} references)"
    )
    return code


# --- parser ---------------------------------------------------------------------------

REF_HELP = "Parse, resolve and check knowmade://<root-id>/<path> cross-root references."


def register_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Register ``ref`` on a parent ``llm-wiki`` parser; dispatch through :func:`run`."""
    ref = subparsers.add_parser(
        "ref",
        help=REF_HELP,
        description=(
            f"{REF_HELP} A reference names a root by its stable id (wk-... wiki, hb-... hub) "
            "and a path inside it, never by a slug or a filesystem path. Resolution reads "
            "the federation registry and the registered hubs, changes nothing, and answers "
            "where, not whether it may be published. Exit 0 resolved or equivalent, 1 a "
            "refusal (unresolved, divergent, ambiguous write, findings), 2 unusable input "
            "(invalid URI, corrupt registry, --replica that is not a replica), 3 a private "
            "target under --public."
        ),
    )
    ref.set_defaults(func=run)
    sub = ref.add_subparsers(dest="ref_command", required=True)

    parse = sub.add_parser(
        "parse",
        help="Validate a reference and print its parts.",
        description=(
            "knowmade://<root-id>[/<path>][#fragment]. The root id is wk-<16 hex> or "
            "hb-<16 hex>; the path is POSIX, relative to that root, percent-decoded per "
            "segment; '..' and '.' segments, absolute paths, backslashes and empty segments "
            "are refused. The fragment is carried unchanged. An invalid reference exits 2."
        ),
    )
    parse.add_argument("uri", help="The knowmade:// reference.")
    parse.add_argument("--json", action="store_true", help="Print JSON.")

    resolve = sub.add_parser(
        "resolve",
        help="Print the local path of a reference.",
        description=(
            "Read-only. Candidates are the registry entries of the id and, for a wk- id, the "
            "topics of registered hubs that carry it; only available roots are replicas. One "
            "replica with the target: resolved. Several with identical bytes: equivalent "
            "(the first, in registry then hub order, is printed). Several that differ: "
            "divergent, exit 1 (--all lists them). None available: unresolved, exit 1 with "
            "the last known paths. --for-write needs exactly one available replica."
        ),
    )
    resolve.add_argument("uri", help="The knowmade:// reference.")
    mode = resolve.add_mutually_exclusive_group()
    mode.add_argument(
        "--for-write",
        action="store_true",
        help="Resolve for a write: fail with ambiguous unless exactly one replica is available.",
    )
    mode.add_argument(
        "--all", action="store_true", help="List every replica with its digest instead of failing on divergence."
    )
    resolve.add_argument(
        "--replica",
        help="Use this available replica root explicitly (the way past a divergent or ambiguous refusal).",
    )
    resolve.add_argument(
        "--public",
        action="store_true",
        help="Refuse (exit 3, nothing printed) when any replica of the target is private.",
    )
    resolve.add_argument("--json", action="store_true", help="Print JSON.")

    check = sub.add_parser(
        "check",
        help="Check the knowmade:// references of Markdown files.",
        description=(
            "Scans Markdown (directories recurse, .git and code are skipped) for knowmade:// "
            "references in links, definitions, autolinks, bare text and frontmatter. Findings: "
            "invalid-ref, private-target (a file outside every private location refers into "
            "one) and divergent-ref are errors; unresolved-ref (the root may be offline) and "
            "missing-target are warnings. A file inside a private location may refer to anything. "
            "Exit 1 when a finding at or above --fail-on exists."
        ),
    )
    check.add_argument("paths", nargs="*", help="Files or directories to scan (default: the wiki root).")
    check.add_argument("--root", help="Root directory to scan by default and to show paths relative to.")
    check.add_argument("--json", action="store_true", help="Print JSON.")
    check.add_argument(
        "--fail-on",
        choices=FAIL_ON_LEVELS,
        default="error",
        help="Lowest severity that exits 1 (default: error).",
    )
    return ref


_VERBS = {"parse": cmd_parse, "resolve": cmd_resolve, "check": cmd_check}


def run(args: argparse.Namespace) -> int:
    """`llm-wiki ref parse|resolve|check`."""
    try:
        return _VERBS[args.ref_command](args)
    except RefError as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
    except SystemExit as exc:
        # JSONReadError is a SystemExit: an unreadable JSON file reached through
        # the roots layer is an unusable input, exit 2.
        if isinstance(exc.code, str):
            print(exc.code, file=sys.stderr)
            return 2 if isinstance(exc, _fsio.JSONReadError) else 1
        raise
    except OSError as exc:
        print(f"ref: {exc}", file=sys.stderr)
        return 2
