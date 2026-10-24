"""The ``adapter`` namespace: the machine-local private-adapter registry.

Registration, route matching, the describe handshake, the v1 request/response
execution boundary and the remote-write approval gate. Adapter code and
credentials stay outside the wiki. Moved verbatim from ``legacy_cli``
(emercons/knowmade#67 R1.4, ``KM-CLI-001``). The manifest and the
request/response validators live in ``adapter_manifest``, the registry file in
``adapter_registry`` (emercons/knowmade#105 R6); this module keeps path and
command resolution, process execution and the namespace handlers.
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
from pathlib import Path
from typing import Any

from .common import (
    file_sha256,
    load_json_object,
    write_private_json,
)
from .markdown import today_date
from .roots import expand_leading_tilde

# These names moved to adapter_manifest / adapter_registry; re-imported so
# ``adapters.X`` keeps working.
from .adapter_manifest import (  # noqa: F401
    ADAPTER_ARTIFACT_CLASSES,
    ADAPTER_MANIFEST_NAME,
    ADAPTER_PROTOCOL,
    ADAPTER_REMOTE_EFFECTS,
    ADAPTER_ROUTE_RESOURCE_KINDS,
    adapter_path,
    adapter_path_text,
    adapter_route_matches,
    path_within,
    read_adapter_manifest,
    validate_adapter_manifest,
    validate_adapter_request,
    validate_adapter_response,
    validate_adapter_routes,
)
from .adapter_registry import (  # noqa: F401
    ADAPTER_REGISTRY_SCHEMA,
    adapter_config_dir,
    adapter_public_entry,
    adapter_registry_path,
    empty_adapter_registry,
    read_adapter_registry,
    registered_adapter,
    write_adapter_registry,
)


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
