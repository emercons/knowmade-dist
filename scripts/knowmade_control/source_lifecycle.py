"""Durable project-local lifecycle for source originals and derivatives."""

from __future__ import annotations

import argparse
import codecs
import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any

try:
    from . import frontmatter as _fm
    from .fsio import JSONReadError, read_json_object, write_json_atomic
    from .stdio import configure_utf8_stdio
except ImportError:  # no parent package: loaded by file path, or run as a bare module
    # Consumers load this file with importlib.util.spec_from_file_location. A
    # bare ``import frontmatter`` only works when the package directory is on
    # sys.path, and putting it there would shadow the consumer's own
    # same-named modules. Load the sibling package under a private name
    # instead, so the siblings' own relative imports resolve and sys.path stays
    # untouched.
    import importlib
    import importlib.util

    _PACKAGE_DIR = Path(__file__).resolve().parent
    _PACKAGE = "_knowmade_control_siblings"
    if _PACKAGE not in sys.modules:
        _spec = importlib.util.spec_from_file_location(_PACKAGE, _PACKAGE_DIR / "__init__.py", submodule_search_locations=[str(_PACKAGE_DIR)])
        if _spec is None or _spec.loader is None:
            raise ImportError(f"cannot load the knowmade_control package from {_PACKAGE_DIR}")
        _module = importlib.util.module_from_spec(_spec)
        sys.modules[_PACKAGE] = _module
        try:
            _spec.loader.exec_module(_module)
        except BaseException:
            sys.modules.pop(_PACKAGE, None)
            raise
    _fm = importlib.import_module(f"{_PACKAGE}.frontmatter")
    _fsio = importlib.import_module(f"{_PACKAGE}.fsio")
    JSONReadError = _fsio.JSONReadError
    read_json_object = _fsio.read_json_object
    write_json_atomic = _fsio.write_json_atomic
    configure_utf8_stdio = importlib.import_module(f"{_PACKAGE}.stdio").configure_utf8_stdio

SCHEMA = "llm-wiki/source-pointer/v1"
RECORD_SCHEMA = "llm-wiki/source-record/v1"
CI_MARKERS = ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "TF_BUILD", "BUILDKITE")
DEFAULTS: dict[str, Any] = {
    "backend": "filesystem",
    "persistent": False,
    "local_originals": "../.wiki-local/originals",
    "tracked_originals": "../sources/raw",
    "derived": "../sources/text",
    "pointers": "../sources/pointers",
    "quarantine": "../.wiki-local/quarantine",
    "default_decision": "review-required",
    "git_max_file_mib": 95,
    "receive_max_file_mib": 1024,
}
BOOL_KEYS = ("persistent",)
INT_KEYS = ("git_max_file_mib", "receive_max_file_mib")
DECISIONS = ("review-required", "local", "git")
# The store locations. Their defaults are relative to the wiki root, and `../`
# is the project root for a project-local `.wiki`. A hub topic resolves them
# inside the topic instead (see ``load_config``).
PATH_KEYS = ("local_originals", "tracked_originals", "derived", "pointers", "quarantine")
PRIVATE_SUFFIX = "-PRIVATE"
# Actions that write originals, derived text or pointers into the store.
WRITING_ACTIONS = ("receive", "extract", "decide", "attach-derived", "import-pointer")
REQUIRED_POINTER = {"schema", "source_id", "sha256", "bytes", "content_format", "retention", "original", "derived_artifacts"}
# Optional v1 pointer keys (knowmade#118). Every v1 reader checks only
# REQUIRED_POINTER and ignores other keys, so they stay within v1; a pointer
# carries one only when it has a value, so older pointers stay byte-identical.
OPTIONAL_POINTER = ("retrieved_at", "note", "aliases", "imported_from")
POINTER_SCHEMA_FAMILY = "llm-wiki/source-pointer/"
DERIVED_STATUS = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")


class LifecycleError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


_TRUE_WORDS = {"true", "yes", "on"}
_FALSE_WORDS = {"false", "no", "off"}


def _strip_comment(value: str) -> str:
    """``value`` without a trailing ``# comment`` (a ``#`` inside quotes is text)."""
    value = value.strip()
    if value[:1] in {'"', "'"}:
        close = value.find(value[0], 1)
        if close > 0 and value[close + 1 :].lstrip()[:1] in {"", "#"}:
            return value[: close + 1]
    return re.split(r"(?:^|\s)#", value, maxsplit=1)[0].strip()


def _scalar(value: str) -> Any:
    value = _strip_comment(value)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    if value.lower() in _TRUE_WORDS | _FALSE_WORDS:
        return value.lower() in _TRUE_WORDS
    if re.fullmatch(r"\d+", value):
        return int(value)
    return value


@dataclass(frozen=True)
class SourceConfig:
    wiki_root: Path
    values: dict[str, Any]
    unknown_keys: tuple[str, ...] = ()
    # The source_store keys config.md sets itself; every other key is a default.
    explicit: frozenset[str] = frozenset()

    def path(self, key: str) -> Path:
        candidate = Path(str(self.values[key]))
        return (self.wiki_root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()

    @property
    def records(self) -> Path:
        return self.path("local_originals") / ".records"


def hub_of_topic(wiki_root: Path) -> Path | None:
    """The hub that owns ``wiki_root`` as ``topics/<slug>`` or ``topics/.archive/<slug>``, else None."""
    parent = wiki_root.parent
    if parent.name == ".archive" and parent.parent.name == "topics":
        parent = parent.parent
    if parent.name != "topics":
        return None
    hub = parent.parent
    return hub if (hub / "wikis.json").is_file() else None


def _inside_root(value: str) -> str:
    """A default store path moved inside the wiki root: ``../x/y`` becomes ``x/y``."""
    while value.startswith("../"):
        value = value[3:]
    return value


def load_config(wiki_root: Path) -> SourceConfig:
    """Read ``source_store`` from ``config.md`` over the defaults.

    The defaults suit a project-local ``.wiki``, where ``../`` is the project
    root. For a hub topic, ``../`` would be ``topics/``, a directory every topic
    shares and no ``-PRIVATE`` ignore rule covers, so there the defaults
    resolve inside the topic (``<topic>/.wiki-local/...``, ``<topic>/sources/...``)
    (knowmade#89). Keys that ``config.md`` sets are used as written.
    """
    wiki_root = wiki_root.resolve()
    config_path = wiki_root / "config.md"
    if not config_path.is_file():
        raise LifecycleError(f"wiki config not found: {config_path}")
    values = dict(DEFAULTS)
    explicit: set[str] = set()
    section_indent: int | None = None
    unknown: list[str] = []
    for line in _fm.read_block(config_path, default="").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if re.fullmatch(r"source_store\s*:\s*", line.strip()):
            section_indent = indent
            continue
        if section_indent is None:
            continue
        if indent <= section_indent:
            section_indent = None
            continue
        match = re.match(r"\s+([A-Za-z0-9_-]+)\s*:\s*(.*?)\s*$", line)
        if not match:
            continue
        if match.group(1) in DEFAULTS:
            values[match.group(1)] = _scalar(match.group(2))
            explicit.add(match.group(1))
        elif match.group(1) not in unknown:
            unknown.append(match.group(1))
    if values["backend"] != "filesystem":
        raise LifecycleError(f"unsupported source_store backend {values['backend']!r}; Phase 1 supports filesystem")
    for key in BOOL_KEYS:
        if not isinstance(values[key], bool):
            raise LifecycleError(f"source_store.{key} must be a boolean (true/false, yes/no or on/off), got {values[key]!r}")
    for key in INT_KEYS:
        if isinstance(values[key], bool) or not isinstance(values[key], int) or values[key] <= 0:
            raise LifecycleError(f"source_store.{key} must be a positive integer")
    if values["default_decision"] not in DECISIONS:
        raise LifecycleError(f"source_store.default_decision must be one of {', '.join(DECISIONS)}, got {values['default_decision']!r}")
    if hub_of_topic(wiki_root) is not None:
        for key in PATH_KEYS:
            if key not in explicit:
                values[key] = _inside_root(str(values[key]))
    return SourceConfig(wiki_root, values, tuple(unknown), frozenset(explicit))


def config_warnings(config: SourceConfig) -> list[str]:
    """Advisory findings about ``config``, shared by the ``source`` command and lint."""
    if not config.unknown_keys:
        return []
    return [f"unknown source_store key(s) ignored: {', '.join(config.unknown_keys)} (known keys: {', '.join(DEFAULTS)})"]


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def private_store_escapes(config: SourceConfig) -> list[str]:
    """Default store keys of a ``-PRIVATE`` wiki that resolve outside its directory.

    Only the wiki directory itself is covered by the hub's ``-PRIVATE`` ignore
    rule, so a default that lands beside it would put private originals and
    text in a committable place. An explicit ``source_store`` key is the
    owner's decision and is never reported.
    """
    if not config.wiki_root.name.endswith(PRIVATE_SUFFIX):
        return []
    return [key for key in PATH_KEYS if key not in config.explicit and not _is_under(config.path(key), config.wiki_root)]


def require_private_store_inside(config: SourceConfig) -> None:
    escapes = private_store_escapes(config)
    if not escapes:
        return
    where = ", ".join(f"{key} -> {config.path(key)}" for key in escapes)
    raise LifecycleError(
        f"refusing to write sources of private wiki {config.wiki_root.name}: the default source_store "
        f"location(s) {where} fall outside {config.wiki_root}, where the -PRIVATE ignore rule does not "
        "reach. Set those keys explicitly under source_store in config.md (a path inside the wiki, or a "
        "location you have made private yourself), then retry."
    )


def store_root_entries(config: SourceConfig) -> set[str]:
    """Top-level names under the wiki root that the source store owns (for the unknown-file check)."""
    names: set[str] = set()
    for key in PATH_KEYS:
        try:
            relative = config.path(key).relative_to(config.wiki_root)
        except ValueError:
            continue
        if relative.parts:
            names.add(relative.parts[0])
    return names


def is_ephemeral_ci() -> bool:
    return any(os.environ.get(name, "").strip().lower() not in {"", "0", "false", "no"} for name in CI_MARKERS)


def _safe_name(value: str) -> str:
    name = Path(urllib.parse.urlparse(value).path).name or "source.bin"
    return (re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-") or "source.bin")[:160]


def _portable(path: Path, base: Path) -> str:
    try:
        return Path(os.path.relpath(path.resolve(), base.resolve())).as_posix()
    except ValueError:
        return str(path.resolve())


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, payload)


def _record_path(config: SourceConfig, source_id: str) -> Path:
    return config.records / f"{source_id}.json"


def _load_record(config: SourceConfig, source_id: str) -> dict[str, Any]:
    path = _record_path(config, source_id)
    if not path.exists():
        raise LifecycleError(f"unknown source id: {source_id}")
    try:
        return read_json_object(path, "local source record")
    except JSONReadError as exc:
        raise LifecycleError(str(exc)) from exc


def _existing_record(config: SourceConfig, source_id: str) -> dict[str, Any] | None:
    """The stored lifecycle record for ``source_id``, or None when it is new."""
    if not _record_path(config, source_id).exists():
        return None
    return _load_record(config, source_id)


def _copy_stream(source: str, destination: Path, limit: int) -> tuple[int, str | None]:
    parsed = urllib.parse.urlparse(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + ".part")
    content_type = None
    total = 0
    try:
        if parsed.scheme in {"http", "https"}:
            stream = urllib.request.urlopen(urllib.request.Request(source, headers={"User-Agent": "llm-wiki-source/1"}), timeout=30)  # noqa: S310
            content_type = stream.headers.get_content_type()
        else:
            stream = Path(source).expanduser().resolve().open("rb")
        with stream, temp.open("wb") as output:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                total += len(block)
                if total > limit:
                    raise LifecycleError(f"source exceeds configured receive limit of {limit} bytes")
                output.write(block)
        temp.replace(destination)
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    return total, content_type


def _provenance(source: str, canonical_url: str | None, retrieved_at: str | None) -> tuple[str | None, str | None]:
    """The validated ``(canonical_url, retrieved_at)`` to record for a received source."""
    if canonical_url is not None and urllib.parse.urlparse(canonical_url).scheme not in {"http", "https"}:
        raise LifecycleError(f"--canonical-url must be an http(s) URL: {canonical_url!r}")
    if retrieved_at is not None:
        try:
            datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise LifecycleError(f"--retrieved-at must be an ISO 8601 timestamp: {retrieved_at!r}") from exc
    url = canonical_url or (source if urllib.parse.urlparse(source).scheme in {"http", "https"} else None)
    return url, retrieved_at


def _alias(value: str) -> str:
    alias = value.strip().replace("\\", "/")
    if not alias:
        raise LifecycleError("--alias must not be empty")
    return alias


def _fill_known(config: SourceConfig, known: dict[str, Any], provided: dict[str, Any], aliases: list[str]) -> bool:
    """Add provenance a known record lacks and new aliases; never overwrite a recorded value.

    Returns whether the record changed. A value that differs from the recorded
    one is refused: the record is the history of the bytes, not a scratchpad.
    """
    changed = False
    for key, value in provided.items():
        if value is None or known.get(key) == value:
            continue
        if known.get(key) is not None:
            raise LifecycleError(f"known source {known['source_id']} already records {key} {known[key]!r}; refusing to replace it with {value!r}")
        known[key] = value
        changed = True
    merged = sorted(set(known.get("aliases") or []) | set(aliases))
    if merged != sorted(known.get("aliases") or []):
        known["aliases"] = merged
        changed = True
    if changed:
        _atomic_json(_record_path(config, known["source_id"]), known)
        _refresh_pointer(config, known)
    return changed


def receive(
    config: SourceConfig,
    source: str,
    quarantine_reason: str | None = None,
    canonical_url: str | None = None,
    retrieved_at: str | None = None,
    note: str | None = None,
    aliases: list[str] | None = None,
) -> dict[str, Any]:
    canonical_url, retrieved_at = _provenance(source, canonical_url, retrieved_at)
    aliases = sorted({_alias(value) for value in aliases or []})
    if is_ephemeral_ci() and not bool(config.values["persistent"]):
        raise LifecycleError("source receive refused on an ephemeral CI runner: configure a persistent source_store backend and set source_store.persistent: true")
    provisional = config.path("quarantine" if quarantine_reason else "local_originals") / _safe_name(source)
    size, content_type = _copy_stream(source, provisional, int(config.values["receive_max_file_mib"]) * 1024 * 1024)
    if size == 0 and not quarantine_reason:
        quarantine_reason = "empty-payload"
    digest = sha256_file(provisional)
    known = _existing_record(config, digest)
    if known is not None:
        # Idempotent receive: the lifecycle record (status, derived artifacts,
        # retention decision, pointer/tracked paths) is never reset. Only a
        # lost original is restored, at the path the record already names.
        destination = Path(str(known.get("original_path") or ""))
        if not str(known.get("original_path") or ""):
            provisional.unlink(missing_ok=True)
            raise LifecycleError(f"existing source record has no original_path: {_record_path(config, digest)}")
        restored = False
        if destination.exists():
            if sha256_file(destination) != digest:
                provisional.unlink(missing_ok=True)
                raise LifecycleError(f"known source's original differs from received bytes: {destination}")
            provisional.unlink(missing_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            provisional.replace(destination)
            restored = True
        updated = _fill_known(config, known, {"canonical_url": canonical_url, "retrieved_at": retrieved_at, "note": note}, aliases)
        return {**known, "already_known": True, "original_restored": restored, "record_updated": updated}
    destination = config.path("quarantine" if quarantine_reason else "local_originals") / digest[:2] / f"{digest}-{_safe_name(source)}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if sha256_file(destination) != digest:
            raise LifecycleError(f"duplicate destination differs from received bytes: {destination}")
        provisional.unlink(missing_ok=True)
    else:
        provisional.replace(destination)
    record = {
        "schema": RECORD_SCHEMA,
        "source_id": digest,
        "received_at": utc_now(),
        "status": "quarantined" if quarantine_reason else "received",
        "original_path": str(destination),
        "sha256": digest,
        "bytes": size,
        "content_format": (content_type or mimetypes.guess_type(destination.name)[0] or "application/octet-stream").split(";", 1)[0],
        "canonical_url": canonical_url,
        "retrieved_at": retrieved_at,
        "input": source,
        "quarantine_reason": quarantine_reason,
        "derived_artifacts": [],
        "retention": None,
    }
    if note is not None:
        record["note"] = note
    if aliases:
        record["aliases"] = aliases
    _atomic_json(_record_path(config, digest), record)
    return {**record, "already_known": False}


SNIFF_BYTES = 4096
_META_CHARSET = re.compile(rb"<meta\b[^>]*?\bcharset\s*=\s*[\"']?\s*([A-Za-z0-9_.:\-]+)", re.IGNORECASE)


def _text_codec(label: str) -> str | None:
    """Canonical codec name for a declared charset label, or None when unusable."""
    try:
        name = codecs.lookup(label).name
        b"".decode(name)
        "".encode(name)  # rejects binary transforms such as hex and rot13
    except (LookupError, ValueError):
        return None
    # A page whose ASCII-readable meta tag declares UTF-16/32 is not that
    # encoding; the HTML standard treats the declaration as UTF-8.
    return "utf-8" if name.startswith(("utf-16", "utf-32")) else name


def decode_text(data: bytes, *, sniff_meta: bool = False) -> tuple[str, str]:
    """Decode a text original and name the codec used.

    A BOM decides first (UTF-8, UTF-16). For HTML, the first ``SNIFF_BYTES``
    bytes are searched for ``<meta charset=...>`` and the ``http-equiv``
    content-type form; an unknown label is skipped. UTF-8 is the fallback.
    Invalid bytes become U+FFFD and line endings are normalized to LF.
    """
    if data.startswith(codecs.BOM_UTF8):
        name, text = "utf-8", data[len(codecs.BOM_UTF8):].decode("utf-8", errors="replace")
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        name, text = "utf-16", data.decode("utf-16", errors="replace")
    else:
        name = "utf-8"
        if sniff_meta:
            for match in _META_CHARSET.finditer(data[:SNIFF_BYTES]):
                declared = _text_codec(match.group(1).decode("ascii"))
                if declared:
                    name = declared
                    break
        text = data.decode(name, errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n"), name


def _extract_pdf(source: Path) -> tuple[str, str]:
    executable = shutil.which("pdftotext")
    if executable:
        result = subprocess.run([executable, "-layout", str(source), "-"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, check=False)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout, "text-extracted"
    try:
        from pypdf import PdfReader  # type: ignore

        text = "\n\n\f\n\n".join(page.extract_text() or "" for page in PdfReader(str(source)).pages)
        if text.strip():
            return text, "text-extracted"
    except Exception:  # malformed/image-only PDFs vary across optional extractors
        pass
    return "[No embedded text was extracted. OCR is required and remains a topic-specific workflow.]\n", "ocr-needed"


def extract(config: SourceConfig, source_id: str) -> dict[str, Any]:
    record = _load_record(config, source_id)
    if record.get("status") == "quarantined":
        raise LifecycleError("quarantined bytes cannot be extracted until separately reviewed")
    source = Path(record["original_path"])
    if not source.is_file() or sha256_file(source) != record["sha256"]:
        raise LifecycleError("local original is unavailable or its hash no longer matches")
    fmt = str(record["content_format"])
    status = "text-extracted"
    encoding: str | None = None
    if fmt == "application/pdf" or source.suffix.lower() == ".pdf":
        text, status = _extract_pdf(source)
    elif fmt.startswith("text/") or source.suffix.lower() in {".md", ".txt", ".csv", ".json", ".xml", ".html", ".htm"}:
        html = fmt in {"text/html", "application/xhtml+xml"} or source.suffix.lower() in {".html", ".htm"}
        text, encoding = decode_text(source.read_bytes(), sniff_meta=html)
        if source.suffix.lower() in {".html", ".htm"}:
            text = unescape(re.sub(r"<[^>]+>", " ", text))
    else:
        text, status = "[Binary original retained; no generic text extractor is available for this format.]\n", "metadata-only"
    destination = config.path("derived") / f"{source_id}.txt"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.read_text(encoding="utf-8", errors="replace") != text:
        raise LifecycleError(f"derived target already exists with different contents: {destination}")
    if not destination.exists():
        destination.write_bytes(text.encode("utf-8"))
    artifact = {"path": _portable(destination, config.wiki_root), "sha256": sha256_file(destination), "bytes": destination.stat().st_size, "format": "text/plain", "extraction_status": status}
    if encoding:
        artifact["source_encoding"] = encoding
    # Artifacts attached with attach-derived (OCR and the like) are kept.
    others = [item for item in record.get("derived_artifacts", []) if not isinstance(item, dict) or item.get("path") != artifact["path"]]
    record.update({"derived_artifacts": [artifact, *others], "status": "extracted", "extracted_at": utc_now()})
    _atomic_json(_record_path(config, source_id), record)
    return record


def attach_derived(config: SourceConfig, source_id: str, file: Path, status: str, tool: str) -> dict[str, Any]:
    """Store an externally produced derivative (OCR text, page-stable extraction) of a received source."""
    if not DERIVED_STATUS.fullmatch(status):
        raise LifecycleError(f"--status must be a lowercase slug such as ocr-text, got {status!r}")
    if not tool.strip():
        raise LifecycleError("--tool must name the program that produced the file")
    record = _load_record(config, source_id)
    if record.get("status") == "quarantined":
        raise LifecycleError("quarantined bytes require separate review; derived text refused")
    file = file.expanduser().resolve()
    if not file.is_file():
        raise LifecycleError(f"derived file not found: {file}")
    destination = config.path("derived") / f"{source_id}.{status}{file.suffix.lower() or '.txt'}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = sha256_file(file)
    if destination.exists():
        if sha256_file(destination) != digest:
            raise LifecycleError(f"derived target already exists with different contents: {destination}")
    else:
        temp = destination.with_name(destination.name + ".part")
        try:
            shutil.copyfile(file, temp)
            temp.replace(destination)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
        if sha256_file(destination) != digest:
            raise LifecycleError(f"copied derived file hash mismatch: {destination}")
    artifact = {
        "path": _portable(destination, config.wiki_root),
        "sha256": digest,
        "bytes": destination.stat().st_size,
        "format": mimetypes.guess_type(destination.name)[0] or "text/plain",
        "extraction_status": status,
        "tool": tool.strip(),
    }
    others = [item for item in record.get("derived_artifacts", []) if not isinstance(item, dict) or item.get("path") != artifact["path"]]
    record["derived_artifacts"] = [*others, artifact]
    if record.get("status") == "received":
        record["status"] = "extracted"
    _atomic_json(_record_path(config, source_id), record)
    _refresh_pointer(config, record)
    return {**record, "attached": artifact}


def _pointer_payload(config: SourceConfig, record: dict[str, Any], retention: str, tracked: Path | None) -> dict[str, Any]:
    original = {"local_path": _portable(Path(record["original_path"]), config.wiki_root)}
    if tracked is not None:
        original["tracked_path"] = _portable(tracked, config.wiki_root)
    payload = {
        "schema": SCHEMA,
        "source_id": record["source_id"],
        "recorded_at": utc_now(),
        "sha256": record["sha256"],
        "bytes": record["bytes"],
        "content_format": record["content_format"],
        "retention": retention,
        "canonical_url": record.get("canonical_url"),
        "original": original,
        "derived_artifacts": record.get("derived_artifacts", []),
        "quarantine": None,
    }
    for key in OPTIONAL_POINTER:
        if record.get(key):
            payload[key] = record[key]
    return payload


def _core(pointer: dict[str, Any]) -> dict[str, Any]:
    """A pointer without its write time and optional provenance keys."""
    return {k: v for k, v in pointer.items() if k != "recorded_at" and k not in OPTIONAL_POINTER}


def _read_pointer(pointer: Path) -> dict[str, Any]:
    try:
        existing = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LifecycleError(f"invalid pointer JSON {pointer}: {exc}") from exc
    if not isinstance(existing, dict):
        raise LifecycleError(f"invalid pointer JSON {pointer}: expected an object")
    return existing


def _keep_existing(payload: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    """``payload`` keeping what only the written pointer knows: its time, import origin and aliases."""
    merged = {**payload, "recorded_at": existing.get("recorded_at")}
    if existing.get("imported_from"):
        merged["imported_from"] = existing["imported_from"]
    aliases = sorted(set(payload.get("aliases") or []) | set(existing.get("aliases") or []))
    if aliases:
        merged["aliases"] = aliases
    return merged


def _refresh_pointer(config: SourceConfig, record: dict[str, Any]) -> None:
    """Rewrite a decided source's pointer from its record, keeping the pointer's ``recorded_at``."""
    if record.get("retention") not in {"local", "git"} or not record.get("pointer_path"):
        return
    pointer = Path(str(record["pointer_path"]))
    if not pointer.is_file():
        return
    existing = _read_pointer(pointer)
    tracked = Path(str(record["tracked_path"])) if record.get("tracked_path") else None
    payload = _keep_existing(_pointer_payload(config, record, str(record["retention"]), tracked), existing)
    if payload != existing:
        _atomic_json(pointer, payload)


def decide(config: SourceConfig, source_id: str, retention: str | None = None) -> dict[str, Any]:
    if retention is None:
        retention = str(config.values["default_decision"])
        if retention not in {"local", "git"}:
            raise LifecycleError(f"no retention decision: pass --retain local|git, or set source_store.default_decision to local or git (it is {retention!r}, which requires an explicit --retain)")
    record = _load_record(config, source_id)
    if record.get("status") == "quarantined":
        raise LifecycleError("quarantined bytes require separate review; retention decision refused")
    if not record.get("derived_artifacts"):
        raise LifecycleError("extract the source before recording a retention decision")
    source = Path(record["original_path"])
    if not source.is_file() or sha256_file(source) != record["sha256"]:
        raise LifecycleError("local original is missing or its SHA-256 no longer matches")
    tracked = None
    if retention == "git":
        if record["bytes"] > int(config.values["git_max_file_mib"]) * 1024 * 1024:
            raise LifecycleError("original exceeds source_store.git_max_file_mib")
        tracked = config.path("tracked_originals") / f"{source_id}-{_safe_name(str(record['input']))}"
        tracked.parent.mkdir(parents=True, exist_ok=True)
        if tracked.exists() and sha256_file(tracked) != record["sha256"]:
            raise LifecycleError(f"tracked destination already exists with different contents: {tracked}")
        if not tracked.exists():
            shutil.copy2(source, tracked)
        if sha256_file(tracked) != record["sha256"]:
            raise LifecycleError(f"copied original hash mismatch: {tracked}")
    pointer = config.path("pointers") / f"{source_id}.pointer.json"
    payload = _pointer_payload(config, record, retention, tracked)
    if pointer.exists():
        existing = _read_pointer(pointer)
        if _core(existing) != _core(payload):
            raise LifecycleError(f"pointer target already exists with different contents: {pointer}")
        # Same pointer: carry provenance the record gained since into it (an
        # older engine wrote pointers without retrieved_at, knowmade#118).
        upgraded = _keep_existing(payload, existing)
        if upgraded != existing:
            _atomic_json(pointer, upgraded)
        payload = upgraded
    else:
        _atomic_json(pointer, payload)
    record.update({"retention": retention, "pointer_path": str(pointer), "tracked_path": str(tracked) if tracked else None, "status": "decided"})
    _atomic_json(_record_path(config, source_id), record)
    return payload


def _resolve(config: SourceConfig, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (config.wiki_root / path).resolve()


def foreign_schema(data: Any) -> str | None:
    """How a JSON object that is not a source pointer labels itself, else None.

    A file in the pointers directory whose ``schema`` is not a
    ``llm-wiki/source-pointer/...`` string (a consumer's legacy pointer, say)
    is reported as foreign instead of being validated as a broken v1 pointer
    (knowmade#118). Corrupt JSON and non-objects are not foreign: they fail.
    """
    if not isinstance(data, dict):
        return None
    schema = data.get("schema")
    if isinstance(schema, str) and schema.startswith(POINTER_SCHEMA_FAMILY):
        return None
    if schema is not None:
        return f"schema {schema!r}"
    if "schema_version" in data:
        return f"schema_version {data['schema_version']!r}"
    return "no schema key"


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def validate_pointer_data(config: SourceConfig, data: Any, check_original: bool = True) -> list[str]:
    """Errors of one v1 pointer object; its relative paths resolve against the wiki root."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["invalid pointer JSON: expected an object"]
    missing = sorted(REQUIRED_POINTER - data.keys())
    if missing:
        return [f"missing pointer fields: {', '.join(missing)}"]
    if data.get("schema") != SCHEMA:
        errors.append(f"unsupported pointer schema: {data.get('schema')!r}")
    if data.get("retention") not in {"local", "git"}:
        errors.append("retention must be local or git")
    if not re.fullmatch(r"[0-9a-f]{64}", str(data.get("sha256", ""))):
        errors.append("sha256 must be 64 lowercase hex characters")
    if not isinstance(data.get("bytes"), int) or data.get("bytes", -1) < 0:
        errors.append("bytes must be a non-negative integer")
    derived = data.get("derived_artifacts")
    if not isinstance(derived, list):
        errors.append("derived_artifacts must be a list")
    else:
        for item in derived:
            if not isinstance(item, dict) or not all(key in item for key in ("path", "sha256", "bytes", "format")):
                errors.append("each derived artifact needs path, sha256, bytes and format")
                continue
            path = _resolve(config, str(item["path"]))
            if not path.is_file():
                errors.append(f"derived artifact unavailable: {item['path']}")
            elif sha256_file(path) != item["sha256"] or path.stat().st_size != item["bytes"]:
                errors.append(f"derived artifact hash/size mismatch: {item['path']}")
    original = data.get("original")
    if not isinstance(original, dict) or not original.get("local_path"):
        errors.append("original.local_path is required")
    else:
        local = _resolve(config, str(original["local_path"]))
        if check_original and local.exists() and (sha256_file(local) != data["sha256"] or local.stat().st_size != data["bytes"]):
            errors.append("available local original hash/size mismatch")
        tracked_value = original.get("tracked_path")
        if data.get("retention") == "git" and not tracked_value:
            errors.append("git retention requires original.tracked_path")
        elif tracked_value:
            tracked = _resolve(config, str(tracked_value))
            if not tracked.is_file():
                errors.append(f"tracked original unavailable: {tracked_value}")
            elif sha256_file(tracked) != data["sha256"] or tracked.stat().st_size != data["bytes"]:
                errors.append("tracked original hash/size mismatch")
    aliases = data.get("aliases")
    if aliases is not None and (not isinstance(aliases, list) or not all(isinstance(item, str) and item for item in aliases)):
        errors.append("aliases must be a list of non-empty strings")
    return errors


def validate_pointer(config: SourceConfig, pointer: Path, check_original: bool = True) -> list[str]:
    try:
        data = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"invalid pointer JSON: {exc}"]
    return validate_pointer_data(config, data, check_original)


def pointer_files(config: SourceConfig) -> list[Path]:
    """Every ``*.pointer.json`` under the pointers directory, foreign ones included."""
    root = config.path("pointers")
    return sorted(root.rglob("*.pointer.json")) if root.exists() else []


def classify_pointer_files(config: SourceConfig) -> tuple[list[Path], list[dict[str, str]]]:
    """``(pointers, foreign)``: the files to validate, and foreign-schema files with their label."""
    pointers: list[Path] = []
    foreign: list[dict[str, str]] = []
    for path in pointer_files(config):
        label = foreign_schema(_load_json(path))
        if label is None:
            pointers.append(path)
        else:
            foreign.append({"pointer": str(path), "schema": label})
    return pointers, foreign


def _verify_row(config: SourceConfig, pointer: Path, data: Any, check_original: bool, require_originals: bool) -> dict[str, Any]:
    original = data.get("original") if isinstance(data, dict) else None
    local_value = original.get("local_path") if isinstance(original, dict) else None
    available = bool(local_value and _resolve(config, str(local_value)).is_file())
    errors = validate_pointer(config, pointer, check_original=check_original)
    retention = data.get("retention") if isinstance(data, dict) else None
    if require_originals and retention == "local" and not available:
        errors.append("retention: local original is missing (the only copy)")
    return {"pointer": str(pointer), "ok": not errors, "retention": retention, "original_available": available, "errors": errors}


def verify(config: SourceConfig, source_id: str | None = None, *, check_original: bool = True, require_originals: bool = False, pointer_file: Path | None = None) -> dict[str, Any]:
    foreign: list[dict[str, str]] = []
    if pointer_file is not None:
        pointer_file = pointer_file.expanduser().resolve()
        if not pointer_file.is_file():
            raise LifecycleError(f"pointer file not found: {pointer_file}")
        label = foreign_schema(_load_json(pointer_file))
        if label is not None:
            raise LifecycleError(f"{pointer_file} is not a source pointer ({label}); `source import-pointer` maps a legacy pointer")
        pointers = [pointer_file]
    else:
        pointers, foreign = classify_pointer_files(config)
        if source_id:
            pointers = [path for path in pointers if path.name == f"{source_id}.pointer.json"]
            if not pointers:
                raise LifecycleError(f"pointer not found for source id: {source_id}")
            foreign = []
    results = [_verify_row(config, pointer, _load_json(pointer), check_original, require_originals) for pointer in pointers]
    return {
        "ok": all(item["ok"] for item in results),
        "count": len(results),
        "originals_available": sum(item["original_available"] for item in results),
        "originals_unavailable": sum(not item["original_available"] for item in results),
        "missing_originals": sum(item["retention"] == "local" and not item["original_available"] for item in results),
        "foreign": foreign,
        "results": results,
    }


def _pointer_for(config: SourceConfig, digest: str) -> tuple[Path | None, dict[str, Any] | None]:
    """The v1 pointer file and object for ``digest``, if the pointers directory holds one."""
    for path in pointer_files(config):
        if path.name != f"{digest}.pointer.json":
            continue
        data = _load_json(path)
        if isinstance(data, dict) and foreign_schema(data) is None and data.get("sha256") == digest:
            return path, data
    return None, None


def resolve(config: SourceConfig, digest: str, fallbacks: list[str] | None = None) -> dict[str, Any]:
    """The first readable original of ``digest`` whose bytes hash to it.

    Candidates, in order: the local store's original, the pointer's
    ``original.local_path`` and ``original.tracked_path``, then each
    ``--fallback`` (relative to the working directory). Nothing is written.
    """
    digest = digest.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise LifecycleError(f"expected a 64-character SHA-256, got {digest!r}")
    candidates: list[tuple[str, Path]] = []
    record_path = _record_path(config, digest)
    record = _load_json(record_path) if record_path.is_file() else None
    if isinstance(record, dict):
        if record.get("original_path"):
            candidates.append(("local_original", Path(str(record["original_path"]))))
        if record.get("tracked_path"):
            candidates.append(("tracked_original", Path(str(record["tracked_path"]))))
    pointer, data = _pointer_for(config, digest)
    original = data.get("original") if isinstance(data, dict) else None
    if isinstance(original, dict):
        if original.get("local_path"):
            candidates.append(("pointer_local_path", _resolve(config, str(original["local_path"]))))
        if original.get("tracked_path"):
            candidates.append(("tracked_original", _resolve(config, str(original["tracked_path"]))))
    candidates += [("fallback", Path(value).expanduser()) for value in fallbacks or []]
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    found: dict[str, Any] | None = None
    for via, path in candidates:
        path = path.resolve()
        if str(path) in seen:
            continue
        seen.add(str(path))
        if not path.is_file():
            status = "missing"
        elif found is not None:
            status = "unchecked"
        else:
            status = "match" if sha256_file(path) == digest else "mismatch"
        row = {"path": str(path), "via": via, "status": status}
        rows.append(row)
        if status == "match" and found is None:
            found = row
    return {
        "ok": found is not None,
        "sha256": digest,
        "path": found["path"] if found else None,
        "via": found["via"] if found else None,
        "pointer": str(pointer) if pointer else None,
        "candidates": rows,
    }


def paths(config: SourceConfig) -> dict[str, Any]:
    """The resolved store locations and whether this machine may receive sources."""
    ephemeral = is_ephemeral_ci()
    persistent = bool(config.values["persistent"])
    return {
        "wiki_root": str(config.wiki_root),
        "backend": config.values["backend"],
        "persistent": persistent,
        **{key: str(config.path(key)) for key in PATH_KEYS},
        "records": str(config.records),
        "explicit": sorted(config.explicit),
        "unknown_keys": list(config.unknown_keys),
        "ephemeral_ci": ephemeral,
        "receive_allowed": persistent or not ephemeral,
        "private_store_escapes": private_store_escapes(config),
    }


def _legacy_url(legacy: dict[str, Any]) -> str | None:
    if isinstance(legacy.get("source_url"), str):
        return legacy["source_url"]
    for item in legacy.get("acquisition_provenance") or []:
        if isinstance(item, dict):
            for key in ("final_url", "url"):
                if isinstance(item.get(key), str) and urllib.parse.urlparse(item[key]).scheme in {"http", "https"}:
                    return item[key]
    return None


def _legacy_pointer(config: SourceConfig, legacy: dict[str, Any], origin: Path, base: Path, vault: Path | None, derived_status: str) -> dict[str, Any]:
    """Map a flat legacy pointer (``sha256``, ``bytes``, original path keys, ``derived_text``) onto v1."""
    digest = str(legacy.get("sha256", "")).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise LifecycleError(f"{origin}: legacy pointer has no 64-character sha256")
    candidates = [base / str(legacy["local_original_path"])] if legacy.get("local_original_path") else []
    if vault is not None and legacy.get("vault_relative_path"):
        candidates.append(vault / str(legacy["vault_relative_path"]))
    if legacy.get("original_repo_path"):
        candidates.append(base / str(legacy["original_repo_path"]))
    if not candidates:
        raise LifecycleError(f"{origin}: legacy pointer names no original (local_original_path, vault_relative_path with --vault, or original_repo_path)")
    original = next((path for path in candidates if path.is_file()), candidates[0]).resolve()
    size = legacy.get("bytes")
    if not isinstance(size, int) and original.is_file():
        size = original.stat().st_size
    artifacts = []
    for value in legacy.get("derived_text") or []:
        path = (base / str(value)).resolve()
        if not path.is_file():
            raise LifecycleError(f"{origin}: derived artifact unavailable: {path}")
        artifacts.append(
            {
                "path": _portable(path, config.wiki_root),
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
                "format": mimetypes.guess_type(path.name)[0] or "text/plain",
                "extraction_status": derived_status,
            }
        )
    content_format = legacy.get("content_type") or next((item.get("content_type") for item in legacy.get("acquisition_provenance") or [] if isinstance(item, dict) and item.get("content_type")), None)
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "source_id": digest,
        "recorded_at": legacy.get("externalized_at_utc") or legacy.get("recorded_at_utc") or utc_now(),
        "sha256": digest,
        "bytes": size,
        "content_format": str(content_format or mimetypes.guess_type(original.name)[0] or "application/octet-stream"),
        "retention": "local",
        "canonical_url": _legacy_url(legacy),
        "original": {"local_path": _portable(original, config.wiki_root)},
        "derived_artifacts": artifacts,
        "quarantine": None,
    }
    retrieved = legacy.get("retrieved_at") or legacy.get("retrieved_at_utc")
    if retrieved:
        payload["retrieved_at"] = str(retrieved)
    if legacy.get("note"):
        payload["note"] = str(legacy["note"])
    if legacy.get("original_repo_path"):
        payload["aliases"] = [str(legacy["original_repo_path"]).replace("\\", "/")]
    payload["imported_from"] = _portable(origin, config.wiki_root)
    return payload


def import_pointer(config: SourceConfig, file: Path, *, base: Path | None = None, vault: Path | None = None, derived_status: str = "imported", dry_run: bool = False) -> dict[str, Any]:
    """Validate a v1 or flat legacy pointer and place it in the pointers directory.

    A v1 pointer is taken as it is; a legacy one is mapped (``_legacy_pointer``,
    paths relative to ``base``, by default the wiki root's parent). A pointer
    for the same bytes that is already there stays authoritative; the import
    only adds its aliases. ``dry_run`` validates and writes nothing.
    """
    if not DERIVED_STATUS.fullmatch(derived_status):
        raise LifecycleError(f"--derived-status must be a lowercase slug, got {derived_status!r}")
    file = file.expanduser().resolve()
    if not file.is_file():
        raise LifecycleError(f"pointer file not found: {file}")
    data = _load_json(file)
    if not isinstance(data, dict):
        raise LifecycleError(f"{file} is not a JSON object")
    schema = data.get("schema")
    if isinstance(schema, str) and schema.startswith(POINTER_SCHEMA_FAMILY):
        payload = data
    else:
        payload = _legacy_pointer(config, data, file, (base or config.wiki_root.parent).expanduser().resolve(), vault.expanduser().resolve() if vault else None, derived_status)
    errors = validate_pointer_data(config, payload)
    destination = config.path("pointers") / f"{payload.get('sha256')}.pointer.json"
    result: dict[str, Any] = {"ok": not errors, "pointer": str(destination), "written": False, "merged": False, "dry_run": dry_run, "errors": errors, "payload": payload}
    if errors:
        return result
    if destination.exists():
        existing = _read_pointer(destination)
        if existing.get("sha256") != payload["sha256"] or existing.get("bytes") != payload["bytes"]:
            raise LifecycleError(f"pointer target already exists for other bytes: {destination}")
        aliases = sorted(set(existing.get("aliases") or []) | set(payload.get("aliases") or []))
        merged = {**existing, "aliases": aliases} if aliases else existing
        result.update({"merged": True, "payload": merged})
        if merged != existing and not dry_run:
            _atomic_json(destination, merged)
            result["written"] = True
        return result
    if not dry_run:
        _atomic_json(destination, payload)
        result["written"] = True
    return result


def alias_map(config: SourceConfig) -> dict[str, Any]:
    """Every alias the local records and the v1 pointers name, and aliases claimed by two sources."""
    owners: dict[str, set[str]] = {}
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    sources: list[tuple[dict[str, Any], str, str]] = []
    if config.records.is_dir():
        sources += [(data, "record", str(path)) for path in sorted(config.records.glob("*.json")) if isinstance(data := _load_json(path), dict)]
    pointers, _ = classify_pointer_files(config)
    sources += [(data, "pointer", str(path)) for path in pointers if isinstance(data := _load_json(path), dict)]
    for data, kind, where in sources:
        source_id = str(data.get("source_id") or data.get("sha256") or "")
        for alias in data.get("aliases") or []:
            if not isinstance(alias, str):
                continue
            owners.setdefault(alias, set()).add(source_id)
            row = rows.setdefault((alias, source_id), {"alias": alias, "source_id": source_id, "record": None, "pointer": None})
            row[kind] = row[kind] or where
    conflicts = [{"alias": alias, "source_ids": sorted(ids)} for alias, ids in sorted(owners.items()) if len(ids) > 1]
    return {"ok": not conflicts, "count": len(rows), "aliases": [rows[key] for key in sorted(rows)], "conflicts": conflicts}


def _add_resolution(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--path", help="Wiki root; defaults to normal wiki resolution.")
    parser.add_argument("--local", action="store_true", help="Target .wiki/ in the current directory.")
    parser.add_argument("--wiki", help="Named wiki from the hub registry.")
    parser.add_argument("--hub", help="Override hub path for --wiki resolution.")
    # The output is always JSON; --json is accepted so callers can pass it uniformly.
    parser.add_argument("--json", action="store_true", help=argparse.SUPPRESS)


SOURCE_HELP = "Receive, extract, retain, resolve and verify source originals for one wiki."


def _add_arguments(parser: argparse.ArgumentParser, extensions: tuple[Any, ...] = ()) -> None:
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("receive", help="Copy a file or http(s) URL into the source store and record it.")
    p.add_argument("source", help="File path or http(s) URL of the bytes to receive.")
    p.add_argument("--quarantine-reason", help="Hold the bytes in quarantine for this reason instead of the originals store.")
    p.add_argument("--canonical-url", help="Record this http(s) URL as the source's canonical URL (for a file captured from the web earlier).")
    p.add_argument("--retrieved-at", help="ISO 8601 time the bytes were retrieved; recorded with --canonical-url.")
    p.add_argument("--note", help="Free-text note recorded with the source.")
    p.add_argument("--alias", action="append", default=[], metavar="PATH", help="Another name (typically a repository path) of the same bytes; repeatable.")
    _add_resolution(p)
    p = sub.add_parser("extract", help="Extract text from a received original into a derived artifact.")
    p.add_argument("source_id", help="Id of a received source.")
    _add_resolution(p)
    p = sub.add_parser("attach-derived", help="Store an externally produced derivative (OCR text) of a received source.")
    p.add_argument("source_id", help="Id of a received source.")
    p.add_argument("file", help="The derived file to store.")
    p.add_argument("--status", required=True, help="extraction_status slug, such as ocr-text.")
    p.add_argument("--tool", required=True, help="Program that produced the file.")
    _add_resolution(p)
    p = sub.add_parser("decide", help="Record where the original is retained (local store or git).")
    p.add_argument("source_id", help="Id of an extracted source.")
    p.add_argument("--retain", choices=("local", "git"), help="Retention decision; defaults to source_store.default_decision when that is local or git.")
    _add_resolution(p)
    p = sub.add_parser("verify", help="Check records and pointers against the stored originals.")
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("source_id", nargs="?", help="Verify this one source.")
    target.add_argument("--all", action="store_true", help="Verify every source.")
    target.add_argument("--pointer-file", metavar="PATH", help="Verify one pointer file anywhere; its paths resolve against the wiki root.")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--portable", action="store_true", help="Model a clone without the local store: skip hash-checking local originals.")
    mode.add_argument("--require-originals", action="store_true", help="Fail when a retention: local pointer's original is missing (it is the only copy).")
    _add_resolution(p)
    p = sub.add_parser("resolve", help="Print the first available original whose bytes match a SHA-256.")
    p.add_argument("sha256", help="SHA-256 of the original to find.")
    p.add_argument("--fallback", action="append", default=[], metavar="PATH", help="Another candidate file, tried after the store; repeatable.")
    _add_resolution(p)
    p = sub.add_parser("paths", help="Print the resolved source_store locations and the ephemeral-CI verdict.")
    _add_resolution(p)
    p = sub.add_parser("import-pointer", help="Validate a v1 or legacy pointer file and place it in the pointers directory.")
    p.add_argument("file", help="The pointer file to import.")
    p.add_argument("--base", metavar="DIR", help="Directory a legacy pointer's paths are relative to (default: the wiki root's parent).")
    p.add_argument("--vault", metavar="DIR", help="Directory a legacy vault_relative_path is relative to.")
    p.add_argument("--derived-status", default="imported", help="extraction_status for a legacy pointer's derived_text (default: imported).")
    p.add_argument("--dry-run", action="store_true", help="Validate and print the pointer; write nothing.")
    _add_resolution(p)
    p = sub.add_parser("map", help="List the aliases records and pointers name, and aliases claimed by two sources.")
    _add_resolution(p)
    # Verbs of other modules (``raw_note``): each registers its own subparser and sets ``handler``.
    for register in extensions:
        register(sub, _add_resolution)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="llm-wiki source", description=SOURCE_HELP)
    _add_arguments(parser)
    return parser


def register_parser(subparsers: Any, *, resolve_wiki_root: Any = None, extensions: tuple[Any, ...] = ()) -> argparse.ArgumentParser:
    """Register ``source`` on a parent ``llm-wiki`` parser; dispatch through :func:`run`.

    ``resolve_wiki_root`` is called with the parsed namespace; the parent passes
    a thin wrapper so the resolver it installs after import is the one used.
    """
    parser = subparsers.add_parser("source", help=SOURCE_HELP, description=SOURCE_HELP)
    _add_arguments(parser, extensions)
    parser.set_defaults(func=lambda args: run(args, resolve_wiki_root=resolve_wiki_root))
    return parser


def run(args: argparse.Namespace, *, resolve_wiki_root: Any = None) -> int:
    """Exit 0 on success, 1 when the JSON says ``ok: false``, 2 on a usage or store error."""
    handler = getattr(args, "handler", None)
    if handler is not None:  # a verb registered through ``extensions``
        return handler(args, resolve_wiki_root)
    try:
        root = Path(resolve_wiki_root(args) if resolve_wiki_root else (args.path or Path.cwd() / ".wiki")).resolve()
        config = load_config(root)
        for warning in config_warnings(config):
            print(f"source lifecycle note: {warning}", file=sys.stderr)
        if args.action in WRITING_ACTIONS and not getattr(args, "dry_run", False):
            require_private_store_inside(config)
        if args.action == "receive":
            result = receive(config, args.source, args.quarantine_reason, args.canonical_url, args.retrieved_at, args.note, args.alias)
        elif args.action == "extract":
            result = extract(config, args.source_id)
        elif args.action == "attach-derived":
            result = attach_derived(config, args.source_id, Path(args.file), args.status, args.tool)
        elif args.action == "decide":
            result = decide(config, args.source_id, args.retain)
        elif args.action == "resolve":
            result = resolve(config, args.sha256, args.fallback)
        elif args.action == "paths":
            result = paths(config)
        elif args.action == "import-pointer":
            result = import_pointer(
                config, Path(args.file), base=Path(args.base) if args.base else None, vault=Path(args.vault) if args.vault else None, derived_status=args.derived_status, dry_run=args.dry_run
            )
        elif args.action == "map":
            result = alias_map(config)
        else:
            result = verify(config, args.source_id, check_original=not args.portable, require_originals=args.require_originals, pointer_file=Path(args.pointer_file) if args.pointer_file else None)
    except (LifecycleError, OSError) as exc:
        print(f"source lifecycle error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok", True) else 1


def main(argv: list[str] | None = None, *, resolve_wiki_root: Any = None) -> int:
    configure_utf8_stdio()
    return run(build_parser().parse_args(argv), resolve_wiki_root=resolve_wiki_root)
