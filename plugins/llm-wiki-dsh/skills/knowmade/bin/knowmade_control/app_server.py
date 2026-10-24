"""Codex app-server JSON-RPC client shared by the live harnesses.

The one client for the benchmark and the adoption eval; the divergences between
their former copies are recorded in ``docs/history/app-server-changelog.md``.
"""

from __future__ import annotations

import json
import os
import queue
import shlex
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

USAGE_KEYS = (
    ("total_tokens", "totalTokens"),
    ("input_tokens", "inputTokens"),
    ("cached_input_tokens", "cachedInputTokens"),
    ("output_tokens", "outputTokens"),
    ("reasoning_output_tokens", "reasoningOutputTokens"),
)

ToolHandler = Callable[[dict[str, Any]], tuple[dict[str, Any], dict[str, Any]]]
ItemHandler = Callable[[dict[str, Any]], dict[str, Any] | None]


class AppServerError(RuntimeError):
    pass


@dataclass
class Event:
    received_at: float
    payload: dict[str, Any]


def resolve_command(command: str) -> list[str]:
    """Split a runtime command and resolve its executable through PATHEXT.

    Windows ``CreateProcess`` does not apply ``PATHEXT``, so a bare ``codex``
    or ``claude`` raises ``FileNotFoundError`` even though the launcher is on
    ``PATH`` as ``codex.cmd`` / ``claude.cmd``. ``shutil.which`` applies it.
    The original word is kept when it does not resolve, so the resulting
    error still names what was asked for.
    """
    argv = shlex.split(command)
    if not argv:
        raise AppServerError("empty app-server command")
    resolved = shutil.which(argv[0])
    if resolved:
        argv[0] = resolved
    return argv


def cli_version(
    command: str,
    names: Iterable[str],
    fallback: str,
    *,
    full_command: bool = False,
) -> str:
    """Return ``<tool> --version`` output, ``fallback`` for a stand-in, else "unknown".

    ``names`` are the basenames (with or without extension) that identify the
    real tool anywhere in the command, so ``codex.cmd`` and ``node .../cli.js``
    both count. The lookup goes through :func:`resolve_command` and never
    raises: it is evaluated before a paid run and must not be the reason a
    report is lost afterwards.
    """
    try:
        argv = resolve_command(command)
    except (AppServerError, ValueError):
        return "unknown"
    wanted = set(names)
    is_real = any(
        Path(part).name in wanted or Path(part).stem in wanted for part in argv
    )
    if not is_real:
        return fallback
    probe = [*argv, "--version"] if full_command else [argv[0], "--version"]
    try:
        proc = subprocess.run(
            probe,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else "unknown"


def normalize_usage(raw: dict[str, Any] | None) -> dict[str, int]:
    """Map an app-server usage block onto the benchmark's snake_case keys."""
    source = raw or {}
    usage = {key: int(source.get(wire, 0) or 0) for key, wire in USAGE_KEYS}
    usage["uncached_input_tokens"] = max(
        0, usage["input_tokens"] - usage["cached_input_tokens"]
    )
    return usage


def usage_delta(before: dict[str, Any] | None, after: dict[str, Any]) -> dict[str, int]:
    """Normalised ``after - before`` of two cumulative ``total`` blocks."""
    base = normalize_usage(before)
    end = normalize_usage(after)
    delta = {
        key: max(0, end[key] - base[key]) for key, _wire in USAGE_KEYS
    }
    delta["uncached_input_tokens"] = max(
        0, delta["input_tokens"] - delta["cached_input_tokens"]
    )
    return delta


def sum_usage(blocks: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Normalised sum of per-request ``last`` blocks."""
    total = normalize_usage(None)
    for block in blocks:
        row = normalize_usage(block)
        for key, _wire in USAGE_KEYS:
            total[key] += row[key]
    total["uncached_input_tokens"] = max(
        0, total["input_tokens"] - total["cached_input_tokens"]
    )
    return total


def parse_tool_arguments(params: dict[str, Any]) -> dict[str, Any] | None:
    """Decode ``item/tool/call`` arguments; ``None`` when they are not an object.

    The model writes these, so a malformed JSON string is a model failure to
    grade, not a harness crash.
    """
    arguments = params.get("arguments", {})
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return None
    return arguments if isinstance(arguments, dict) else None


def tool_text_result(success: bool, text: str) -> dict[str, Any]:
    return {"success": success, "contentItems": [{"type": "inputText", "text": text}]}


def isolated_codex_env(temp_root: Path) -> dict[str, str]:
    """Environment for a Codex child with a throwaway HOME and CODEX_HOME.

    Only ``auth.json`` is copied from the operator's real Codex home, when
    file-based auth exists, so local config, MCP servers and history never
    become hidden variables in a run.
    """
    home = temp_root / "home"
    codex_home = temp_root / "codex-home"
    home.mkdir(parents=True, exist_ok=True)
    codex_home.mkdir(parents=True, exist_ok=True)
    original = Path(
        os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))
    ).expanduser()
    source_auth = original / "auth.json"
    if source_auth.is_file():
        shutil.copy2(source_auth, codex_home / "auth.json")
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["CODEX_HOME"] = str(codex_home)
    return env


class AppServer:
    """One ``codex app-server --stdio`` child spoken to over JSON-RPC."""

    def __init__(self, command: str, env: dict[str, str], timeout: float):
        self.command = command
        self.timeout = timeout
        # Latest cumulative ``tokenUsage.total`` seen per thread id; the
        # snapshot :func:`run_turn` takes before ``turn/start``.
        self.token_totals: dict[str, dict[str, Any]] = {}
        self._next_id = 1
        self._events: queue.Queue[Event | BaseException] = queue.Queue()
        self._pending: list[Event] = []
        self._stderr: list[str] = []
        self.process = subprocess.Popen(
            resolve_command(command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=env,
        )
        assert self.process.stdout is not None
        assert self.process.stderr is not None
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        try:
            for line in self.process.stdout:
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    self._events.put(
                        AppServerError(f"non-JSON app-server output: {line.rstrip()}")
                    )
                    return
                self._events.put(Event(time.monotonic(), payload))
        except BaseException as exc:  # surfaced on the main thread
            self._events.put(exc)
        finally:
            self._events.put(AppServerError("app-server stdout closed"))

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        for line in self.process.stderr:
            self._stderr.append(line.rstrip())
            if len(self._stderr) > 100:
                del self._stderr[:20]

    def _send(self, payload: dict[str, Any]) -> None:
        if self.process.poll() is not None:
            raise AppServerError(self._failure("app-server exited before request"))
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def respond(self, request_id: Any, result: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def request(self, method: str, params: dict[str, Any]) -> tuple[dict[str, Any], float]:
        """Send a request and return ``(result, received_at)``.

        Every other event that arrives first is buffered for
        :meth:`next_event`, server-initiated requests included.
        """
        request_id = self._next_id
        self._next_id += 1
        self._send(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        )
        deadline = time.monotonic() + self.timeout
        while True:
            event = self._get(deadline)
            payload = event.payload
            if payload.get("id") == request_id and "method" not in payload:
                if "error" in payload:
                    raise AppServerError(f"{method} failed: {payload['error']}")
                return payload.get("result", {}), event.received_at
            self._pending.append(event)

    def next_event(self, deadline: float) -> Event:
        if self._pending:
            return self._pending.pop(0)
        return self._get(deadline)

    def _get(self, deadline: float) -> Event:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AppServerError(self._failure("timed out waiting for app-server"))
        try:
            item = self._events.get(timeout=remaining)
        except queue.Empty as exc:
            raise AppServerError(self._failure("timed out waiting for app-server")) from exc
        if isinstance(item, BaseException):
            raise AppServerError(self._failure(str(item))) from item
        return item

    def _failure(self, message: str) -> str:
        stderr = "\n".join(self._stderr[-20:])
        return f"{message}\napp-server stderr:\n{stderr}" if stderr else message

    def close(self) -> None:
        if self.process.poll() is not None:
            return
        if self.process.stdin:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)


def initialize(
    server: AppServer, name: str, title: str, version: str = "1.0.0"
) -> None:
    server.request(
        "initialize",
        {
            "clientInfo": {"name": name, "title": title, "version": version},
            "capabilities": {"experimentalApi": True},
        },
    )
    server.notify("initialized")


def start_thread(
    server: AppServer, params: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    """``thread/start``; returns ``(thread_id, result)``."""
    result, _ = server.request("thread/start", params)
    thread_id = result.get("thread", {}).get("id")
    if not thread_id:
        raise AppServerError("thread/start response did not include a thread id")
    return thread_id, result


@dataclass
class TurnResult:
    turn_id: str
    status: str
    error: Any
    output: str
    started: float
    response_at: float
    first_output_at: float | None
    completed_at: float
    token_usage: dict[str, int]
    token_accounting: str
    usage_updates: int
    compactions: int
    tool_activity: list[dict[str, Any]] = field(default_factory=list)

    @property
    def latency_ms(self) -> float:
        return round((self.completed_at - self.started) * 1000, 1)

    @property
    def ttft_ms(self) -> float | None:
        if self.first_output_at is None:
            return None
        return round((self.first_output_at - self.started) * 1000, 1)


def run_turn(
    server: AppServer,
    thread_id: str,
    input_items: list[dict[str, Any]],
    tool_handler: ToolHandler,
    on_item: ItemHandler | None = None,
) -> TurnResult:
    """Drive one ``turn/start`` to ``turn/completed``.

    ``tool_handler`` answers every ``item/tool/call`` with
    ``(tool_result, activity_row)``; ``on_item`` may turn a completed
    non-message item into an activity row. Both kinds of rows land in
    ``tool_activity`` in event order.

    Token accounting (emercons/knowmade#46). ``thread/tokenUsage/updated`` is
    emitted once per model request and carries two blocks: ``last`` (that one
    request) and ``total`` (cumulative for the thread). A turn that calls a tool
    makes at least two model requests, so recording ``last`` at ``turn/completed``
    described only the request after the final tool result and dropped the one
    where the skill text was actually uncached. :func:`run_turn` snapshots the
    thread's ``total`` before ``turn/start`` and records the delta at
    ``turn/completed``; ``token_accounting`` in the result says which rule
    produced the numbers (``total_delta``; ``last_sum`` when the server reported
    no usable ``total``; ``none`` when it reported nothing). Reports recorded
    with the old ``last`` rule are not comparable with ones recorded under this
    rule; see ``benchmarks/README.md``.
    """
    snapshot = dict(server.token_totals.get(thread_id) or {})
    started = time.monotonic()
    result, response_at = server.request(
        "turn/start", {"threadId": thread_id, "input": input_items}
    )
    turn_id = result.get("turn", {}).get("id")
    if not turn_id:
        raise AppServerError("turn/start response did not include a turn id")
    output_parts: list[str] = []
    completed_output: str | None = None
    first_output_at: float | None = None
    end_total: dict[str, Any] | None = None
    last_requests: list[dict[str, Any]] = []
    usage_updates = 0
    compactions = 0
    tool_activity: list[dict[str, Any]] = []
    status = "unknown"
    error: Any = None
    completed_at = response_at
    deadline = time.monotonic() + server.timeout
    while True:
        event = server.next_event(deadline)
        payload = event.payload
        method = payload.get("method")
        params = payload.get("params", {})
        if "id" in payload and method:
            if method != "item/tool/call":
                raise AppServerError(f"unsupported app-server request during turn: {method}")
            tool_result, activity = tool_handler(params)
            tool_activity.append(activity)
            server.respond(payload["id"], tool_result)
            continue
        if method == "thread/tokenUsage/updated":
            usage = params.get("tokenUsage") or {}
            total = usage.get("total")
            last = usage.get("last")
            if isinstance(total, dict):
                server.token_totals[params.get("threadId") or thread_id] = total
            if params.get("turnId") in {None, turn_id}:
                usage_updates += 1
                if isinstance(total, dict):
                    end_total = total
                if isinstance(last, dict):
                    last_requests.append(last)
            continue
        if params.get("turnId") not in {None, turn_id}:
            continue
        if method == "item/agentMessage/delta":
            if first_output_at is None:
                first_output_at = event.received_at
            output_parts.append(params.get("delta", ""))
        elif method == "item/completed":
            item = params.get("item", {})
            if item.get("type") == "agentMessage":
                if first_output_at is None:
                    first_output_at = event.received_at
                completed_output = item.get("text", "")
            elif on_item is not None:
                row = on_item(item)
                if row is not None:
                    tool_activity.append(row)
        elif method in {"thread/compact/start", "thread/compacted"}:
            compactions += 1
        elif method == "error" and params.get("turnId") == turn_id:
            error = params.get("error")
        elif method == "turn/completed":
            turn = params.get("turn", {})
            if turn.get("id") != turn_id:
                continue
            status = turn.get("status", "unknown")
            error = turn.get("error") or error
            completed_at = event.received_at
            break
    token_usage = normalize_usage(None)
    token_accounting = "none"
    if end_total is not None:
        token_usage = usage_delta(snapshot, end_total)
        token_accounting = "total_delta"
    if token_usage["total_tokens"] == 0 and last_requests:
        fallback = sum_usage(last_requests)
        if fallback["total_tokens"] > 0:
            token_usage = fallback
            token_accounting = "last_sum"
    return TurnResult(
        turn_id=turn_id,
        status=status,
        error=error,
        output=completed_output if completed_output is not None else "".join(output_parts),
        started=started,
        response_at=response_at,
        first_output_at=first_output_at,
        completed_at=completed_at,
        token_usage=token_usage,
        token_accounting=token_accounting,
        usage_updates=usage_updates,
        compactions=compactions,
        tool_activity=tool_activity,
    )
