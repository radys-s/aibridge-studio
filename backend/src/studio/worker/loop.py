"""Worker: a bounded tool loop driven by a cheap model through OmniRoute.

The worker edits only the run's working copy, only inside the task scope, and runs only
allowlisted commands. Its own test runs are recorded as claims; Studio verifies separately.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from studio.agents.tools import (
    FINISH,
    LIST_FILES,
    READ_FILE,
    RUN_COMMAND,
    WRITE_TOOLS,
    FileTools,
)
from studio.db import Database
from studio.domain import Budget, Plan, TaskSpec
from studio.gateway import ChatGateway, GatewayError, GatewayUnavailable
from studio.routing import BudgetExceeded, ModelRouter, NoModelAvailable, RouteBudget
from studio.sandbox import Denied, Sandbox, safe_text, safe_value, secret_findings

MAX_MODEL_SWITCHES_PER_TURN = 4
MAX_NUDGES = 3
# Context trimming: the history grows every turn and is re-sent in full. Tool results and
# write_file contents older than the last KEEP_RECENT_TOOL_RESULTS results shrink to a stub, so a
# long run does not hit free-tier token caps (seen live: 44.7k tokens for 11 worker turns).
KEEP_RECENT_TOOL_RESULTS = 6
TRIM_ABOVE_CHARS = 600

TOOLS: list[dict[str, Any]] = [LIST_FILES, READ_FILE, *WRITE_TOOLS, RUN_COMMAND, FINISH]


class WorkerStopped(Exception):
    """Cancel requested by the user."""


@dataclass
class WorkerOutcome:
    status: str  # finished | turn_limit | time_limit | budget | no_model | gateway_down | error
    summary: str = ""
    turns: int = 0
    files_written: list[str] = field(default_factory=list)
    claimed_checks: list[dict[str, Any]] = field(default_factory=list)
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def system_prompt(spec: TaskSpec, plan: Plan | None, feedback: list[str]) -> str:
    lines = [
        "You are a careful software engineer working in a git repository.",
        "Use the tools to inspect and change files. Paths are relative, use forward slashes.",
        "There is no shell: run_command takes exactly one command without pipes or '&&'.",
        f"You may change only files matching: {', '.join(spec.allowed_paths)}.",
        "Keep the change minimal and focused on the goal. Do not touch unrelated code.",
        "Before finishing, run the acceptance commands yourself and make them pass.",
        "When done, call finish with a short factual summary.",
        "",
        f"GOAL:\n{spec.goal}",
        "",
        "ACCEPTANCE CRITERIA:",
        *[f"- {c}" for c in spec.acceptance_criteria],
        "",
        "ACCEPTANCE COMMANDS (run from repository root):",
        *[f"- {c}" for c in spec.acceptance_commands],
    ]
    if plan:
        lines += ["", "PLAN FROM THE LEAD ENGINEER:", plan.summary, *[f"- {s}" for s in plan.steps]]
        if plan.files_to_touch:
            lines.append("Likely files: " + ", ".join(plan.files_to_touch))
    if feedback:
        lines += ["", "THE PREVIOUS ATTEMPT WAS REVIEWED. FIX THESE ISSUES:", *feedback]
    return "\n".join(lines)


class Worker:
    def __init__(
        self,
        db: Database,
        gateway: ChatGateway,
        router: ModelRouter,
        sandbox: Sandbox,
        run_id: str,
        step_id: int,
        budget: Budget,
        complexity: str = "moderate",
        should_stop: Callable[[], bool] = lambda: False,
    ) -> None:
        self.db = db
        self.gateway = gateway
        self.router = router
        self.sandbox = sandbox
        self.run_id = run_id
        self.step_id = step_id
        self.budget = budget
        self.complexity = complexity
        self.should_stop = should_stop

    # --- model call through the router ------------------------------------------------

    def _call(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        excluded: set[str] = set()
        route_budget = RouteBudget(self.budget.max_tokens, self.budget.max_cost_usd)
        for _ in range(MAX_MODEL_SWITCHES_PER_TURN):
            model = self.router.choose(
                self.run_id,
                route_budget,
                excluded,
                complexity=self.complexity,
                role="worker",
                should_stop=self.should_stop,
            )
            try:
                res = self.gateway.chat(model, safe_value(messages), TOOLS)
            except GatewayError as exc:
                self.db.record_model_call(
                    run_id=self.run_id,
                    step_id=self.step_id,
                    role="worker",
                    requested_model=model,
                    outcome=exc.kind,
                    error=str(exc)[:500],
                )
                self.db.event(
                    self.run_id, "model_error", f"{model}: {exc.kind}", {"error": str(exc)[:300]}
                )
                if isinstance(exc, GatewayUnavailable):
                    raise
                self.router.report_failure(
                    model, exc.kind, exc.status, str(exc), getattr(exc, "retry_after", None)
                )
                excluded.add(model)
                continue
            self.db.record_model_call(
                run_id=self.run_id,
                step_id=self.step_id,
                role="worker",
                requested_model=model,
                actual_model=res.actual_model,
                provider=res.provider,
                tokens_in=res.tokens_in,
                tokens_out=res.tokens_out,
                cost_usd=res.cost_usd,
                latency_ms=res.latency_ms,
                outcome="ok",
            )
            return res.message
        raise NoModelAvailable(f"{MAX_MODEL_SWITCHES_PER_TURN} models failed in one turn")

    # --- tools ------------------------------------------------------------------------

    def _tool(self, name: str, args: dict[str, Any], outcome: WorkerOutcome) -> str:
        files = FileTools(self.sandbox)
        result = files.call(name, args)
        outcome.files_written.extend(files.files_written)
        if result is not None:
            return result
        if name != "run_command":
            return f"error: unknown tool {name}"
        try:
            res = self.sandbox.run_command(str(args["command"]))
        except Denied as exc:
            return f"denied: {exc}"
        except KeyError as exc:
            return f"error: missing argument {exc}"
        outcome.claimed_checks.append({k: v for k, v in res.as_dict().items() if k != "output"})
        return json.dumps(res.as_dict(), ensure_ascii=False)

    # --- loop -------------------------------------------------------------------------

    def run(self, spec: TaskSpec, plan: Plan | None, feedback: list[str]) -> WorkerOutcome:
        outcome = WorkerOutcome(status="error")
        prompt = system_prompt(spec, plan, feedback)
        if secret_findings(prompt):
            outcome.status, outcome.detail = "sensitive", "Sensitive worker prompt withheld."
            return outcome
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "Start. Inspect the repository, then make the change."},
        ]
        deadline = time.monotonic() + self.budget.max_worker_seconds
        nudges = 0
        try:
            while True:
                if self.should_stop():
                    raise WorkerStopped()
                if outcome.turns >= self.budget.max_worker_turns:
                    outcome.status = "turn_limit"
                    return outcome
                if time.monotonic() > deadline:
                    outcome.status = "time_limit"
                    return outcome
                outcome.turns += 1
                msg = self._call(compact(messages))
                if secret_findings(json.dumps(msg, ensure_ascii=False)):
                    outcome.status, outcome.detail = "sensitive", "Sensitive agent output withheld."
                    return outcome
                calls = msg.get("tool_calls") or []
                assistant: dict[str, Any] = {
                    "role": "assistant",
                    "content": msg.get("content") or "",
                }
                if calls:
                    assistant["tool_calls"] = calls
                messages.append(assistant)
                if not calls:
                    nudges += 1
                    if nudges > MAX_NUDGES:
                        outcome.status = "error"
                        outcome.detail = "model stopped calling tools"
                        return outcome
                    messages.append(
                        {"role": "user", "content": "Use the tools. Call finish when done."}
                    )
                    continue
                for call in calls:
                    fn = call.get("function") or {}
                    name = str(fn.get("name"))
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                        if not isinstance(args, dict):
                            raise ValueError("arguments must be an object")
                    except ValueError as exc:
                        result = f"error: invalid JSON arguments: {exc}"
                        args = {}
                    else:
                        if name == "finish":
                            outcome.status = "finished"
                            outcome.summary = str(args.get("summary", ""))[:4000]
                            return outcome
                        result = safe_text(self._tool(name, args, outcome))
                    self.db.event(
                        self.run_id, "tool", f"{name} {_brief(args)}", {"result": result[:300]}
                    )
                    messages.append(
                        {"role": "tool", "tool_call_id": call.get("id", ""), "content": result}
                    )
        except BudgetExceeded as exc:
            outcome.status, outcome.detail = "budget", str(exc)
        except NoModelAvailable as exc:
            if self.should_stop():  # stopped while waiting for a cooling model
                raise WorkerStopped() from exc
            outcome.status, outcome.detail = "no_model", str(exc)
        except GatewayUnavailable as exc:
            outcome.status, outcome.detail = "gateway_down", str(exc)
        return outcome


def compact(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What is sent to the model: old bulky tool results and file contents shortened.

    The full history stays in `messages`; this returns a trimmed copy. Never changed: system
    and user messages, the last KEEP_RECENT_TOOL_RESULTS tool results with the assistant
    turns that issued them, the latest read of each file (replace_in_file needs its exact
    text) and replace_in_file arguments. Old write_file content becomes a placeholder; the
    file itself is on disk. Tool call ids stay, so the history remains valid.
    """
    tool_idx = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    if len(tool_idx) <= KEEP_RECENT_TOOL_RESULTS:
        return messages
    first_kept = tool_idx[-KEEP_RECENT_TOOL_RESULTS]
    # The boundary is the assistant turn that issued the first kept result.
    cutoff = max(
        (i for i in range(first_kept) if messages[i].get("role") == "assistant"), default=0
    )
    calls: dict[str, tuple[str, str]] = {}  # tool_call_id -> (tool name, path)
    for m in messages:
        for c in m.get("tool_calls") or []:
            fn = c.get("function") or {}
            calls[str(c.get("id", ""))] = (str(fn.get("name")), _path(fn.get("arguments")))
    latest_read: dict[str, int] = {}
    for i in tool_idx:
        name, path = calls.get(str(messages[i].get("tool_call_id", "")), ("", ""))
        if name == "read_file" and path:
            latest_read[path] = i
    keep = set(latest_read.values())
    out: list[dict[str, Any]] = []
    for i, m in enumerate(messages):
        if i >= cutoff or i in keep:
            out.append(m)
        elif m.get("role") == "tool":
            out.append({**m, "content": _stub(str(m.get("content") or ""))})
        elif m.get("role") == "assistant" and m.get("tool_calls"):
            out.append({**m, "tool_calls": [_trim_write(c) for c in m["tool_calls"]]})
        else:
            out.append(m)
    return out


def _path(raw: Any) -> str:
    try:
        args = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return ""
    return str(args.get("path", "")) if isinstance(args, dict) else ""


def _stub(text: str) -> str:
    """Head and tail: a failing command prints the error at the end."""
    if len(text) <= TRIM_ABOVE_CHARS:
        return text
    return (
        f"{text[:300]}\n[... older result trimmed, {len(text)} chars in total; "
        f"call the tool again if you need it ...]\n{text[-300:]}"
    )


def _trim_write(call: dict[str, Any]) -> dict[str, Any]:
    fn = call.get("function") or {}
    if fn.get("name") != "write_file":
        return call
    try:
        args = json.loads(fn.get("arguments") or "{}")
    except (TypeError, ValueError):
        return call
    content = args.get("content") if isinstance(args, dict) else None
    if not isinstance(content, str) or len(content) <= TRIM_ABOVE_CHARS:
        return call
    short = {**args, "content": f"<{len(content)} chars written; read the file to see it>"}
    return {**call, "function": {**fn, "arguments": json.dumps(short, ensure_ascii=False)}}


def _brief(args: dict[str, Any]) -> str:
    for key in ("path", "command"):
        if key in args:
            return str(args[key])[:120]
    return ""
