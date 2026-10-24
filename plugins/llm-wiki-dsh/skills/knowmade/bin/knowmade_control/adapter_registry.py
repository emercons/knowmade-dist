"""The machine-local private-adapter registry file.

Where the registry lives (``$LLM_WIKI_CONFIG_DIR`` or ``~/.config/llm-wiki``),
how it is read and written (through ``fsio``, owner-only permissions), and the
lookups on its entries. Adapter code and credentials stay outside the wiki.
Split from ``adapters`` (emercons/knowmade#105 R6).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import fsio as _fsio
from .roots import (
    expand_leading_tilde,
    home_directory,
)


ADAPTER_REGISTRY_SCHEMA = 1


def adapter_config_dir() -> Path:
    override = os.environ.get("LLM_WIKI_CONFIG_DIR")
    if override:
        return expand_leading_tilde(override)
    return home_directory() / ".config" / "llm-wiki"


def adapter_registry_path() -> Path:
    return adapter_config_dir() / "adapters.json"


def empty_adapter_registry() -> dict[str, Any]:
    return {"schema_version": ADAPTER_REGISTRY_SCHEMA, "adapters": {}}


def read_adapter_registry() -> dict[str, Any]:
    path = adapter_registry_path()
    if not path.exists():
        return empty_adapter_registry()
    data = _fsio.read_json_object(path, "adapter registry")
    if data.get("schema_version") != ADAPTER_REGISTRY_SCHEMA:
        raise SystemExit(
            f"unsupported adapter registry schema in {path}: "
            f"expected {ADAPTER_REGISTRY_SCHEMA}"
        )
    if not isinstance(data.get("adapters"), dict):
        raise SystemExit(f"invalid adapter registry {path}: adapters must be an object")
    return data


def write_adapter_registry(data: dict[str, Any]) -> None:
    path = adapter_registry_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    _fsio.write_json_atomic(path, data, mode=0o600, sort_keys=True)
    path.chmod(0o600)


def registered_adapter(registry: dict[str, Any], adapter_id: str) -> dict[str, Any]:
    entry = registry.get("adapters", {}).get(adapter_id)
    if not isinstance(entry, dict):
        raise SystemExit(f"adapter is not registered: {adapter_id}")
    return entry


def adapter_public_entry(adapter_id: str, entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": adapter_id,
        "version": entry.get("version"),
        "capabilities": entry.get("capabilities", []),
        "distribution": entry.get("distribution", "private"),
        "network": entry.get("network", "none"),
        "route_count": len(entry.get("routes", [])),
        "remote_resource_count": len(entry.get("remote_resources", [])),
        "root": entry.get("root"),
        "registered_at": entry.get("registered_at"),
    }
