"""The ``adapter`` namespace: the machine-local private-adapter registry.

Registration, route matching, the describe handshake, the v1 request/response
execution boundary and the remote-write approval gate. Adapter code and
credentials stay outside the wiki. Moved verbatim from ``legacy_cli``
(emercons/knowmade#67 R1.4, ``KM-CLI-001``); the compatibility module
re-exports every public name.
"""

from __future__ import annotations

import argparse
import json
import ntpath
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any

try:
    from . import fsio as _fsio
    from .common import (
        file_sha256,
        load_json_object,
        write_private_json,
    )
    from .markdown import today_date
    from .roots import (
        expand_leading_tilde,
        home_directory,
        normalize_windows_absolute_path,
    )
except ImportError:  # executed directly as a script, not imported as a package
    import fsio as _fsio
    from common import (
        file_sha256,
        load_json_object,
        write_private_json,
    )
    from markdown import today_date
    from roots import (
        expand_leading_tilde,
        home_directory,
        normalize_windows_absolute_path,
    )


ADAPTER_PROTOCOL = "llm-wiki-adapter/v1"
ADAPTER_REGISTRY_SCHEMA = 1
ADAPTER_MANIFEST_NAME = ".llm-wiki-adapter.json"
ADAPTER_ARTIFACT_CLASSES = {"wiki-safe", "private", "bulk"}
ADAPTER_REMOTE_EFFECTS = {"remote-read", "remote-write"}
ADAPTER_ROUTE_RESOURCE_KINDS = {"url"}


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


def validate_adapter_routes(
    data: dict[str, Any], path: Path, errors: list[str]
) -> None:
    """Validate optional, provider-neutral intent/resource routing metadata."""
    routes = data.get("routes", [])
    if not isinstance(routes, list):
        errors.append("routes must be an array")
        return
    route_ids: set[str] = set()
    for index, route in enumerate(routes):
        label = f"route {index + 1}"
        if not isinstance(route, dict):
            errors.append(f"{label} must be an object")
            continue
        route_id = route.get("id")
        if not isinstance(route_id, str) or not re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", route_id
        ):
            errors.append(f"{label} id must be a lowercase hyphenated identifier")
        elif route_id in route_ids:
            errors.append(f"route id {route_id!r} is duplicated")
        else:
            route_ids.add(route_id)
        intents = route.get("intents")
        if not isinstance(intents, list) or not intents or not all(
            isinstance(item, str) and re.fullmatch(r"[a-z][a-z0-9-]*", item)
            for item in intents
        ):
            errors.append(f"{label} intents must be a non-empty lowercase token array")
        elif len(intents) != len(set(intents)):
            errors.append(f"{label} intents must not contain duplicates")
        resource = route.get("resource")
        if not isinstance(resource, dict):
            errors.append(f"{label} resource must be an object")
        else:
            kind = resource.get("kind")
            if not isinstance(kind, str) or kind not in ADAPTER_ROUTE_RESOURCE_KINDS:
                errors.append(f"{label} resource kind must be url")
            schemes = resource.get("schemes")
            if not isinstance(schemes, list) or not schemes or not all(
                isinstance(item, str)
                and item == item.lower()
                and re.fullmatch(r"[a-z][a-z0-9+.-]*", item)
                for item in schemes
            ):
                errors.append(f"{label} URL schemes must be a non-empty lowercase array")
            elif len(schemes) != len(set(schemes)):
                errors.append(f"{label} URL schemes must not contain duplicates")
            hosts = resource.get("hosts")
            if not isinstance(hosts, list) or not hosts or not all(
                isinstance(item, str)
                and item == item.lower()
                and re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", item)
                for item in hosts
            ):
                errors.append(f"{label} URL hosts must be a non-empty exact-host array")
            elif len(hosts) != len(set(hosts)):
                errors.append(f"{label} URL hosts must not contain duplicates")
            prefixes = resource.get("path_prefixes")
            if not isinstance(prefixes, list) or not prefixes or not all(
                isinstance(item, str)
                and item.startswith("/")
                and "?" not in item
                and "#" not in item
                for item in prefixes
            ):
                errors.append(
                    f"{label} URL path_prefixes must be a non-empty absolute-path array"
                )
            elif len(prefixes) != len(set(prefixes)):
                errors.append(f"{label} URL path_prefixes must not contain duplicates")
        priority = route.get("priority", 0)
        if isinstance(priority, bool) or not isinstance(priority, int) or not 0 <= priority <= 1000:
            errors.append(f"{label} priority must be an integer from 0 to 1000")
        guide = route.get("guide")
        if not isinstance(guide, str) or not guide:
            errors.append(f"{label} guide must be a relative file path")
        else:
            guide_path = Path(guide)
            if guide_path.is_absolute() or ".." in guide_path.parts:
                errors.append(f"{label} guide must stay inside the adapter root")
            else:
                resolved_guide = (path.parent / guide_path).resolve(strict=False)
                if not path_within(resolved_guide, [path.parent.resolve(strict=False)]):
                    errors.append(f"{label} guide must stay inside the adapter root")
                elif not resolved_guide.is_file():
                    errors.append(f"{label} guide does not exist: {guide}")


def validate_adapter_manifest(data: dict[str, Any], path: Path) -> None:
    errors: list[str] = []
    adapter_id = data.get("id")
    if not isinstance(adapter_id, str) or not re.fullmatch(
        r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", adapter_id
    ):
        errors.append("id must be a lowercase hyphenated identifier")
    if data.get("protocol") != ADAPTER_PROTOCOL:
        errors.append(f"protocol must be {ADAPTER_PROTOCOL!r}")
    if not isinstance(data.get("version"), str) or not data.get("version"):
        errors.append("version must be a non-empty string")
    entrypoint = data.get("entrypoint")
    if not isinstance(entrypoint, list) or not entrypoint or not all(
        isinstance(item, str) and item for item in entrypoint
    ):
        errors.append("entrypoint must be a non-empty argv array")
    if data.get("writes_wiki") is not False:
        errors.append("writes_wiki must be false; adapters cannot write into a wiki")
    network = data.get("network", "none")
    if not isinstance(network, str) or network not in {"none", "optional", "required"}:
        errors.append("network must be none, optional, or required")
    capabilities = data.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities or not all(
        isinstance(item, str) and item for item in capabilities
    ):
        errors.append("capabilities must be a non-empty string array")
    operations = data.get("operations")
    if not isinstance(operations, dict) or not operations:
        errors.append("operations must be a non-empty object")
    else:
        for name, operation in operations.items():
            if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9-]*", name):
                errors.append("operation names must be lowercase hyphenated identifiers")
                continue
            if not isinstance(operation, dict):
                errors.append(f"operation {name!r} must be an object")
                continue
            read_arguments = operation.get("read_arguments", [])
            if not isinstance(read_arguments, list) or not all(
                isinstance(item, str) and item for item in read_arguments
            ):
                errors.append(f"operation {name!r} read_arguments must be a string array")
                read_arguments = []
            if not isinstance(operation.get("requires_output_dir", False), bool):
                errors.append(f"operation {name!r} requires_output_dir must be boolean")
            effects = operation.get("effects", [])
            if not isinstance(effects, list) or not all(
                isinstance(item, str) and item in ADAPTER_REMOTE_EFFECTS
                for item in effects
            ):
                errors.append(
                    f"operation {name!r} effects must contain only remote-read "
                    "and/or remote-write"
                )
                effects = []
            elif len(effects) != len(set(effects)):
                errors.append(f"operation {name!r} effects must not contain duplicates")
            remote_arguments = operation.get("remote_resource_arguments", [])
            if not isinstance(remote_arguments, list) or not all(
                isinstance(item, str) and item for item in remote_arguments
            ):
                errors.append(
                    f"operation {name!r} remote_resource_arguments must be a string array"
                )
                remote_arguments = []
            if effects and not remote_arguments:
                errors.append(
                    f"operation {name!r} with remote effects must declare "
                    "remote_resource_arguments"
                )
            approved_plan_argument = operation.get("approved_plan_argument")
            if "remote-write" in effects:
                if operation.get("requires_output_dir") is not True:
                    errors.append(
                        f"operation {name!r} remote-write requires an output directory"
                    )
                if (
                    not isinstance(approved_plan_argument, str)
                    or not approved_plan_argument
                    or approved_plan_argument not in read_arguments
                ):
                    errors.append(
                        f"operation {name!r} remote-write approved_plan_argument must "
                        "name one of its read_arguments"
                    )
            elif approved_plan_argument is not None:
                errors.append(
                    f"operation {name!r} approved_plan_argument is only valid for remote-write"
                )
    output_classes = data.get("output_classes", [])
    if not isinstance(output_classes, list) or not all(
        isinstance(item, str) and item in ADAPTER_ARTIFACT_CLASSES for item in output_classes
    ):
        errors.append(
            "output_classes must contain only wiki-safe, private, and/or bulk"
        )
    validate_adapter_routes(data, path, errors)
    if errors:
        raise SystemExit(
            f"invalid adapter manifest {path}: " + "; ".join(sorted(set(errors)))
        )


def read_adapter_manifest(root: Path) -> tuple[Path, dict[str, Any]]:
    path = root / ADAPTER_MANIFEST_NAME
    if not path.is_file():
        raise SystemExit(f"adapter manifest not found: {path}")
    data = load_json_object(path, "adapter manifest")
    validate_adapter_manifest(data, path)
    return path, data


def adapter_path_text(value: str, platform: str | None = None) -> str:
    """Canonical text for a path a user, a registry or a request wrote.

    Only Windows semantics change anything: backslash and slash are both
    separators, ``/c/...`` (Git Bash) and ``/C:/...`` are the drive paths they
    spell, and ``roots.normalize_windows_absolute_path`` owns that drive-path
    rule, the one the hub registry follows too. On POSIX the text is returned
    untouched, because there a backslash is an ordinary filename character.
    ``platform`` is ``os.name`` by default and exists so POSIX can test the
    Windows rules.
    """
    if (platform or os.name) != "nt":
        return value
    normalized = normalize_windows_absolute_path(value, "nt")
    return normalized if normalized is not None else value.replace("\\", "/")


def adapter_path(value: str) -> Path:
    """A ``Path`` for text from a registry entry or a request (no ``~`` expansion)."""
    return Path(adapter_path_text(str(value)))


def normalized_absolute_path(value: str) -> Path:
    return expand_leading_tilde(adapter_path_text(value)).resolve(strict=False)


def adapter_entrypoint_is_path(value: str, platform: str | None = None) -> bool:
    """True when an entrypoint names a file relative to the adapter root.

    A bare command (``python3``) is looked up on ``PATH`` instead. On Windows a
    drive colon also makes it a path (``C:tool`` is drive-relative, never a
    command name) so that it is anchored and then refused as an escape.
    """
    text = adapter_path_text(value, platform)
    if "/" in text or text.startswith("."):
        return True
    return (platform or os.name) == "nt" and ":" in text


def windows_path_escapes(root: str, candidate: str) -> bool:
    """True when ``candidate`` is not ``root`` or inside it, by Windows rules.

    Both separators and letter case are equivalent; a different drive is an
    escape. Pure string work (``ntpath``), so POSIX can test it.
    """
    root_key = ntpath.normcase(ntpath.normpath(root))
    candidate_key = ntpath.normcase(ntpath.normpath(candidate))
    try:
        return ntpath.commonpath([root_key, candidate_key]) != root_key
    except ValueError:
        return True


def resolve_adapter_command(root: Path, entrypoint: list[str]) -> list[str]:
    command = list(entrypoint)
    executable = Path(adapter_path_text(command[0]))
    if executable.is_absolute():
        command[0] = os.path.abspath(executable)
    elif adapter_entrypoint_is_path(command[0]):
        # Do not dereference the final executable symlink. Python virtual
        # environments rely on being invoked through .venv/bin/python so the
        # interpreter discovers pyvenv.cfg and its installed dependencies.
        anchored = os.path.abspath(root / executable)
        if os.name == "nt":
            # A relative entrypoint must stay inside the adapter directory.
            # Check the spelled path, then the directory that actually holds
            # it, so a junction or symlinked directory cannot lead out either.
            spelled_root = os.path.abspath(root)
            real_root = str(Path(spelled_root).resolve(strict=False))
            real_parent = Path(anchored).parent.resolve(strict=False)
            if windows_path_escapes(spelled_root, anchored) or windows_path_escapes(
                real_root, str(real_parent / Path(anchored).name)
            ):
                raise SystemExit(
                    f"adapter entrypoint escapes the adapter directory: {command[0]}"
                )
        command[0] = anchored
    else:
        resolved = shutil.which(command[0])
        if resolved:
            command[0] = resolved
    return command


def registered_adapter(registry: dict[str, Any], adapter_id: str) -> dict[str, Any]:
    entry = registry.get("adapters", {}).get(adapter_id)
    if not isinstance(entry, dict):
        raise SystemExit(f"adapter is not registered: {adapter_id}")
    return entry


def path_within(path: Path, roots: list[Path]) -> bool:
    return any(path == root or root in path.parents for root in roots)


ADAPTER_ENV_BASE = frozenset({
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
})
# What Windows itself needs to find and start programs and to place per-user
# state. None of it is a credential. Without PATHEXT and COMSPEC an adapter that
# shells out to a bare command name cannot resolve it to the .exe/.cmd that
# answers to it; without APPDATA/LOCALAPPDATA/USERPROFILE those tools lose
# their configuration.
ADAPTER_ENV_WINDOWS = frozenset({
    "ALLUSERSPROFILE",
    "APPDATA",
    "COMSPEC",
    "HOMEDRIVE",
    "HOMEPATH",
    "LOCALAPPDATA",
    "PATHEXT",
    "PROGRAMDATA",
    "SYSTEMDRIVE",
    "USERPROFILE",
    "WINDIR",
})


def adapter_environment(
    entry: dict[str, Any],
    platform: str | None = None,
    source: dict[str, str] | None = None,
) -> dict[str, str]:
    """The environment an adapter is started with: an allowlist, never a copy.

    Registration adds names with ``--env``; everything else (tokens, API keys,
    cloud credentials) is dropped. ``platform`` and ``source`` default to
    ``os.name`` and ``os.environ`` and exist so tests can pin them.
    """
    names = set(ADAPTER_ENV_BASE)
    if (platform or os.name) == "nt":
        names |= ADAPTER_ENV_WINDOWS
    names.update(str(item) for item in entry.get("env_allow", []))
    available = os.environ if source is None else source
    environment = {name: available[name] for name in names if name in available}
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONUTF8"] = "1"
    environment["LLM_WIKI_ADAPTER_ROOT"] = str(entry["root"])
    return environment


def shebang_interpreter(path: Path) -> list[str] | None:
    """Return the interpreter a script declares, when it declares one.

    Windows has no shebang support: CreateProcess refuses any file that is not a
    native executable, so a registered adapter written as a script fails with
    "not a valid Win32 application" even though the platform can run it perfectly
    well through the interpreter the script itself names.
    """
    try:
        with path.open("rb") as handle:
            first = handle.readline(256)
    except OSError:
        return None
    if not first.startswith(b"#!"):
        return None
    parts = first[2:].decode("utf-8", "replace").strip().split()
    if not parts:
        return None
    if parts[0].endswith("/env") and len(parts) > 1:
        parts = parts[1:]
        # ``env -S python3 -u`` / ``env -i python3``: the option belongs to env,
        # not to the interpreter. NAME=value words set env vars; skip those too.
        while parts and (parts[0].startswith("-") or "=" in parts[0]):
            parts = parts[1:]
        if not parts:
            return None
    else:
        parts[0] = Path(parts[0]).name
    resolved = shutil.which(parts[0])
    return [resolved, *parts[1:]] if resolved else None


def run_adapter_process(
    entry: dict[str, Any], arguments: list[str], timeout: int
) -> subprocess.CompletedProcess[str]:
    if timeout <= 0:
        raise SystemExit("adapter timeout must be greater than zero")
    command = [str(item) for item in entry["command"]] + arguments
    if os.name == "nt" and command:
        interpreter = shebang_interpreter(adapter_path(command[0]))
        if interpreter is not None:
            command = interpreter + command
    try:
        return subprocess.run(
            command,
            cwd=str(entry["root"]),
            env=adapter_environment(entry),
            text=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SystemExit(f"adapter timed out after {timeout} seconds") from exc
    except OSError as exc:
        raise SystemExit(f"could not execute registered adapter: {exc}") from exc


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


def adapter_add(args: argparse.Namespace) -> int:
    root = normalized_absolute_path(args.path)
    if not root.is_dir():
        raise SystemExit(f"adapter root is not a directory: {root}")
    manifest_path, manifest = read_adapter_manifest(root)
    adapter_id = str(manifest["id"])
    registry = read_adapter_registry()
    if adapter_id in registry["adapters"] and not args.replace:
        raise SystemExit(
            f"adapter is already registered: {adapter_id}; use --replace to update it"
        )
    read_roots = {root}
    read_roots.update(normalized_absolute_path(value) for value in args.read_root)
    write_roots = {
        normalized_absolute_path(value) for value in args.write_root
    }
    command = resolve_adapter_command(root, list(manifest["entrypoint"]))
    remote_resources = sorted(set(args.remote_resource))
    for resource in remote_resources:
        if not isinstance(resource, str) or not resource or any(
            character.isspace() for character in resource
        ) or ":" not in resource:
            raise SystemExit(
                "remote resources must be exact non-whitespace values with a scheme prefix"
            )
    entry = {
        "root": str(root),
        "command": command,
        "version": manifest["version"],
        "capabilities": manifest["capabilities"],
        "distribution": manifest.get("distribution", "private"),
        "network": manifest.get("network", "none"),
        "routes": manifest.get("routes", []),
        "read_roots": sorted(str(path) for path in read_roots),
        "write_roots": sorted(str(path) for path in write_roots),
        "remote_resources": remote_resources,
        "env_allow": sorted(set(args.env)),
        "manifest_sha256": file_sha256(manifest_path),
        "registered_at": today_date().isoformat(),
    }
    registry["adapters"][adapter_id] = entry
    write_adapter_registry(registry)
    report = adapter_public_entry(adapter_id, entry)
    report.update(
        {
            "status": "registered",
            "registry": str(adapter_registry_path()),
            "read_roots": entry["read_roots"],
            "write_roots": entry["write_roots"],
            "remote_resource_count": len(entry["remote_resources"]),
        }
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"Registered adapter: {adapter_id} {entry['version']}")
        print(f"Registry: {adapter_registry_path()}")
        print("Trust: explicit local registration; no repository was cloned or published.")
    return 0


def adapter_list(args: argparse.Namespace) -> int:
    registry = read_adapter_registry()
    entries = [
        adapter_public_entry(adapter_id, entry)
        for adapter_id, entry in sorted(registry["adapters"].items())
        if isinstance(entry, dict)
    ]
    if args.json:
        print(json.dumps({"adapters": entries}, indent=2, sort_keys=True))
    elif not entries:
        print("No private adapters registered.")
    else:
        for entry in entries:
            capabilities = ",".join(entry["capabilities"])
            print(
                f"{entry['id']}\t{entry['version']}\t{entry['network']}\t"
                f"{capabilities}\t{entry['root']}"
            )
    return 0


def adapter_show(args: argparse.Namespace) -> int:
    registry = read_adapter_registry()
    entry = registered_adapter(registry, args.adapter_id)
    report = dict(entry)
    report["id"] = args.adapter_id
    report["registry"] = str(adapter_registry_path())
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def adapter_route_matches(route: dict[str, Any], intent: str, resource: str) -> bool:
    if intent not in route.get("intents", []):
        return False
    resource_spec = route.get("resource")
    if not isinstance(resource_spec, dict):
        return False
    if resource_spec.get("kind") != "url":
        return False
    try:
        parsed = urllib.parse.urlsplit(resource)
        port = parsed.port
    except ValueError:
        return False
    if not parsed.scheme or not parsed.hostname or parsed.username or parsed.password:
        return False
    if port is not None:
        return False
    path = parsed.path or "/"
    schemes = resource_spec.get("schemes")
    hosts = resource_spec.get("hosts")
    prefixes = resource_spec.get("path_prefixes")
    if not all(isinstance(items, list) for items in (schemes, hosts, prefixes)):
        return False
    return (
        parsed.scheme.lower() in schemes
        and parsed.hostname.lower() in hosts
        and any(isinstance(prefix, str) and path.startswith(prefix) for prefix in prefixes)
    )


def adapter_route(args: argparse.Namespace) -> int:
    """Resolve an effect and resource through registered manifest routes."""
    intent = args.intent.strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9-]*", intent):
        raise SystemExit("route intent must be one lowercase token")
    registry = read_adapter_registry()
    candidates: list[dict[str, Any]] = []
    unavailable: list[dict[str, Any]] = []
    for adapter_id, entry in sorted(registry["adapters"].items()):
        if not isinstance(entry, dict):
            continue
        for route in entry.get("routes", []):
            if not isinstance(route, dict) or not adapter_route_matches(
                route, intent, args.resource
            ):
                continue
            root = adapter_path(str(entry.get("root", ""))).resolve(strict=False)
            manifest_path = root / ADAPTER_MANIFEST_NAME
            if not manifest_path.is_file():
                unavailable.append({
                    "adapter_id": adapter_id,
                    "route_id": route.get("id"),
                    "priority": route.get("priority", 0),
                    "issue": "manifest missing",
                })
                continue
            if file_sha256(manifest_path) != entry.get("manifest_sha256"):
                unavailable.append({
                    "adapter_id": adapter_id,
                    "route_id": route.get("id"),
                    "priority": route.get("priority", 0),
                    "issue": "manifest changed",
                })
                continue
            guide = (root / str(route["guide"])).resolve(strict=False)
            if not guide.is_file() or not path_within(guide, [root]):
                unavailable.append({
                    "adapter_id": adapter_id,
                    "route_id": route.get("id"),
                    "priority": route.get("priority", 0),
                    "issue": "route guide missing",
                })
                continue
            candidates.append(
                {
                    "adapter_id": adapter_id,
                    "route_id": route["id"],
                    "priority": route.get("priority", 0),
                    "guide": str(guide),
                }
            )
    all_matches = candidates + unavailable
    if all_matches:
        highest_priority = max(int(item["priority"]) for item in all_matches)
    else:
        highest_priority = -1
    blocking_unavailable = [
        item for item in unavailable if int(item["priority"]) == highest_priority
    ]
    if blocking_unavailable:
        report = {
            "status": "unavailable",
            "intent": intent,
            "resource_kind": "url",
            "issues": blocking_unavailable,
        }
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print("A matching adapter route is unavailable; run adapter doctor.")
        return 2
    if not candidates:
        report = {
            "status": "no-match",
            "intent": intent,
            "resource_kind": "url",
        }
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print("No registered adapter route matched.")
        return 1
    winners = [
        candidate for candidate in candidates
        if int(candidate["priority"]) == highest_priority
    ]
    if len(winners) != 1:
        report = {
            "status": "ambiguous",
            "intent": intent,
            "resource_kind": "url",
            "matches": winners,
        }
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print("Multiple registered adapter routes matched at the same priority.")
        return 2
    report = {
        "status": "matched",
        "intent": intent,
        "resource_kind": "url",
        **winners[0],
    }
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            f"{report['adapter_id']}\t{report['route_id']}\t{report['guide']}"
        )
    return 0


def adapter_doctor(args: argparse.Namespace) -> int:
    registry = read_adapter_registry()
    entry = registered_adapter(registry, args.adapter_id)
    issues: list[str] = []
    root = adapter_path(str(entry.get("root", ""))).resolve(strict=False)
    manifest_path = root / ADAPTER_MANIFEST_NAME
    manifest: dict[str, Any] | None = None
    if not root.is_dir():
        issues.append("registered root is missing")
    elif not manifest_path.is_file():
        issues.append("adapter manifest is missing")
    else:
        try:
            manifest = load_json_object(manifest_path, "adapter manifest")
            validate_adapter_manifest(manifest, manifest_path)
        except SystemExit as exc:
            issues.append(str(exc))
        if file_sha256(manifest_path) != entry.get("manifest_sha256"):
            issues.append("adapter manifest changed; re-register with --replace")
    command = entry.get("command")
    if not isinstance(command, list) or not command:
        issues.append("registered command is invalid")
    elif not adapter_path(str(command[0])).is_file():
        issues.append("registered executable is missing")
    handshake: dict[str, Any] | None = None
    if not issues:
        result = run_adapter_process(entry, ["describe"], args.timeout)
        if result.returncode != 0:
            issues.append(f"describe handshake failed with exit {result.returncode}")
        else:
            try:
                parsed = json.loads(result.stdout)
                handshake = parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                handshake = None
            if handshake is None:
                issues.append("describe handshake did not return one JSON object")
            else:
                for field in ("protocol", "id", "version", "capabilities"):
                    if manifest is not None and handshake.get(field) != manifest.get(field):
                        issues.append(f"describe handshake disagrees on {field}")
    report = {
        "id": args.adapter_id,
        "status": "healthy" if not issues else "unhealthy",
        "issues": issues,
        "root": str(root),
        "protocol": ADAPTER_PROTOCOL,
        "handshake": handshake,
    }
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"Adapter {args.adapter_id}: {report['status']}")
        for issue in issues:
            print(f"- {issue}")
    return 0 if not issues else 1


def validate_adapter_request(
    request: dict[str, Any], adapter_id: str, manifest: dict[str, Any],
    entry: dict[str, Any], approved_plan_sha256: str | None,
) -> tuple[str, Path | None, dict[str, Any]]:
    if request.get("protocol") != ADAPTER_PROTOCOL:
        raise SystemExit(f"request protocol must be {ADAPTER_PROTOCOL!r}")
    if request.get("adapter_id") != adapter_id:
        raise SystemExit(f"request adapter_id must be {adapter_id!r}")
    operation = request.get("operation")
    operations = manifest["operations"]
    if not isinstance(operation, str) or operation not in operations:
        raise SystemExit(f"request operation is not supported by {adapter_id}")
    arguments = request.get("arguments")
    if not isinstance(arguments, dict):
        raise SystemExit("request arguments must be an object")
    if not isinstance(request.get("options", {}), dict):
        raise SystemExit("request options must be an object")
    read_roots = [adapter_path(value).resolve(strict=False) for value in entry["read_roots"]]
    operation_spec = operations[operation]
    effects = set(operation_spec.get("effects", []))
    for name in operation_spec.get("read_arguments", []):
        raw_path = arguments.get(name)
        if not isinstance(raw_path, str) or not raw_path:
            raise SystemExit(f"request argument {name!r} must be an absolute path")
        path = adapter_path(raw_path)
        if not path.is_absolute():
            raise SystemExit(f"request argument {name!r} must be an absolute path")
        resolved = path.resolve(strict=False)
        if not resolved.exists():
            raise SystemExit(f"request argument {name!r} does not exist")
        if not path_within(resolved, read_roots):
            raise SystemExit(f"request argument {name!r} is outside registered read roots")
    output_dir: Path | None = None
    if operation_spec.get("requires_output_dir", False):
        raw_output = request.get("output_dir")
        if not isinstance(raw_output, str) or not raw_output:
            raise SystemExit("request output_dir must be an absolute path")
        candidate = adapter_path(raw_output)
        if not candidate.is_absolute():
            raise SystemExit("request output_dir must be an absolute path")
        output_dir = candidate.resolve(strict=False)
        write_roots = [adapter_path(value).resolve(strict=False) for value in entry["write_roots"]]
        if not write_roots or not path_within(output_dir, write_roots):
            raise SystemExit("request output_dir is outside registered write roots")
    allowed_remote_resources = set(entry.get("remote_resources", []))
    remote_resources: list[str] = []
    for name in operation_spec.get("remote_resource_arguments", []):
        resource = arguments.get(name)
        if not isinstance(resource, str) or not resource:
            raise SystemExit(f"request argument {name!r} must be a remote resource string")
        if resource not in allowed_remote_resources:
            raise SystemExit(
                f"request argument {name!r} is outside registered remote resources"
            )
        remote_resources.append(resource)
    governance: dict[str, Any] = {
        "effects": sorted(effects),
        "remote_resources": remote_resources,
    }
    remote_write = request.get("remote_write")
    if "remote-write" not in effects:
        if remote_write is not None:
            raise SystemExit("request remote_write is only valid for a remote-write operation")
        if approved_plan_sha256 is not None:
            raise SystemExit(
                "--approve-remote-write is only valid for a remote-write operation"
            )
        return operation, output_dir, governance
    if not isinstance(remote_write, dict):
        raise SystemExit("remote-write request must include a remote_write object")
    plan_sha256 = remote_write.get("plan_sha256")
    if not isinstance(plan_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", plan_sha256):
        raise SystemExit("remote_write plan_sha256 must be 64 lowercase hex characters")
    plan_argument = operation_spec["approved_plan_argument"]
    plan_path = adapter_path(str(arguments[plan_argument])).resolve(strict=False)
    actual_plan_sha256 = file_sha256(plan_path)
    if plan_sha256 != actual_plan_sha256:
        raise SystemExit("remote_write plan_sha256 does not match the approved plan file")
    if approved_plan_sha256 is None:
        raise SystemExit(
            "remote-write operation requires --approve-remote-write with the exact plan sha256"
        )
    if approved_plan_sha256 != plan_sha256:
        raise SystemExit("--approve-remote-write does not match remote_write plan_sha256")
    idempotency_key = remote_write.get("idempotency_key")
    if (
        not isinstance(idempotency_key, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", idempotency_key)
    ):
        raise SystemExit(
            "remote_write idempotency_key must be 8-128 safe identifier characters"
        )
    expected_revision = remote_write.get("expected_revision")
    if not isinstance(expected_revision, str) or not expected_revision:
        raise SystemExit("remote_write expected_revision must be a non-empty string")
    governance["remote_write"] = {
        "plan_sha256": plan_sha256,
        "idempotency_key": idempotency_key,
        "expected_revision": expected_revision,
    }
    return operation, output_dir, governance


def validate_adapter_response(
    response: dict[str, Any], adapter_id: str, operation: str,
    manifest: dict[str, Any], entry: dict[str, Any], output_dir: Path | None,
    governance: dict[str, Any],
) -> None:
    if response.get("protocol") != ADAPTER_PROTOCOL:
        raise SystemExit("adapter response has the wrong protocol")
    if response.get("adapter_id") != adapter_id:
        raise SystemExit("adapter response has the wrong adapter_id")
    if response.get("operation") != operation:
        raise SystemExit("adapter response has the wrong operation")
    if response.get("adapter_version") != manifest.get("version"):
        raise SystemExit("adapter response has the wrong adapter_version")
    if response.get("status") not in {"ok", "error"}:
        raise SystemExit("adapter response status must be ok or error")
    if not isinstance(response.get("summary"), dict):
        raise SystemExit("adapter response summary must be an object")
    if response["status"] == "ok" and not isinstance(response.get("run_id"), str):
        raise SystemExit("successful adapter response run_id must be a string")
    if response["status"] == "error":
        errors = response.get("errors")
        if not isinstance(errors, list) or not errors or not all(
            isinstance(item, str) and item for item in errors
        ):
            raise SystemExit("failed adapter response errors must be a non-empty string array")
    artifacts = response.get("artifacts", [])
    if not isinstance(artifacts, list):
        raise SystemExit("adapter response artifacts must be an array")
    allowed_classes = set(manifest.get("output_classes", []))
    write_roots = [adapter_path(value).resolve(strict=False) for value in entry["write_roots"]]
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise SystemExit("adapter response artifact must be an object")
        raw_path = artifact.get("path")
        artifact_class = artifact.get("class")
        expected_sha = artifact.get("sha256")
        if not isinstance(raw_path, str) or not raw_path:
            raise SystemExit("adapter response artifact path is missing")
        if artifact_class not in allowed_classes:
            raise SystemExit("adapter response artifact has an undeclared class")
        if not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
            raise SystemExit("adapter response artifact sha256 is invalid")
        candidate = adapter_path(raw_path)
        if not candidate.is_absolute():
            if output_dir is None:
                raise SystemExit("relative adapter artifact requires output_dir")
            candidate = output_dir / candidate
        resolved = candidate.resolve(strict=False)
        if not resolved.is_file():
            raise SystemExit("adapter response artifact does not exist as a file")
        if not path_within(resolved, write_roots):
            raise SystemExit("adapter response artifact is outside registered write roots")
        if output_dir is not None and not path_within(resolved, [output_dir]):
            raise SystemExit("adapter response artifact is outside the request output_dir")
        if file_sha256(resolved) != expected_sha:
            raise SystemExit("adapter response artifact sha256 does not match")
    remote_write = governance.get("remote_write")
    remote_receipt = response.get("remote_receipt")
    if remote_write is None:
        if remote_receipt is not None:
            raise SystemExit(
                "adapter response remote_receipt is only valid for a remote-write operation"
            )
        return
    if remote_receipt is None and response["status"] == "error":
        return
    if not isinstance(remote_receipt, dict):
        raise SystemExit("successful remote-write response must include remote_receipt")
    if remote_receipt.get("resources") != governance["remote_resources"]:
        raise SystemExit("remote receipt resources do not match the approved request")
    if remote_receipt.get("plan_sha256") != remote_write["plan_sha256"]:
        raise SystemExit("remote receipt plan_sha256 does not match the approved request")
    if remote_receipt.get("idempotency_key") != remote_write["idempotency_key"]:
        raise SystemExit("remote receipt idempotency_key does not match the approved request")
    if remote_receipt.get("before_revision") != remote_write["expected_revision"]:
        raise SystemExit("remote receipt before_revision does not match the approved request")
    if not isinstance(remote_receipt.get("after_revision"), str) or not remote_receipt.get(
        "after_revision"
    ):
        raise SystemExit("remote receipt after_revision must be a non-empty string")
    verification = remote_receipt.get("verification")
    if not isinstance(verification, dict):
        raise SystemExit("remote receipt verification must be an object")
    if response["status"] == "ok" and (
        remote_receipt.get("status") != "verified"
        or verification.get("status") != "verified"
    ):
        raise SystemExit("successful remote-write response must be verified")


def redacted_remote_write_report(response: dict[str, Any]) -> dict[str, Any]:
    """Return a content- and identifier-free terminal report for remote writes."""
    receipt = response.get("remote_receipt")
    resource_count = len(receipt.get("resources", [])) if isinstance(receipt, dict) else 0
    return {
        "protocol": response.get("protocol"),
        "adapter_id": response.get("adapter_id"),
        "adapter_version": response.get("adapter_version"),
        "operation": response.get("operation"),
        "status": response.get("status"),
        "artifact_count": len(response.get("artifacts", [])),
        "remote_write": {
            "status": receipt.get("status") if isinstance(receipt, dict) else "failed",
            "resource_count": resource_count,
            "verified": bool(
                isinstance(receipt, dict)
                and isinstance(receipt.get("verification"), dict)
                and receipt["verification"].get("status") == "verified"
            ),
        },
    }


def adapter_run(args: argparse.Namespace) -> int:
    registry = read_adapter_registry()
    entry = registered_adapter(registry, args.adapter_id)
    root = adapter_path(entry["root"]).resolve(strict=False)
    manifest_path, manifest = read_adapter_manifest(root)
    if file_sha256(manifest_path) != entry.get("manifest_sha256"):
        raise SystemExit("adapter manifest changed; re-register with --replace")
    request_path = normalized_absolute_path(args.request)
    request = load_json_object(request_path, "adapter request")
    operation, output_dir, governance = validate_adapter_request(
        request, args.adapter_id, manifest, entry, args.approve_remote_write
    )
    remote_write_operation = "remote-write" in governance.get("effects", [])
    if remote_write_operation and not args.response:
        raise SystemExit("remote-write operation requires --response for a private receipt")
    response_destination: Path | None = None
    if args.response:
        response_destination = normalized_absolute_path(args.response)
        if remote_write_operation:
            write_roots = [adapter_path(value).resolve(strict=False) for value in entry["write_roots"]]
            if not write_roots or not path_within(response_destination, write_roots):
                raise SystemExit("remote-write response receipt is outside registered write roots")
    with tempfile.TemporaryDirectory(prefix="llm-wiki-adapter-") as temporary:
        response_path = Path(temporary) / "response.json"
        result = run_adapter_process(
            entry,
            ["execute", "--request", str(request_path), "--response", str(response_path)],
            args.timeout,
        )
        if not response_path.is_file():
            raise SystemExit(
                f"adapter exited {result.returncode} without a response document"
            )
        response = load_json_object(response_path, "adapter response")
    validate_adapter_response(
        response, args.adapter_id, operation, manifest, entry, output_dir, governance
    )
    if response_destination is not None:
        write_private_json(response_destination, response)
    if args.json:
        report = redacted_remote_write_report(response) if remote_write_operation else response
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            f"Adapter {args.adapter_id} {operation}: {response['status']} "
            f"({len(response.get('artifacts', []))} verified artifacts)"
        )
        print("No artifacts were imported into the wiki automatically.")
    if result.returncode != 0 or response["status"] != "ok":
        return 2
    return 0


def adapter_remove(args: argparse.Namespace) -> int:
    registry = read_adapter_registry()
    registered_adapter(registry, args.adapter_id)
    if not args.yes:
        raise SystemExit("adapter remove requires --yes")
    del registry["adapters"][args.adapter_id]
    write_adapter_registry(registry)
    report = {"id": args.adapter_id, "status": "removed"}
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"Removed adapter registration: {args.adapter_id}")
    return 0


def run_adapter(args: argparse.Namespace) -> int:
    handlers = {
        "add": adapter_add,
        "list": adapter_list,
        "show": adapter_show,
        "route": adapter_route,
        "doctor": adapter_doctor,
        "run": adapter_run,
        "remove": adapter_remove,
    }
    return handlers[args.adapter_command](args)


def register_parser(subparsers: Any) -> argparse.ArgumentParser:
    """Register the ``adapter`` namespace on a top-level subparsers action."""
    adapter = subparsers.add_parser(
        "adapter",
        help="Register and run explicitly trusted local private adapters.",
        description=(
            "Manage the machine-local private-adapter registry. Adapter code and "
            "credentials stay outside the wiki; successful runs return verified "
            "artifacts but never import them automatically."
        ),
    )
    adapter_sub = adapter.add_subparsers(dest="adapter_command", required=True)

    adapter_add_parser = adapter_sub.add_parser(
        "add", help="Register an adapter from an existing local checkout."
    )
    adapter_add_parser.add_argument("path", help="Local adapter root containing .llm-wiki-adapter.json.")
    adapter_add_parser.add_argument(
        "--read-root", action="append", default=[], help="Allowed adapter input root; repeatable."
    )
    adapter_add_parser.add_argument(
        "--write-root", action="append", default=[], help="Allowed adapter output root; repeatable."
    )
    adapter_add_parser.add_argument(
        "--env", action="append", default=[], help="Environment variable name to pass without storing its value; repeatable."
    )
    adapter_add_parser.add_argument(
        "--remote-resource", action="append", default=[],
        help="Exact remote resource identifier allowed for this adapter; repeatable."
    )
    adapter_add_parser.add_argument(
        "--replace", action="store_true", help="Replace an existing registration with the same id."
    )
    adapter_add_parser.add_argument("--json", action="store_true", help="Print JSON.")

    adapter_list_parser = adapter_sub.add_parser("list", help="List registered adapters.")
    adapter_list_parser.add_argument("--json", action="store_true", help="Print JSON.")

    adapter_show_parser = adapter_sub.add_parser("show", help="Show one registration.")
    adapter_show_parser.add_argument("adapter_id", help="Registered adapter id.")
    adapter_show_parser.add_argument(
        "--json", action="store_true", help="Print JSON (the default output format)."
    )

    adapter_route_parser = adapter_sub.add_parser(
        "route", help="Match an intent and resource against registered adapter routes."
    )
    adapter_route_parser.add_argument(
        "--intent", required=True, help="Lowercase requested-effect token."
    )
    adapter_route_parser.add_argument(
        "--resource", required=True, help="External resource URL to classify."
    )
    adapter_route_parser.add_argument("--json", action="store_true", help="Print JSON.")

    adapter_doctor_parser = adapter_sub.add_parser(
        "doctor", help="Validate a registration and perform the describe handshake."
    )
    adapter_doctor_parser.add_argument("adapter_id", help="Registered adapter id.")
    adapter_doctor_parser.add_argument(
        "--timeout", type=int, default=30, help="Handshake timeout in seconds."
    )
    adapter_doctor_parser.add_argument("--json", action="store_true", help="Print JSON.")

    adapter_run_parser = adapter_sub.add_parser(
        "run", help="Execute a registered operation from a JSON request."
    )
    adapter_run_parser.add_argument("adapter_id", help="Registered adapter id.")
    adapter_run_parser.add_argument("--request", required=True, help="Absolute path to the v1 request JSON.")
    adapter_run_parser.add_argument(
        "--response", help="Optional path where the validated response receipt is copied."
    )
    adapter_run_parser.add_argument(
        "--timeout", type=int, default=3600, help="Execution timeout in seconds."
    )
    adapter_run_parser.add_argument(
        "--approve-remote-write",
        metavar="PLAN_SHA256",
        help="Explicitly approve a remote-write operation for this exact plan hash."
    )
    adapter_run_parser.add_argument("--json", action="store_true", help="Print JSON.")

    adapter_remove_parser = adapter_sub.add_parser("remove", help="Remove a local registration.")
    adapter_remove_parser.add_argument("adapter_id", help="Registered adapter id.")
    adapter_remove_parser.add_argument("--yes", action="store_true", help="Confirm removal.")
    adapter_remove_parser.add_argument("--json", action="store_true", help="Print JSON.")

    adapter.set_defaults(func=run_adapter)
    return adapter


def run(args: argparse.Namespace) -> int:
    """Dispatch a parsed ``adapter`` namespace."""
    return run_adapter(args)
