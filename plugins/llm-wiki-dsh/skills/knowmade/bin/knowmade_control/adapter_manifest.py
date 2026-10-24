"""The adapter manifest schema and the v1 request/response validators.

The ``llm-wiki-adapter/v1`` protocol constants, the manifest and route
validators, the manifest reader, route matching, and the validators for one
request and one response document. Pure validation: nothing here starts a
process or touches the registry. Split from ``adapters``
(emercons/knowmade#105 R6).
"""

from __future__ import annotations

import os
import re
import urllib.parse
from pathlib import Path
from typing import Any

from .common import (
    file_sha256,
    load_json_object,
)
from .roots import normalize_windows_absolute_path


ADAPTER_PROTOCOL = "llm-wiki-adapter/v1"
ADAPTER_MANIFEST_NAME = ".llm-wiki-adapter.json"
ADAPTER_ARTIFACT_CLASSES = {"wiki-safe", "private", "bulk"}
ADAPTER_REMOTE_EFFECTS = {"remote-read", "remote-write"}
ADAPTER_ROUTE_RESOURCE_KINDS = {"url"}

_IDENTIFIER_PATTERN = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"


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


def path_within(path: Path, roots: list[Path]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def _string_array(value: Any, *, allow_empty: bool = False) -> bool:
    """True for an array of non-empty strings (non-empty unless allowed)."""
    return (
        isinstance(value, list)
        and (allow_empty or bool(value))
        and all(isinstance(item, str) and item for item in value)
    )


def _check_unique_list(
    errors: list[str], value: Any, item_ok: Any, invalid: str, duplicate: str
) -> None:
    """Record ``invalid`` for a bad non-empty string array, else any duplicate."""
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item_ok(item) for item in value
    ):
        errors.append(invalid)
    elif len(value) != len(set(value)):
        errors.append(duplicate)


# Route list fields: (key, item test, invalid message, duplicate message). The
# ``{label}`` placeholder is the route label ("route 1").
_ROUTE_LIST_FIELDS = (
    (
        "intents",
        lambda item: re.fullmatch(r"[a-z][a-z0-9-]*", item),
        "{label} intents must be a non-empty lowercase token array",
        "{label} intents must not contain duplicates",
    ),
)
_ROUTE_RESOURCE_LIST_FIELDS = (
    (
        "schemes",
        lambda item: item == item.lower()
        and re.fullmatch(r"[a-z][a-z0-9+.-]*", item),
        "{label} URL schemes must be a non-empty lowercase array",
        "{label} URL schemes must not contain duplicates",
    ),
    (
        "hosts",
        lambda item: item == item.lower()
        and re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", item),
        "{label} URL hosts must be a non-empty exact-host array",
        "{label} URL hosts must not contain duplicates",
    ),
    (
        "path_prefixes",
        lambda item: item.startswith("/") and "?" not in item and "#" not in item,
        "{label} URL path_prefixes must be a non-empty absolute-path array",
        "{label} URL path_prefixes must not contain duplicates",
    ),
)


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
            _IDENTIFIER_PATTERN, route_id
        ):
            errors.append(f"{label} id must be a lowercase hyphenated identifier")
        elif route_id in route_ids:
            errors.append(f"route id {route_id!r} is duplicated")
        else:
            route_ids.add(route_id)
        for key, item_ok, invalid, duplicate in _ROUTE_LIST_FIELDS:
            _check_unique_list(
                errors, route.get(key), item_ok,
                invalid.format(label=label), duplicate.format(label=label),
            )
        resource = route.get("resource")
        if not isinstance(resource, dict):
            errors.append(f"{label} resource must be an object")
        else:
            kind = resource.get("kind")
            if not isinstance(kind, str) or kind not in ADAPTER_ROUTE_RESOURCE_KINDS:
                errors.append(f"{label} resource kind must be url")
            for key, item_ok, invalid, duplicate in _ROUTE_RESOURCE_LIST_FIELDS:
                _check_unique_list(
                    errors, resource.get(key), item_ok,
                    invalid.format(label=label), duplicate.format(label=label),
                )
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


# Top-level manifest fields: (key, default, is-valid test, error message).
_MANIFEST_FIELDS = (
    (
        "id",
        None,
        lambda value: isinstance(value, str)
        and bool(re.fullmatch(_IDENTIFIER_PATTERN, value)),
        "id must be a lowercase hyphenated identifier",
    ),
    (
        "protocol",
        None,
        lambda value: value == ADAPTER_PROTOCOL,
        f"protocol must be {ADAPTER_PROTOCOL!r}",
    ),
    (
        "version",
        None,
        lambda value: isinstance(value, str) and bool(value),
        "version must be a non-empty string",
    ),
    (
        "entrypoint",
        None,
        _string_array,
        "entrypoint must be a non-empty argv array",
    ),
    (
        "writes_wiki",
        None,
        lambda value: value is False,
        "writes_wiki must be false; adapters cannot write into a wiki",
    ),
    (
        "network",
        "none",
        lambda value: isinstance(value, str)
        and value in {"none", "optional", "required"},
        "network must be none, optional, or required",
    ),
    (
        "capabilities",
        None,
        _string_array,
        "capabilities must be a non-empty string array",
    ),
    (
        "output_classes",
        [],
        lambda value: isinstance(value, list)
        and all(
            isinstance(item, str) and item in ADAPTER_ARTIFACT_CLASSES
            for item in value
        ),
        "output_classes must contain only wiki-safe, private, and/or bulk",
    ),
)

# Per-operation fields: (key, default, is-valid test, error message). The
# ``{name}`` placeholder is the operation name. An invalid value is read back
# as the default so the cross-field checks below see an empty list.
_OPERATION_FIELDS = (
    (
        "read_arguments",
        [],
        lambda value: _string_array(value, allow_empty=True),
        "operation {name!r} read_arguments must be a string array",
    ),
    (
        "requires_output_dir",
        False,
        lambda value: isinstance(value, bool),
        "operation {name!r} requires_output_dir must be boolean",
    ),
    (
        "effects",
        [],
        lambda value: isinstance(value, list)
        and all(
            isinstance(item, str) and item in ADAPTER_REMOTE_EFFECTS
            for item in value
        ),
        "operation {name!r} effects must contain only remote-read "
        "and/or remote-write",
    ),
    (
        "remote_resource_arguments",
        [],
        lambda value: _string_array(value, allow_empty=True),
        "operation {name!r} remote_resource_arguments must be a string array",
    ),
)


def validate_adapter_manifest(data: dict[str, Any], path: Path) -> None:
    errors: list[str] = []
    for key, default, is_valid, message in _MANIFEST_FIELDS:
        if not is_valid(data.get(key, default)):
            errors.append(message)
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
            values: dict[str, Any] = {}
            for key, default, is_valid, message in _OPERATION_FIELDS:
                value = operation.get(key, default)
                if is_valid(value):
                    values[key] = value
                else:
                    errors.append(message.format(name=name))
                    values[key] = default
            read_arguments = values["read_arguments"]
            effects = values["effects"]
            remote_arguments = values["remote_resource_arguments"]
            if len(effects) != len(set(effects)):
                errors.append(f"operation {name!r} effects must not contain duplicates")
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


def validate_adapter_request(
    request: dict[str, Any], adapter_id: str, manifest: dict[str, Any],
    entry: dict[str, Any], approved_plan_sha256: str | None,
) -> tuple[str, Path | None, dict[str, Any]]:
    for key, expected, message in (
        ("protocol", ADAPTER_PROTOCOL, f"request protocol must be {ADAPTER_PROTOCOL!r}"),
        ("adapter_id", adapter_id, f"request adapter_id must be {adapter_id!r}"),
    ):
        if request.get(key) != expected:
            raise SystemExit(message)
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
    for key, expected, message in (
        ("protocol", ADAPTER_PROTOCOL, "adapter response has the wrong protocol"),
        ("adapter_id", adapter_id, "adapter response has the wrong adapter_id"),
        ("operation", operation, "adapter response has the wrong operation"),
        (
            "adapter_version",
            manifest.get("version"),
            "adapter response has the wrong adapter_version",
        ),
    ):
        if response.get(key) != expected:
            raise SystemExit(message)
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
    for key, expected, message in (
        (
            "resources",
            governance["remote_resources"],
            "remote receipt resources do not match the approved request",
        ),
        (
            "plan_sha256",
            remote_write["plan_sha256"],
            "remote receipt plan_sha256 does not match the approved request",
        ),
        (
            "idempotency_key",
            remote_write["idempotency_key"],
            "remote receipt idempotency_key does not match the approved request",
        ),
        (
            "before_revision",
            remote_write["expected_revision"],
            "remote receipt before_revision does not match the approved request",
        ),
    ):
        if remote_receipt.get(key) != expected:
            raise SystemExit(message)
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
