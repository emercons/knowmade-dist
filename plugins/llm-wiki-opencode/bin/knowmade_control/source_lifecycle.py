"""Durable project-local lifecycle for source originals and derivatives."""

from __future__ import annotations

import argparse
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
        _spec = importlib.util.spec_from_file_location(
            _PACKAGE, _PACKAGE_DIR / "__init__.py", submodule_search_locations=[str(_PACKAGE_DIR)]
        )
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
    "backend": "filesystem", "persistent": False,
    "local_originals": "../.wiki-local/originals", "tracked_originals": "../sources/raw",
    "derived": "../sources/text", "pointers": "../sources/pointers",
    "quarantine": "../.wiki-local/quarantine", "default_decision": "review-required",
    "git_max_file_mib": 95, "receive_max_file_mib": 1024,
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
WRITING_ACTIONS = ("receive", "extract", "decide")
REQUIRED_POINTER = {"schema", "source_id", "sha256", "bytes", "content_format", "retention", "original", "derived_artifacts"}


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
        if close > 0 and value[close + 1 :].lstrip()[:1] in {"", "#"}: return value[: close + 1]
    return re.split(r"(?:^|\s)#", value, maxsplit=1)[0].strip()


def _scalar(value: str) -> Any:
    value = _strip_comment(value)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'": value = value[1:-1]
    if value.lower() in _TRUE_WORDS | _FALSE_WORDS: return value.lower() in _TRUE_WORDS
    if re.fullmatch(r"\d+", value): return int(value)
    return value


def _frontmatter(path: Path) -> str:
    """The frontmatter block of ``path`` ("" when it has none), via the shared reader."""
    try: return _fm.read_block(path)
    except _fm.FrontmatterError: return ""


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
    wiki_root = wiki_root.resolve(); config_path = wiki_root / "config.md"
    if not config_path.is_file(): raise LifecycleError(f"wiki config not found: {config_path}")
    values = dict(DEFAULTS); explicit: set[str] = set(); section_indent: int | None = None; unknown: list[str] = []
    for line in _frontmatter(config_path).splitlines():
        if not line.strip() or line.lstrip().startswith("#"): continue
        indent = len(line) - len(line.lstrip(" "))
        if re.fullmatch(r"source_store\s*:\s*", line.strip()): section_indent = indent; continue
        if section_indent is None: continue
        if indent <= section_indent: section_indent = None; continue
        match = re.match(r"\s+([A-Za-z0-9_-]+)\s*:\s*(.*?)\s*$", line)
        if not match: continue
        if match.group(1) in DEFAULTS: values[match.group(1)] = _scalar(match.group(2)); explicit.add(match.group(1))
        elif match.group(1) not in unknown: unknown.append(match.group(1))
    if values["backend"] != "filesystem": raise LifecycleError(f"unsupported source_store backend {values['backend']!r}; Phase 1 supports filesystem")
    for key in BOOL_KEYS:
        if not isinstance(values[key], bool): raise LifecycleError(f"source_store.{key} must be a boolean (true/false, yes/no or on/off), got {values[key]!r}")
    for key in INT_KEYS:
        if isinstance(values[key], bool) or not isinstance(values[key], int) or values[key] <= 0: raise LifecycleError(f"source_store.{key} must be a positive integer")
    if values["default_decision"] not in DECISIONS: raise LifecycleError(f"source_store.default_decision must be one of {', '.join(DECISIONS)}, got {values['default_decision']!r}")
    if hub_of_topic(wiki_root) is not None:
        for key in PATH_KEYS:
            if key not in explicit: values[key] = _inside_root(str(values[key]))
    return SourceConfig(wiki_root, values, tuple(unknown), frozenset(explicit))


def config_warnings(config: SourceConfig) -> list[str]:
    """Advisory findings about ``config``, shared by the ``source`` command and lint."""
    if not config.unknown_keys: return []
    return [f"unknown source_store key(s) ignored: {', '.join(config.unknown_keys)} (known keys: {', '.join(DEFAULTS)})"]


def _is_under(path: Path, root: Path) -> bool:
    try: path.resolve().relative_to(root.resolve()); return True
    except ValueError: return False


def private_store_escapes(config: SourceConfig) -> list[str]:
    """Default store keys of a ``-PRIVATE`` wiki that resolve outside its directory.

    Only the wiki directory itself is covered by the hub's ``-PRIVATE`` ignore
    rule, so a default that lands beside it would put private originals and
    text in a committable place. An explicit ``source_store`` key is the
    owner's decision and is never reported.
    """
    if not config.wiki_root.name.endswith(PRIVATE_SUFFIX): return []
    return [key for key in PATH_KEYS if key not in config.explicit and not _is_under(config.path(key), config.wiki_root)]


def require_private_store_inside(config: SourceConfig) -> None:
    escapes = private_store_escapes(config)
    if not escapes: return
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
        try: relative = config.path(key).relative_to(config.wiki_root)
        except ValueError: continue
        if relative.parts: names.add(relative.parts[0])
    return names


def is_ephemeral_ci() -> bool:
    return any(os.environ.get(name, "").strip().lower() not in {"", "0", "false", "no"} for name in CI_MARKERS)


def _safe_name(value: str) -> str:
    name = Path(urllib.parse.urlparse(value).path).name or "source.bin"
    return (re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-") or "source.bin")[:160]


def _portable(path: Path, base: Path) -> str:
    try: return Path(os.path.relpath(path.resolve(), base.resolve())).as_posix()
    except ValueError: return str(path.resolve())


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, payload)


def _record_path(config: SourceConfig, source_id: str) -> Path:
    return config.records / f"{source_id}.json"


def _load_record(config: SourceConfig, source_id: str) -> dict[str, Any]:
    path = _record_path(config, source_id)
    if not path.exists(): raise LifecycleError(f"unknown source id: {source_id}")
    try: return read_json_object(path, "local source record")
    except JSONReadError as exc: raise LifecycleError(str(exc)) from exc


def _existing_record(config: SourceConfig, source_id: str) -> dict[str, Any] | None:
    """The stored lifecycle record for ``source_id``, or None when it is new."""
    if not _record_path(config, source_id).exists():
        return None
    return _load_record(config, source_id)


def _copy_stream(source: str, destination: Path, limit: int) -> tuple[int, str | None]:
    parsed = urllib.parse.urlparse(source); destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.name + ".part"); content_type = None; total = 0
    try:
        if parsed.scheme in {"http", "https"}:
            stream = urllib.request.urlopen(urllib.request.Request(source, headers={"User-Agent": "llm-wiki-source/1"}), timeout=30)  # noqa: S310
            content_type = stream.headers.get_content_type()
        else: stream = Path(source).expanduser().resolve().open("rb")
        with stream, temp.open("wb") as output:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                total += len(block)
                if total > limit: raise LifecycleError(f"source exceeds configured receive limit of {limit} bytes")
                output.write(block)
        temp.replace(destination)
    except Exception: temp.unlink(missing_ok=True); raise
    return total, content_type


def _provenance(source: str, canonical_url: str | None, retrieved_at: str | None) -> tuple[str | None, str | None]:
    """The validated ``(canonical_url, retrieved_at)`` to record for a received source."""
    if canonical_url is not None and urllib.parse.urlparse(canonical_url).scheme not in {"http", "https"}:
        raise LifecycleError(f"--canonical-url must be an http(s) URL: {canonical_url!r}")
    if retrieved_at is not None:
        try: datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
        except ValueError as exc: raise LifecycleError(f"--retrieved-at must be an ISO 8601 timestamp: {retrieved_at!r}") from exc
    url = canonical_url or (source if urllib.parse.urlparse(source).scheme in {"http", "https"} else None)
    return url, retrieved_at


def receive(config: SourceConfig, source: str, quarantine_reason: str | None = None, canonical_url: str | None = None, retrieved_at: str | None = None) -> dict[str, Any]:
    canonical_url, retrieved_at = _provenance(source, canonical_url, retrieved_at)
    if is_ephemeral_ci() and not bool(config.values["persistent"]):
        raise LifecycleError("source receive refused on an ephemeral CI runner: configure a persistent source_store backend and set source_store.persistent: true")
    provisional = config.path("quarantine" if quarantine_reason else "local_originals") / _safe_name(source)
    size, content_type = _copy_stream(source, provisional, int(config.values["receive_max_file_mib"]) * 1024 * 1024)
    if size == 0 and not quarantine_reason: quarantine_reason = "empty-payload"
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
        return {**known, "already_known": True, "original_restored": restored}
    destination = config.path("quarantine" if quarantine_reason else "local_originals") / digest[:2] / f"{digest}-{_safe_name(source)}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if sha256_file(destination) != digest: raise LifecycleError(f"duplicate destination differs from received bytes: {destination}")
        provisional.unlink(missing_ok=True)
    else: provisional.replace(destination)
    record = {"schema": RECORD_SCHEMA, "source_id": digest, "received_at": utc_now(), "status": "quarantined" if quarantine_reason else "received", "original_path": str(destination), "sha256": digest, "bytes": size, "content_format": (content_type or mimetypes.guess_type(destination.name)[0] or "application/octet-stream").split(";", 1)[0], "canonical_url": canonical_url, "retrieved_at": retrieved_at, "input": source, "quarantine_reason": quarantine_reason, "derived_artifacts": [], "retention": None}
    _atomic_json(_record_path(config, digest), record); return {**record, "already_known": False}


def _extract_pdf(source: Path) -> tuple[str, str]:
    executable = shutil.which("pdftotext")
    if executable:
        result = subprocess.run([executable, "-layout", str(source), "-"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, check=False)
        if result.returncode == 0 and result.stdout.strip(): return result.stdout, "text-extracted"
    try:
        from pypdf import PdfReader  # type: ignore
        text = "\n\n\f\n\n".join(page.extract_text() or "" for page in PdfReader(str(source)).pages)
        if text.strip(): return text, "text-extracted"
    except Exception:  # malformed/image-only PDFs vary across optional extractors
        pass
    return "[No embedded text was extracted. OCR is required and remains a topic-specific workflow.]\n", "ocr-needed"


def extract(config: SourceConfig, source_id: str) -> dict[str, Any]:
    record = _load_record(config, source_id)
    if record.get("status") == "quarantined": raise LifecycleError("quarantined bytes cannot be extracted until separately reviewed")
    source = Path(record["original_path"])
    if not source.is_file() or sha256_file(source) != record["sha256"]: raise LifecycleError("local original is unavailable or its hash no longer matches")
    fmt = str(record["content_format"]); status = "text-extracted"
    if fmt == "application/pdf" or source.suffix.lower() == ".pdf": text, status = _extract_pdf(source)
    elif fmt.startswith("text/") or source.suffix.lower() in {".md", ".txt", ".csv", ".json", ".xml", ".html", ".htm"}:
        text = source.read_text(encoding="utf-8", errors="replace")
        if source.suffix.lower() in {".html", ".htm"}: text = unescape(re.sub(r"<[^>]+>", " ", text))
    else: text, status = "[Binary original retained; no generic text extractor is available for this format.]\n", "metadata-only"
    destination = config.path("derived") / f"{source_id}.txt"; destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.read_text(encoding="utf-8", errors="replace") != text: raise LifecycleError(f"derived target already exists with different contents: {destination}")
    if not destination.exists(): destination.write_bytes(text.encode("utf-8"))
    artifact = {"path": _portable(destination, config.wiki_root), "sha256": sha256_file(destination), "bytes": destination.stat().st_size, "format": "text/plain", "extraction_status": status}
    record.update({"derived_artifacts": [artifact], "status": "extracted", "extracted_at": utc_now()}); _atomic_json(_record_path(config, source_id), record); return record


def _pointer_payload(config: SourceConfig, record: dict[str, Any], retention: str, tracked: Path | None) -> dict[str, Any]:
    original = {"local_path": _portable(Path(record["original_path"]), config.wiki_root)}
    if tracked is not None: original["tracked_path"] = _portable(tracked, config.wiki_root)
    return {"schema": SCHEMA, "source_id": record["source_id"], "recorded_at": utc_now(), "sha256": record["sha256"], "bytes": record["bytes"], "content_format": record["content_format"], "retention": retention, "canonical_url": record.get("canonical_url"), "original": original, "derived_artifacts": record.get("derived_artifacts", []), "quarantine": None}


def decide(config: SourceConfig, source_id: str, retention: str | None = None) -> dict[str, Any]:
    if retention is None:
        retention = str(config.values["default_decision"])
        if retention not in {"local", "git"}: raise LifecycleError(f"no retention decision: pass --retain local|git, or set source_store.default_decision to local or git (it is {retention!r}, which requires an explicit --retain)")
    record = _load_record(config, source_id)
    if record.get("status") == "quarantined": raise LifecycleError("quarantined bytes require separate review; retention decision refused")
    if not record.get("derived_artifacts"): raise LifecycleError("extract the source before recording a retention decision")
    source = Path(record["original_path"])
    if not source.is_file() or sha256_file(source) != record["sha256"]: raise LifecycleError("local original is missing or its SHA-256 no longer matches")
    tracked = None
    if retention == "git":
        if record["bytes"] > int(config.values["git_max_file_mib"]) * 1024 * 1024: raise LifecycleError("original exceeds source_store.git_max_file_mib")
        tracked = config.path("tracked_originals") / f"{source_id}-{_safe_name(str(record['input']))}"; tracked.parent.mkdir(parents=True, exist_ok=True)
        if tracked.exists() and sha256_file(tracked) != record["sha256"]: raise LifecycleError(f"tracked destination already exists with different contents: {tracked}")
        if not tracked.exists(): shutil.copy2(source, tracked)
        if sha256_file(tracked) != record["sha256"]: raise LifecycleError(f"copied original hash mismatch: {tracked}")
    pointer = config.path("pointers") / f"{source_id}.pointer.json"; payload = _pointer_payload(config, record, retention, tracked)
    if pointer.exists():
        try: existing = json.loads(pointer.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc: raise LifecycleError(f"invalid pointer JSON {pointer}: {exc}") from exc
        if not isinstance(existing, dict): raise LifecycleError(f"invalid pointer JSON {pointer}: expected an object")
        if {k: v for k, v in existing.items() if k != "recorded_at"} != {k: v for k, v in payload.items() if k != "recorded_at"}: raise LifecycleError(f"pointer target already exists with different contents: {pointer}")
        payload = existing
    else: _atomic_json(pointer, payload)
    record.update({"retention": retention, "pointer_path": str(pointer), "tracked_path": str(tracked) if tracked else None, "status": "decided"}); _atomic_json(_record_path(config, source_id), record); return payload


def _resolve(config: SourceConfig, value: str) -> Path:
    path = Path(value); return path if path.is_absolute() else (config.wiki_root / path).resolve()


def validate_pointer(config: SourceConfig, pointer: Path, check_original: bool = True) -> list[str]:
    errors: list[str] = []
    try: data = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc: return [f"invalid pointer JSON: {exc}"]
    if not isinstance(data, dict): return ["invalid pointer JSON: expected an object"]
    missing = sorted(REQUIRED_POINTER - data.keys())
    if missing: return [f"missing pointer fields: {', '.join(missing)}"]
    if data.get("schema") != SCHEMA: errors.append(f"unsupported pointer schema: {data.get('schema')!r}")
    if data.get("retention") not in {"local", "git"}: errors.append("retention must be local or git")
    if not re.fullmatch(r"[0-9a-f]{64}", str(data.get("sha256", ""))): errors.append("sha256 must be 64 lowercase hex characters")
    if not isinstance(data.get("bytes"), int) or data.get("bytes", -1) < 0: errors.append("bytes must be a non-negative integer")
    derived = data.get("derived_artifacts")
    if not isinstance(derived, list): errors.append("derived_artifacts must be a list")
    else:
        for item in derived:
            if not isinstance(item, dict) or not all(key in item for key in ("path", "sha256", "bytes", "format")): errors.append("each derived artifact needs path, sha256, bytes and format"); continue
            path = _resolve(config, str(item["path"]))
            if not path.is_file(): errors.append(f"derived artifact unavailable: {item['path']}")
            elif sha256_file(path) != item["sha256"] or path.stat().st_size != item["bytes"]: errors.append(f"derived artifact hash/size mismatch: {item['path']}")
    original = data.get("original")
    if not isinstance(original, dict) or not original.get("local_path"): errors.append("original.local_path is required")
    else:
        local = _resolve(config, str(original["local_path"]))
        if check_original and local.exists() and (sha256_file(local) != data["sha256"] or local.stat().st_size != data["bytes"]): errors.append("available local original hash/size mismatch")
        tracked_value = original.get("tracked_path")
        if data.get("retention") == "git" and not tracked_value: errors.append("git retention requires original.tracked_path")
        elif tracked_value:
            tracked = _resolve(config, str(tracked_value))
            if not tracked.is_file(): errors.append(f"tracked original unavailable: {tracked_value}")
            elif sha256_file(tracked) != data["sha256"] or tracked.stat().st_size != data["bytes"]: errors.append("tracked original hash/size mismatch")
    return errors


def pointer_files(config: SourceConfig) -> list[Path]:
    root = config.path("pointers"); return sorted(root.rglob("*.pointer.json")) if root.exists() else []


def verify(config: SourceConfig, source_id: str | None = None, *, check_original: bool = True, require_originals: bool = False) -> dict[str, Any]:
    pointers = pointer_files(config)
    if source_id:
        pointers = [path for path in pointers if path.name == f"{source_id}.pointer.json"]
        if not pointers: raise LifecycleError(f"pointer not found for source id: {source_id}")
    results = []
    for pointer in pointers:
        try: data = json.loads(pointer.read_text(encoding="utf-8"))
        except (OSError, ValueError): data = None  # validate_pointer reports the failure below
        original = data.get("original") if isinstance(data, dict) else None
        local_value = original.get("local_path") if isinstance(original, dict) else None
        available = bool(local_value and _resolve(config, str(local_value)).is_file())
        errors = validate_pointer(config, pointer, check_original=check_original)
        retention = data.get("retention") if isinstance(data, dict) else None
        sole_copy_missing = retention == "local" and not available
        if require_originals and sole_copy_missing: errors.append("retention: local original is missing (the only copy)")
        results.append({"pointer": str(pointer), "ok": not errors, "retention": retention, "original_available": available, "errors": errors})
    return {"ok": all(item["ok"] for item in results), "count": len(results), "originals_available": sum(item["original_available"] for item in results), "originals_unavailable": sum(not item["original_available"] for item in results), "missing_originals": sum(item["retention"] == "local" and not item["original_available"] for item in results), "results": results}


def _add_resolution(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--path", help="Wiki root; defaults to normal wiki resolution."); parser.add_argument("--local", action="store_true"); parser.add_argument("--wiki"); parser.add_argument("--hub")


SOURCE_HELP = "Receive, extract, retain, and verify source originals for one wiki."


def _add_arguments(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("receive"); p.add_argument("source"); p.add_argument("--quarantine-reason"); p.add_argument("--canonical-url", help="Record this http(s) URL as the source's canonical URL (for a file captured from the web earlier)."); p.add_argument("--retrieved-at", help="ISO 8601 time the bytes were retrieved; recorded with --canonical-url."); _add_resolution(p)
    p = sub.add_parser("extract"); p.add_argument("source_id"); _add_resolution(p)
    p = sub.add_parser("decide"); p.add_argument("source_id"); p.add_argument("--retain", choices=("local", "git"), help="Retention decision; defaults to source_store.default_decision when that is local or git."); _add_resolution(p)
    p = sub.add_parser("verify"); target = p.add_mutually_exclusive_group(required=True); target.add_argument("source_id", nargs="?"); target.add_argument("--all", action="store_true"); mode = p.add_mutually_exclusive_group(); mode.add_argument("--portable", action="store_true", help="Model a clone without the local store: skip hash-checking local originals."); mode.add_argument("--require-originals", action="store_true", help="Fail when a retention: local pointer's original is missing (it is the only copy)."); _add_resolution(p)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="llm-wiki source", description=SOURCE_HELP)
    _add_arguments(parser)
    return parser


def register_parser(subparsers: Any, *, resolve_wiki_root: Any = None) -> argparse.ArgumentParser:
    """Register ``source`` on a parent ``llm-wiki`` parser; dispatch through :func:`run`.

    ``resolve_wiki_root`` is called with the parsed namespace; the parent passes
    a thin wrapper so the resolver it installs after import is the one used.
    """
    parser = subparsers.add_parser("source", help=SOURCE_HELP, description=SOURCE_HELP)
    _add_arguments(parser)
    parser.set_defaults(func=lambda args: run(args, resolve_wiki_root=resolve_wiki_root))
    return parser


def run(args: argparse.Namespace, *, resolve_wiki_root: Any = None) -> int:
    try:
        root = Path(resolve_wiki_root(args) if resolve_wiki_root else (args.path or Path.cwd() / ".wiki")).resolve(); config = load_config(root)
        for warning in config_warnings(config): print(f"source lifecycle note: {warning}", file=sys.stderr)
        if args.action in WRITING_ACTIONS: require_private_store_inside(config)
        if args.action == "receive": result = receive(config, args.source, args.quarantine_reason, args.canonical_url, args.retrieved_at)
        elif args.action == "extract": result = extract(config, args.source_id)
        elif args.action == "decide": result = decide(config, args.source_id, args.retain)
        else: result = verify(config, args.source_id, check_original=not args.portable, require_originals=args.require_originals)
    except (LifecycleError, OSError) as exc: print(f"source lifecycle error: {exc}", file=sys.stderr); return 2
    print(json.dumps(result, ensure_ascii=False, indent=2)); return 0 if result.get("ok", True) else 1


def main(argv: list[str] | None = None, *, resolve_wiki_root: Any = None) -> int:
    configure_utf8_stdio()
    return run(build_parser().parse_args(argv), resolve_wiki_root=resolve_wiki_root)
