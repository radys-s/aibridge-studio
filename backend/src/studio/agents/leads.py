"""Claude Code and Codex as leads: drafting, planning, review and (on takeover) implementation.

Provenance: invocation pattern (stdin prompt, JSON-schema structured output, timeout, health
probe) derived from AI Bridge `backend/src/aibridge/agents/claude.py` and `codex.py`
(commit 953e0b9). Changes: per-call model and effort, streamed events (Claude
`stream-json`, Codex `--json`), usage capture, read-only vs. write modes, Studio-specific
prompts and schemas, cancellable subprocess, scrubbed environment, hand-written strict
schemas (Codex structured output requires `additionalProperties: false` and every property
required).

Both CLIs use their own login. Nothing here assumes API access from a subscription.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ValidationError

from studio.agents.prompts import (
    DRAFT_SCHEMA,
    IMPLEMENT_SCHEMA,
    PLAN_SCHEMA,
    REVIEW_SCHEMA,
)
from studio.domain import LeadConfig

Mode = Literal["read", "write"]


class LeadError(Exception):
    def __init__(self, message: str, kind: str = "error") -> None:
        super().__init__(message)
        # error | timeout | not_installed | not_logged_in | limit | cancelled | bad_output
        # Autopilot adds: no_model | budget | gateway_down
        self.kind = kind


@dataclass(frozen=True)
class AgentEvent:
    """One line of what a lead is doing, for the live view. Never fed back to a model."""

    kind: str  # text | tool | tool_result | status | error
    text: str
    data: dict[str, Any] | None = None


@dataclass
class LeadUsage:
    actual_model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0  # Claude reports an API-equivalent figure even on a subscription


@dataclass
class CallContext:
    config: LeadConfig
    timeout_s: int
    should_stop: Callable[[], bool]
    on_event: Callable[[AgentEvent], None] = lambda _e: None
    usage: LeadUsage = field(default_factory=LeadUsage)
    # Used by Autopilot, which records its own gateway calls and routes through the router.
    run_id: str | None = None
    step_id: int | None = None
    role: str = "lead"
    scope: tuple[str, ...] = ()
    complexity: str = "moderate"
    exclude_models: frozenset[str] = frozenset()
    max_tokens: int = 400_000
    max_cost_usd: float = 0.5


class Lead(Protocol):
    name: str

    def available(self) -> dict[str, Any]: ...

    def ask(
        self, prompt: str, schema: dict[str, Any], cwd: Path, ctx: CallContext, mode: Mode = ...
    ) -> dict[str, Any]: ...


# --- environment ---------------------------------------------------------------------

# Variables a host process (e.g. the Claude desktop app) sets for its own child sessions.
# Inherited by a headless CLI they change how it authenticates or where it sends requests.
_HOST_PREFIXES = ("CLAUDE", "ANTHROPIC_", "USE_LOCAL_OAUTH", "USE_STAGING_OAUTH", "CODEX_SANDBOX")
_SECRET_NAME = re.compile(r"(API_KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|COOKIE)", re.I)


def lead_env() -> dict[str, str]:
    """Full user environment minus secrets and host-session variables.

    The CLIs need the user profile to find their own login, so this is not the worker's
    minimal allowlist; it only removes what would change billing (API keys) or leak.
    """
    return {
        k: v
        for k, v in os.environ.items()
        if not k.upper().startswith(_HOST_PREFIXES) and not _SECRET_NAME.search(k)
    }


# --- process --------------------------------------------------------------------------


@dataclass
class RunResult:
    returncode: int
    lines: list[str]
    stderr: str


def _run(
    argv: list[str],
    prompt: str,
    cwd: Path,
    ctx: CallContext,
    on_line: Callable[[str], None],
) -> RunResult:
    """Run a CLI, stream its stdout line by line, kill it on cancel and on timeout."""
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=lead_env(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    lines: queue.Queue[str | None] = queue.Queue()
    err_chunks: list[str] = []

    def pump_out() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)

    def pump_err() -> None:
        assert proc.stderr is not None
        err_chunks.append(proc.stderr.read())

    threads = [threading.Thread(target=t, daemon=True) for t in (pump_out, pump_err)]
    for t in threads:
        t.start()
    try:
        assert proc.stdin is not None
        proc.stdin.write(prompt)
        proc.stdin.close()
    except OSError:
        pass
    deadline = time.monotonic() + ctx.timeout_s
    collected: list[str] = []

    def kill(reason: str, kind: str) -> LeadError:
        kill_tree(proc)
        return LeadError(reason, kind)

    while True:
        try:
            line = lines.get(timeout=0.5)
        except queue.Empty:
            line = ""
        if line is None:
            break
        if line:
            collected.append(line)
            try:
                on_line(line)
            except Exception as exc:  # a parse bug must not kill the run silently
                ctx.on_event(AgentEvent("error", f"stream parse error: {exc}"))
        if ctx.should_stop():
            raise kill("cancelled by user", "cancelled")
        if time.monotonic() > deadline:
            raise kill(f"timed out after {ctx.timeout_s}s", "timeout")
    proc.wait()
    for t in threads:
        t.join(timeout=5)
    return RunResult(proc.returncode, collected, "".join(err_chunks))


def kill_tree(proc: subprocess.Popen[str]) -> None:
    """Kill the CLI and everything it started. On Windows the CLIs are often `.cmd` shims,
    so killing only the direct child would leave the real agent running."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
    proc.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=10)


def _clip(text: str, n: int = 400) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def validate[T: BaseModel](model: type[T], payload: Any) -> T:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise LeadError(f"structured output invalid: {exc}", "bad_output") from exc


# --- Claude ---------------------------------------------------------------------------


class ClaudeLead:
    name = "claude"

    def __init__(self, executable: str = "claude") -> None:
        self.executable = executable

    def available(self) -> dict[str, Any]:
        return {"installed": shutil.which(self.executable) is not None}

    def argv(
        self, binary: str, schema: dict[str, Any], config: LeadConfig, mode: Mode
    ) -> list[str]:
        tools = ["Read", "Grep", "Glob"] + (["Edit", "Write"] if mode == "write" else [])
        return [
            binary,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--json-schema",
            json.dumps(schema),
            *(["--model", config.model] if config.model else []),
            *(["--effort", config.effort] if config.effort else []),
            "--permission-mode",
            "acceptEdits" if mode == "write" else "plan",
            # No Bash in either mode: Studio runs every command itself, under its policy.
            "--tools",
            *tools,
            "--strict-mcp-config",
            "--setting-sources",
            "project",
            "--no-session-persistence",
        ]

    def ask(
        self,
        prompt: str,
        schema: dict[str, Any],
        cwd: Path,
        ctx: CallContext,
        mode: Mode = "read",
    ) -> dict[str, Any]:
        binary = shutil.which(self.executable)
        if not binary:
            raise LeadError("claude CLI not installed", "not_installed")
        final: dict[str, Any] = {}

        def on_line(line: str) -> None:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                return
            self._emit(ev, ctx, final)

        result = _run(self.argv(binary, schema, ctx.config, mode), prompt, cwd, ctx, on_line)
        if not final:
            raise LeadError(
                f"claude exited {result.returncode} without a result: "
                f"{(result.stderr or ''.join(result.lines))[-600:]}",
                "error" if result.returncode else "bad_output",
            )
        if final.get("is_error") or result.returncode != 0:
            message = str(final.get("result") or result.stderr)[:500]
            kind = "not_logged_in" if "authenticate" in message.lower() else "error"
            raise LeadError(f"claude reported error: {message}", kind)
        structured = final.get("structured_output")
        if not isinstance(structured, dict):
            raise LeadError("claude response has no structured_output", "bad_output")
        return structured

    @staticmethod
    def _emit(ev: dict[str, Any], ctx: CallContext, final: dict[str, Any]) -> None:
        kind = ev.get("type")
        if kind == "system" and ev.get("subtype") == "init":
            ctx.usage.actual_model = ev.get("model") or ctx.usage.actual_model
            ctx.on_event(AgentEvent("status", f"model {ev.get('model')}"))
        elif kind == "assistant":
            for block in (ev.get("message") or {}).get("content") or []:
                if block.get("type") == "text" and block.get("text", "").strip():
                    ctx.on_event(AgentEvent("text", _clip(block["text"], 2000)))
                elif block.get("type") == "tool_use":
                    args = block.get("input") or {}
                    target = args.get("file_path") or args.get("path") or args.get("pattern") or ""
                    ctx.on_event(
                        AgentEvent("tool", f"{block.get('name')} {target}".strip(), {"input": args})
                    )
        elif kind == "user":
            for block in (ev.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    content = block.get("content")
                    text = content if isinstance(content, str) else json.dumps(content)[:400]
                    ctx.on_event(AgentEvent("tool_result", _clip(text, 300)))
        elif kind == "result":
            final.update(ev)
            usage = ev.get("usage") or {}
            ctx.usage.tokens_in = int(
                usage.get("input_tokens", 0)
                + usage.get("cache_creation_input_tokens", 0)
                + usage.get("cache_read_input_tokens", 0)
            )
            ctx.usage.tokens_out = int(usage.get("output_tokens", 0))
            ctx.usage.cost_usd = float(ev.get("total_cost_usd") or 0.0)
            models = list((ev.get("modelUsage") or {}).keys())
            if models and not ctx.usage.actual_model:
                ctx.usage.actual_model = models[0]


# --- Codex ----------------------------------------------------------------------------


class CodexLead:
    name = "codex"

    def __init__(
        self,
        executable: str = "codex",
        windows_sandbox: str | None = "elevated" if os.name == "nt" else None,
    ) -> None:
        self.executable = executable
        # --ignore-user-config also drops `[windows] sandbox`; without it workspace-write
        # silently degrades to read-only on Windows (verified live 2026-10-06).
        self.windows_sandbox = windows_sandbox

    def available(self) -> dict[str, Any]:
        return {"installed": shutil.which(self.executable) is not None}

    def argv(
        self,
        binary: str,
        cwd: Path,
        schema_path: Path,
        out_path: Path,
        config: LeadConfig,
        mode: Mode,
    ) -> list[str]:
        # --ignore-user-config: the user's config.toml may pin a model the ChatGPT login
        # cannot use in `exec` (verified 2026-10-06) and it loads personal hooks.
        return [
            binary,
            "exec",
            "--ignore-user-config",
            *(["-m", config.model] if config.model else []),
            *(["-c", f"model_reasoning_effort={config.effort}"] if config.effort else []),
            *(["-c", f'windows.sandbox="{self.windows_sandbox}"'] if self.windows_sandbox else []),
            "-C",
            str(cwd),
            "--sandbox",
            "workspace-write" if mode == "write" else "read-only",
            "--skip-git-repo-check",
            "--ephemeral",
            "--json",
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(out_path),
            "-",
        ]

    def ask(
        self,
        prompt: str,
        schema: dict[str, Any],
        cwd: Path,
        ctx: CallContext,
        mode: Mode = "read",
    ) -> dict[str, Any]:
        binary = shutil.which(self.executable)
        if not binary:
            raise LeadError("codex CLI not installed", "not_installed")
        ctx.usage.actual_model = ctx.config.model or "codex-default"
        failures: list[str] = []
        with tempfile.TemporaryDirectory(prefix="studio-codex-") as tmp:
            schema_path = Path(tmp) / "schema.json"
            out_path = Path(tmp) / "out.json"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")

            def on_line(line: str) -> None:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    return
                self._emit(ev, ctx, failures)

            argv = self.argv(binary, cwd, schema_path, out_path, ctx.config, mode)
            result = _run(argv, prompt, cwd, ctx, on_line)
            if result.returncode != 0 or failures:
                detail = "; ".join(failures) or (result.stderr or "".join(result.lines))[-600:]
                kind = (
                    "not_logged_in"
                    if re.search(r"log ?in|401|unauthori", detail, re.I)
                    else "error"
                )
                raise LeadError(f"codex exited {result.returncode}: {_clip(detail, 600)}", kind)
            try:
                payload = json.loads(out_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise LeadError(f"codex output unreadable: {exc}", "bad_output") from exc
        if not isinstance(payload, dict):
            raise LeadError("codex output is not an object", "bad_output")
        return payload

    @staticmethod
    def _emit(ev: dict[str, Any], ctx: CallContext, failures: list[str]) -> None:
        kind = ev.get("type")
        if kind in ("item.completed", "item.started"):
            item = ev.get("item") or {}
            itype = item.get("type")
            done = kind == "item.completed"
            if itype == "agent_message" and done:
                ctx.on_event(AgentEvent("text", _clip(item.get("text", ""), 2000)))
            elif itype == "reasoning" and done and item.get("text"):
                ctx.on_event(AgentEvent("status", _clip(item["text"], 300)))
            elif itype == "command_execution":
                if done:
                    ctx.on_event(
                        AgentEvent(
                            "tool_result",
                            f"exit {item.get('exit_code')}: "
                            + _clip(item.get("aggregated_output", ""), 300),
                        )
                    )
                else:
                    ctx.on_event(AgentEvent("tool", f"$ {_clip(item.get('command', ''), 200)}"))
            elif itype == "file_change" and done:
                paths = [c.get("path", "") for c in item.get("changes") or []]
                ctx.on_event(AgentEvent("tool", "edit " + ", ".join(paths), {"changes": paths}))
            elif itype == "error" and done:
                # Codex reports soft warnings (e.g. trimmed skill list) as error items.
                ctx.on_event(AgentEvent("status", _clip(item.get("message", ""), 300)))
        elif kind == "turn.completed":
            usage = ev.get("usage") or {}
            ctx.usage.tokens_in += int(usage.get("input_tokens", 0))
            ctx.usage.tokens_out += int(usage.get("output_tokens", 0)) + int(
                usage.get("reasoning_output_tokens", 0)
            )
        elif kind in ("turn.failed", "error"):
            raw = (
                (ev.get("error") or {}).get("message")
                if kind == "turn.failed"
                else ev.get("message")
            )
            text = str(raw or ev)
            try:  # the message is often a JSON-encoded API error
                inner = json.loads(text)
                text = str((inner.get("error") or {}).get("message") or text)
            except (json.JSONDecodeError, AttributeError):
                pass
            if text not in failures:
                failures.append(text)
            ctx.on_event(AgentEvent("error", _clip(text, 500)))


SCHEMAS = {
    "draft": DRAFT_SCHEMA,
    "plan": PLAN_SCHEMA,
    "review": REVIEW_SCHEMA,
    "implement": IMPLEMENT_SCHEMA,
}
