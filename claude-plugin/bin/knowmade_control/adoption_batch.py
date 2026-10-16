#!/usr/bin/env python3
"""Deterministic state machine for Knowmade brownfield adoption batches.

The LLM/agent owns semantic work (extracting PDFs, collection ingestion, dataset
registration). This helper owns the durable transaction journal around that work:
claim one manifest row, complete/fail/release it, and verify that every source is
accounted for exactly once.

Durability order for every mutating command: the manifest and ``state.json``
are replaced atomically first, then the journal rows are appended. A journal
line therefore never describes a transition that is not on disk.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import socket
import sys
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

from .fsio import atomic_write_text
from .stdio import configure_utf8_stdio


BATCH_SCHEMA = "knowmade/adoption-batch/v1"
EVENT_SCHEMA = "knowmade/adoption-batch-event/v1"
LOCK_NAME = ".batch.lock"
READY_STATES = {"planned", "staged-copy", "staged-move"}
TERMINAL_STATES = {
    "ingested",
    "registered-dataset",
    "tracked-collection",
    "needs-human",
    "skipped",
}
OUTCOMES_BY_CLASS = {
    "ingest": {"ingested", "needs-human", "skipped"},
    "collection": {"ingested", "tracked-collection", "needs-human", "skipped"},
    "dataset": {"registered-dataset", "needs-human", "skipped"},
    "needs-human": {"needs-human", "skipped"},
    "skip": {"skipped"},
}
OUTCOMES_REQUIRING_DESTINATION = {"ingested", "registered-dataset", "tracked-collection"}

_WINDOWS_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WINDOWS_STILL_ACTIVE = 259
_WINDOWS_ERROR_ACCESS_DENIED = 5


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"expected JSON object: {path}")
    return value


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SystemExit(f"cannot read manifest {path}: {exc}") from exc
    for line_no, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"invalid manifest JSON at line {line_no}: {exc}") from exc
        if not isinstance(row, dict) or not row.get("item_id"):
            raise SystemExit(f"manifest line {line_no} has no item_id")
        item = str(row["item_id"])
        if item in seen:
            raise SystemExit(f"duplicate item_id in manifest: {item}")
        seen.add(item)
        rows.append(row)
    return rows


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    atomic_write_text(path, "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


def resolve_batch(value: str, wiki_root_arg: str | None) -> Path:
    direct = Path(value).expanduser()
    if direct.is_dir():
        return direct.resolve()
    if wiki_root_arg:
        candidate = Path(wiki_root_arg).expanduser().resolve() / "inbox" / ".imports" / value
        if candidate.is_dir():
            return candidate
    raise SystemExit(
        f"batch not found: {value}; pass the batch directory or use --wiki-root with a batch id"
    )


def resolve_wiki_root(batch: Path, state: dict[str, Any], wiki_root_arg: str | None) -> Path:
    """Locate the wiki a batch belongs to without trusting a pinned absolute path.

    Precedence: an existing explicit ``--wiki-root``; the ``wiki_root_relative`` pin
    written next to the manifest; the ``<wiki>/inbox/.imports/<id>`` layout
    the batch lives in; and only then the absolute path recorded at apply
    time. A project that was moved, restored elsewhere, or opened from WSL
    instead of Windows keeps working because its batch moved with it.
    """
    explicit = Path(wiki_root_arg).expanduser().resolve() if wiki_root_arg else None
    if explicit is not None and explicit.is_dir():
        return explicit
    # Derived candidates must look like a wiki root: ``../../..`` from a batch
    # copied somewhere arbitrary resolves to *some* existing directory.
    derived: list[Path] = []
    relative = state.get("wiki_root_relative")
    if isinstance(relative, str) and relative:
        derived.append((batch / relative).resolve())
    parents = batch.parents
    if len(parents) > 2 and batch.parent.name == ".imports" and parents[1].name == "inbox":
        derived.append(parents[2])
    for candidate in derived:
        if (candidate / "config.md").is_file():
            return candidate
    recorded = str(state.get("wiki_root", ""))
    if recorded and Path(recorded).expanduser().resolve().is_dir():
        return Path(recorded).expanduser().resolve()
    hint = f"--wiki-root is not a directory: {explicit}" if explicit is not None else "pass --wiki-root to point at the wiki"
    raise SystemExit(f"batch wiki_root is unavailable: {recorded or batch}; {hint}")


def validate_batch(
    batch: Path, wiki_root_arg: str | None = None
) -> tuple[Path, Path, dict[str, Any], list[dict[str, Any]]]:
    state_path = batch / "state.json"
    manifest_path = batch / "manifest.jsonl"
    if not state_path.is_file() or not manifest_path.is_file():
        raise SystemExit(f"not an adoption batch (state.json/manifest.jsonl missing): {batch}")
    state = load_json(state_path)
    if state.get("schema") != BATCH_SCHEMA:
        raise SystemExit(f"unsupported batch schema: {state.get('schema')!r}")
    wiki_root = resolve_wiki_root(batch, state, wiki_root_arg)
    rows = load_rows(manifest_path)
    return wiki_root, manifest_path, state, rows


def destination_path(wiki_root: Path, destination: str) -> Path:
    raw = Path(destination)
    if raw.is_absolute():
        target = raw.resolve()
    else:
        target = (wiki_root / raw).resolve()
    try:
        target.relative_to(wiki_root)
    except ValueError as exc:
        raise SystemExit(f"destination must be inside wiki_root: {destination}") from exc
    if not target.exists():
        raise SystemExit(f"destination does not exist: {target}")
    return target


def event_payload(event: str, row: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": EVENT_SCHEMA,
        "at": now_iso(),
        "event": event,
    }
    if row is not None:
        payload.update({
            "item_id": row.get("item_id"),
            "source_path": row.get("source_path"),
            "classification": row.get("classification"),
            "state": row.get("state"),
        })
    payload.update(extra)
    return payload


def append_events(batch: Path, events: list[dict[str, Any]]) -> None:
    """Append journal rows; call only after the manifest and state are on disk."""
    if not events:
        return
    with (batch / "events.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
        for payload in events:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")


def append_event(batch: Path, event: str, row: dict[str, Any] | None = None, **extra: Any) -> None:
    append_events(batch, [event_payload(event, row, **extra)])


def state_summary(rows: list[dict[str, Any]], scan_truncated: bool) -> dict[str, Any]:
    states = Counter(str(row.get("state", "planned")) for row in rows)
    terminal = sum(states.get(value, 0) for value in TERMINAL_STATES)
    claimed = states.get("claimed", 0)
    errors = states.get("error", 0)
    pending = len(rows) - terminal - claimed - errors
    if terminal == len(rows):
        status = "incomplete-scan" if scan_truncated else "complete"
    elif errors:
        status = "errors"
    elif claimed:
        status = "in-progress"
    else:
        status = "active"
    return {
        "status": status,
        "rows": len(rows),
        "terminal_rows": terminal,
        "pending_rows": pending,
        "claimed_rows": claimed,
        "error_rows": errors,
        "state_counts": dict(sorted(states.items())),
    }


def update_state(batch: Path, state: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    summary = state_summary(rows, bool(state.get("scan_truncated")))
    state.update(summary)
    state["updated_at"] = now_iso()
    atomic_write_text(batch / "state.json", json.dumps(state, indent=2, sort_keys=True) + "\n")


def commit_transition(
    batch: Path,
    manifest: Path,
    state: dict[str, Any],
    rows: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> None:
    """Persist a mutation: manifest, then state, then the journal."""
    write_rows(manifest, rows)
    update_state(batch, state, rows)
    append_events(batch, events)


# --- lock inspection -------------------------------------------------------


def pid_alive(pid: int) -> bool | None:
    """Whether ``pid`` is running on this host; ``None`` when it cannot be told."""
    if pid <= 0:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) terminates the target on Windows; query it instead.
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        except (ImportError, AttributeError):
            return None
        handle = kernel32.OpenProcess(_WINDOWS_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return kernel32.GetLastError() == _WINDOWS_ERROR_ACCESS_DENIED
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return None
            return code.value == _WINDOWS_STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def worker_status(host: Any, pid: Any) -> str:
    """``active``/``stale`` for a host:pid pair this machine can inspect, else ``unknown``."""
    if isinstance(pid, bool) or not isinstance(pid, int) or not isinstance(host, str) or not host:
        return "unknown"
    if host != socket.gethostname():
        return "unknown"
    alive = pid_alive(pid)
    if alive is None:
        return "unknown"
    return "active" if alive else "stale"


def parse_worker(worker: Any) -> tuple[str | None, int | None]:
    """Split the default ``host:pid`` worker identity; other labels yield ``(None, None)``."""
    if not isinstance(worker, str):
        return None, None
    host, sep, pid = worker.rpartition(":")
    if not sep or not host or not pid.isdigit():
        return None, None
    return host, int(pid)


def read_lock(lock: Path) -> dict[str, Any] | None:
    """The lock payload, ``{"raw": ...}`` for a non-JSON lock, ``None`` when absent."""
    try:
        text = lock.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text.strip()}
    return value if isinstance(value, dict) else {"raw": text.strip()}


def lock_holder_status(payload: dict[str, Any] | None) -> str:
    if not payload:
        return "unknown"
    return worker_status(payload.get("host"), payload.get("pid"))


def describe_lock(lock: Path, payload: dict[str, Any] | None, status: str) -> str:
    holder = ""
    if payload and payload.get("pid") is not None and payload.get("host"):
        holder = f"pid {payload.get('pid')} on {payload.get('host')}"
        if payload.get("created_at"):
            holder += f", created {payload.get('created_at')}"
    if status == "active":
        return (
            f"batch is locked: {lock} is held by a running worker ({holder})"
            "; wait for it to finish or stop it before retrying"
        )
    if status == "stale":
        return (
            f"batch is locked: {lock} is stale ({holder}; that process is no longer running)"
            "; re-run with --force-unlock to reclaim it"
        )
    detail = json.dumps(payload, sort_keys=True) if payload else ""
    return (
        f"batch is locked: {lock}" + (f" ({detail})" if detail else "")
        + "; use --force-unlock only after confirming no worker is mutating it"
    )


def reclaim_lock(lock: Path) -> bool:
    """Remove a stale lock atomically: rename it away, then delete the renamed file.

    Only one of several concurrent reclaimers can win the rename, so the loser
    cannot delete a lock the winner has already re-created.
    """
    reclaimed = lock.with_name(f"{lock.name}.reclaimed-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    try:
        os.replace(lock, reclaimed)
    except FileNotFoundError:
        return False
    try:
        reclaimed.unlink()
    except OSError:
        pass
    return True


def acquire_lock(lock: Path, force_unlock: bool = False) -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        return os.open(lock, flags, 0o600)
    except FileExistsError:
        pass
    payload = read_lock(lock)
    if payload is not None:
        status = lock_holder_status(payload)
        if status == "active":
            message = describe_lock(lock, payload, status)
            if force_unlock:
                message += "; --force-unlock refuses to unlock a live worker"
            raise SystemExit(message)
        if not force_unlock:
            raise SystemExit(describe_lock(lock, payload, status))
        reclaim_lock(lock)
    try:
        return os.open(lock, flags, 0o600)
    except FileExistsError:
        pass
    payload = read_lock(lock)
    message = describe_lock(lock, payload, lock_holder_status(payload))
    if force_unlock:
        message += "; another operator re-acquired it while --force-unlock was reclaiming the stale lock"
    raise SystemExit(message)


@contextlib.contextmanager
def batch_lock(batch: Path, force_unlock: bool = False) -> Iterator[None]:
    lock = batch / LOCK_NAME
    payload = {
        "created_at": now_iso(),
        "pid": os.getpid(),
        "host": socket.gethostname(),
    }
    fd = acquire_lock(lock, force_unlock)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
        yield
    finally:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


# --- commands --------------------------------------------------------------


def find_row(rows: list[dict[str, Any]], item_id: str) -> dict[str, Any]:
    for row in rows:
        if str(row.get("item_id")) == item_id:
            return row
    raise SystemExit(f"item_id not found: {item_id}")


def clean_claim_fields(row: dict[str, Any]) -> None:
    for key in ["claim_token", "claimed_at", "claimed_by", "resume_state"]:
        row.pop(key, None)


def claim_report(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Open claims with whether their worker can still be observed running."""
    report: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("state", "planned")) != "claimed":
            continue
        host, pid = parse_worker(row.get("claimed_by"))
        report.append({
            "item_id": row.get("item_id"),
            "classification": row.get("classification"),
            "source_path": row.get("source_path"),
            "claimed_by": row.get("claimed_by"),
            "claimed_at": row.get("claimed_at"),
            "attempts": row.get("attempts"),
            "worker_status": worker_status(host, pid),
        })
    return report


def cmd_status(args: argparse.Namespace) -> int:
    batch = resolve_batch(args.batch, args.wiki_root)
    _wiki_root, _manifest, state, rows = validate_batch(batch, args.wiki_root)
    payload = {"batch": str(batch), **state_summary(rows, bool(state.get("scan_truncated")))}
    claims = claim_report(rows)
    if args.json:
        payload["claims"] = claims
        payload["items"] = rows if args.items else None
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Batch: {batch}")
        print(f"Status: {payload['status']}")
        print(
            f"Rows: {payload['rows']} | terminal {payload['terminal_rows']} | "
            f"pending {payload['pending_rows']} | claimed {payload['claimed_rows']} | errors {payload['error_rows']}"
        )
        for key, value in payload["state_counts"].items():
            print(f"  {key:20} {value}")
        for claim in claims:
            print(
                f"  claimed {claim['item_id']} by {claim['claimed_by']} "
                f"since {claim['claimed_at']} (worker {claim['worker_status']})"
            )
    return 0


def cmd_claim(args: argparse.Namespace) -> int:
    batch = resolve_batch(args.batch, args.wiki_root)
    with batch_lock(batch, args.force_unlock):
        _wiki_root, manifest, state, rows = validate_batch(batch, args.wiki_root)
        eligible = set(READY_STATES)
        if args.retry_errors:
            eligible.add("error")
        selected: list[dict[str, Any]] = []
        for row in rows:
            if str(row.get("state", "planned")) not in eligible:
                continue
            if args.classification and row.get("classification") != args.classification:
                continue
            selected.append(row)
            if len(selected) >= args.limit:
                break
        events: list[dict[str, Any]] = []
        for row in selected:
            prior = str(row.get("state", "planned"))
            row["resume_state"] = "planned" if prior == "error" else prior
            row["state"] = "claimed"
            row["claim_token"] = uuid.uuid4().hex
            row["claimed_at"] = now_iso()
            row["claimed_by"] = args.worker
            row["attempts"] = int(row.get("attempts", 0)) + 1
            row.pop("error", None)
            events.append(event_payload("claim", row, worker=args.worker, prior_state=prior))
        commit_transition(batch, manifest, state, rows, events)
    if args.json:
        print(json.dumps({"batch": str(batch), "claimed": selected}, indent=2, sort_keys=True))
    else:
        if not selected:
            print("No eligible rows.")
        for row in selected:
            print(f"{row['item_id']}  {row.get('classification')}  {row.get('source_path')}")
    return 0


def cmd_complete(args: argparse.Namespace) -> int:
    batch = resolve_batch(args.batch, args.wiki_root)
    with batch_lock(batch, args.force_unlock):
        wiki_root, manifest, state, rows = validate_batch(batch, args.wiki_root)
        row = find_row(rows, args.item_id)
        prior = str(row.get("state", "planned"))
        forced = prior != "claimed"
        if forced and not args.force:
            raise SystemExit(f"item is not claimed: {args.item_id} (state={row.get('state')})")
        classification = str(row.get("classification"))
        allowed = OUTCOMES_BY_CLASS.get(classification, {"needs-human", "skipped"})
        if args.outcome not in allowed:
            raise SystemExit(
                f"outcome {args.outcome!r} is invalid for classification {classification!r}; "
                f"allowed: {', '.join(sorted(allowed))}"
            )
        result_path: str | None = None
        if args.outcome in OUTCOMES_REQUIRING_DESTINATION:
            if not args.destination:
                raise SystemExit(f"--destination is required for outcome {args.outcome}")
            target = destination_path(wiki_root, args.destination)
            result_path = target.relative_to(wiki_root).as_posix()
        row["state"] = args.outcome
        row["completed_at"] = now_iso()
        row["result_path"] = result_path
        if args.note:
            row["result_note"] = args.note
        if forced:
            # A forced outcome bypasses claim; count it as an attempt and drop
            # the retryable error so the terminal row is not contradicted.
            row["attempts"] = int(row.get("attempts", 0)) + 1
        row.pop("error", None)
        clean_claim_fields(row)
        events = [event_payload(
            "complete", row, outcome=args.outcome, result_path=result_path,
            prior_state=prior, forced=forced,
        )]
        commit_transition(batch, manifest, state, rows, events)
    print(f"Completed {args.item_id}: {args.outcome}" + (f" -> {result_path}" if result_path else ""))
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    batch = resolve_batch(args.batch, args.wiki_root)
    with batch_lock(batch, args.force_unlock):
        _wiki_root, manifest, state, rows = validate_batch(batch, args.wiki_root)
        row = find_row(rows, args.item_id)
        if row.get("state") != "claimed":
            raise SystemExit(f"item is not claimed: {args.item_id}")
        next_state = str(row.get("resume_state") or "planned")
        row["state"] = next_state
        clean_claim_fields(row)
        events = [event_payload("release", row, released_to=next_state, note=args.note)]
        commit_transition(batch, manifest, state, rows, events)
    print(f"Released {args.item_id} -> {next_state}")
    return 0


def cmd_fail(args: argparse.Namespace) -> int:
    batch = resolve_batch(args.batch, args.wiki_root)
    with batch_lock(batch, args.force_unlock):
        _wiki_root, manifest, state, rows = validate_batch(batch, args.wiki_root)
        row = find_row(rows, args.item_id)
        prior = str(row.get("state", "planned"))
        forced = prior != "claimed"
        if forced and not args.force:
            raise SystemExit(f"item is not claimed: {args.item_id}")
        row["state"] = "error"
        row["error"] = args.error
        row["failed_at"] = now_iso()
        clean_claim_fields(row)
        events = [event_payload("fail", row, error=args.error, prior_state=prior, forced=forced)]
        commit_transition(batch, manifest, state, rows, events)
    print(f"Failed {args.item_id}: {args.error}")
    return 0


def cmd_sweep(args: argparse.Namespace) -> int:
    batch = resolve_batch(args.batch, args.wiki_root)
    _wiki_root, _manifest, _state, preview_rows = validate_batch(batch, args.wiki_root)
    candidates = [
        row for row in preview_rows
        if str(row.get("state", "planned")) in READY_STATES
        and row.get("classification") in {"skip", "needs-human"}
    ]
    if not args.apply:
        print(f"Would terminalize {len(candidates)} rows ({sum(r.get('classification') == 'skip' for r in candidates)} skip, {sum(r.get('classification') == 'needs-human' for r in candidates)} needs-human).")
        print("No files changed. Re-run with --apply.")
        return 0
    with batch_lock(batch, args.force_unlock):
        _wiki_root, manifest, state, rows = validate_batch(batch, args.wiki_root)
        changed = 0
        events: list[dict[str, Any]] = []
        for row in rows:
            if str(row.get("state", "planned")) not in READY_STATES:
                continue
            classification = row.get("classification")
            if classification == "skip":
                row["state"] = "skipped"
            elif classification == "needs-human":
                row["state"] = "needs-human"
            else:
                continue
            row["completed_at"] = now_iso()
            events.append(event_payload("sweep", row, outcome=row["state"]))
            changed += 1
        commit_transition(batch, manifest, state, rows, events)
    print(f"Terminalized {changed} rows.")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    batch = resolve_batch(args.batch, args.wiki_root)
    wiki_root, _manifest, state, rows = validate_batch(batch, args.wiki_root)
    problems: list[str] = []
    seen: set[str] = set()
    staging = state.get("staging")
    staging_open = staging in {"pending", "interrupted"}
    if staging_open:
        moved = sum(1 for row in rows if row.get("state") == "staged-move")
        pending = sum(
            1 for row in rows
            if row.get("state") == "planned" and row.get("kind") == "file" and row.get("classification") == "ingest"
        )
        detail = f": {state['stage_error']}" if state.get("stage_error") else ""
        problems.append(
            f"staging {staging}: {moved} file(s) moved, {pending} still planned (not moved){detail}"
        )
    for row in rows:
        item = str(row.get("item_id"))
        if item in seen:
            problems.append(f"duplicate item_id: {item}")
        seen.add(item)
        row_state = str(row.get("state", "planned"))
        if row_state not in TERMINAL_STATES:
            note = ""
            if staging_open and row.get("classification") == "ingest":
                # knowmade#36: tell "planned but not moved" from "moved".
                if row_state == "planned":
                    note = " [not moved: staging did not reach this file]"
                elif row_state == "staged-move":
                    note = " [moved to staged/]"
            problems.append(f"nonterminal {item}: {row_state} ({row.get('source_path')}){note}")
        if row_state in OUTCOMES_REQUIRING_DESTINATION:
            result_path = row.get("result_path")
            if not result_path:
                problems.append(f"terminal row missing result_path: {item}")
            else:
                target = (wiki_root / str(result_path)).resolve()
                try:
                    target.relative_to(wiki_root)
                except ValueError:
                    problems.append(f"result_path escapes wiki root: {item}: {result_path}")
                else:
                    if not target.exists():
                        problems.append(f"result_path missing: {item}: {result_path}")
    if state.get("scan_truncated"):
        problems.append("scan_truncated=true; batch cannot prove full project coverage")
    payload = {"batch": str(batch), "ok": not problems, "problems": problems, **state_summary(rows, bool(state.get("scan_truncated")))}
    if staging is not None:
        payload["staging"] = staging
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif problems:
        print("Verification: FAIL")
        for problem in problems[:50]:
            print(f"  - {problem}")
    else:
        print("Verification: PASS")
        print(f"All {len(rows)} manifest rows are terminal and durable destinations resolve.")
    return 0 if not problems else 1


COMMANDS = {
    "status": cmd_status,
    "claim": cmd_claim,
    "complete": cmd_complete,
    "release": cmd_release,
    "fail": cmd_fail,
    "sweep": cmd_sweep,
    "verify": cmd_verify,
}


def add_common(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("batch", help="Batch directory, or batch id with --wiki-root.")
    subparser.add_argument(
        "--wiki-root",
        help="Wiki root when batch is supplied by id; also overrides the root recorded in state.json.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llm-wiki-adopt-batch",
        description="Claim, resume, complete, and verify Knowmade brownfield adoption manifest rows.",
    )
    subs = parser.add_subparsers(dest="command", required=True)

    status = subs.add_parser("status")
    add_common(status)
    status.add_argument("--json", action="store_true")
    status.add_argument("--items", action="store_true", help="Include manifest rows with --json.")
    status.set_defaults(func=cmd_status)

    claim = subs.add_parser("claim")
    add_common(claim)
    claim.add_argument("--classification", choices=sorted(OUTCOMES_BY_CLASS))
    claim.add_argument("--limit", type=int, default=1)
    claim.add_argument("--worker", default=f"{socket.gethostname()}:{os.getpid()}")
    claim.add_argument("--retry-errors", action="store_true")
    claim.add_argument("--force-unlock", action="store_true")
    claim.add_argument("--json", action="store_true")
    claim.set_defaults(func=cmd_claim)

    complete = subs.add_parser("complete")
    add_common(complete)
    complete.add_argument("--item-id", required=True)
    complete.add_argument("--outcome", required=True, choices=sorted(TERMINAL_STATES))
    complete.add_argument("--destination", help="Existing path inside wiki_root for durable outcomes.")
    complete.add_argument("--note")
    complete.add_argument("--force", action="store_true")
    complete.add_argument("--force-unlock", action="store_true")
    complete.set_defaults(func=cmd_complete)

    release = subs.add_parser("release")
    add_common(release)
    release.add_argument("--item-id", required=True)
    release.add_argument("--note")
    release.add_argument("--force-unlock", action="store_true")
    release.set_defaults(func=cmd_release)

    fail = subs.add_parser("fail")
    add_common(fail)
    fail.add_argument("--item-id", required=True)
    fail.add_argument("--error", required=True)
    fail.add_argument("--force", action="store_true")
    fail.add_argument("--force-unlock", action="store_true")
    fail.set_defaults(func=cmd_fail)

    sweep = subs.add_parser("sweep")
    add_common(sweep)
    sweep.add_argument("--apply", action="store_true")
    sweep.add_argument("--force-unlock", action="store_true")
    sweep.set_defaults(func=cmd_sweep)

    verify = subs.add_parser("verify")
    add_common(verify)
    verify.add_argument("--json", action="store_true")
    verify.set_defaults(func=cmd_verify)
    return parser


def run(args: argparse.Namespace, command: str | None = None) -> int:
    """Dispatch one batch command from a parsed namespace.

    ``command`` names the subcommand when the caller's parser stores it under
    another destination (``llm-wiki adopt`` uses ``adopt_command``).
    """
    if getattr(args, "limit", 1) < 1:
        raise SystemExit("--limit must be >= 1")
    return int(COMMANDS[command or args.command](args))


def main() -> int:
    configure_utf8_stdio()
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
