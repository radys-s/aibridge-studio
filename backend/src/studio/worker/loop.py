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

from studio.db import Database
from studio.domain import Budget, Plan, TaskSpec
from studio.gateway import ChatGateway, GatewayError, GatewayUnavailable
from studio.routing import BudgetExceeded, ModelRouter, NoModelAvailable, RouteBudget
from studio.sandbox import Denied, Sandbox, clip

MAX_MODEL_SWITCHES_PER_TURN = 4
MAX_NUDGES = 3

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files under a directory of the repository (relative path).",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "e.g. '.' or 'src'"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 text file (relative path, forward slashes).",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a file with the complete new content.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_in_file",
            "description": "Replace one exact, unique occurrence of old_text with new_text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run one allowlisted command (no shell, no pipes), e.g. "
            "'python -m unittest -q'. Returns exit code and output.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Call when the change is complete and checks pass.",
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
        },
    },
]


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
                self.run_id, route_budget, excluded, complexity=self.complexity
            )
            try:
                res = self.gateway.chat(model, messages, TOOLS)
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
                self.router.report_failure(model, exc.kind, exc.status, str(exc))
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
        sb = self.sandbox
        try:
            if name == "list_files":
                return "\n".join(sb.list_files(str(args.get("path") or "."))) or "(empty)"
            if name == "read_file":
                return clip(sb.read_text(str(args["path"])))
            if name == "write_file":
                path = sb.check_writable(str(args["path"]))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(str(args["content"]), encoding="utf-8", newline="")
                outcome.files_written.append(sb.rel(path))
                return f"wrote {sb.rel(path)} ({len(str(args['content']))} chars)"
            if name == "replace_in_file":
                path = sb.check_writable(str(args["path"]))
                text = sb.read_text(str(args["path"]))
                old = str(args["old_text"])
                count = text.count(old) if old else 0
                if count != 1:
                    return f"error: old_text found {count} times; it must match exactly once"
                path.write_text(
                    text.replace(old, str(args["new_text"])), encoding="utf-8", newline=""
                )
                outcome.files_written.append(sb.rel(path))
                return f"replaced in {sb.rel(path)}"
            if name == "run_command":
                result = sb.run_command(str(args["command"]))
                outcome.claimed_checks.append(
                    {k: v for k, v in result.as_dict().items() if k != "output"}
                )
                return json.dumps(result.as_dict(), ensure_ascii=False)
            return f"error: unknown tool {name}"
        except Denied as exc:
            return f"denied: {exc}"
        except KeyError as exc:
            return f"error: missing argument {exc}"
        except OSError as exc:
            return f"error: {exc}"

    # --- loop -------------------------------------------------------------------------

    def run(self, spec: TaskSpec, plan: Plan | None, feedback: list[str]) -> WorkerOutcome:
        outcome = WorkerOutcome(status="error")
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt(spec, plan, feedback)},
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
                msg = self._call(messages)
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
                        result = self._tool(name, args, outcome)
                    self.db.event(
                        self.run_id, "tool", f"{name} {_brief(args)}", {"result": result[:300]}
                    )
                    messages.append(
                        {"role": "tool", "tool_call_id": call.get("id", ""), "content": result}
                    )
        except BudgetExceeded as exc:
            outcome.status, outcome.detail = "budget", str(exc)
        except NoModelAvailable as exc:
            outcome.status, outcome.detail = "no_model", str(exc)
        except GatewayUnavailable as exc:
            outcome.status, outcome.detail = "gateway_down", str(exc)
        return outcome


def _brief(args: dict[str, Any]) -> str:
    for key in ("path", "command"):
        if key in args:
            return str(args[key])[:120]
    return ""
