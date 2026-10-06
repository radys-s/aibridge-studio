"""Claude Code and Codex as leads: planning and independent review, read-only.

Provenance: invocation pattern (stdin prompt, JSON-schema structured output, timeout, health
probe) derived from AI Bridge `backend/src/aibridge/agents/claude.py` and `codex.py`
(commit 953e0b9). Changes: read-only tool restriction, Studio-specific prompts and schemas,
cancellable subprocess, hand-written strict schemas (Codex structured output requires
`additionalProperties: false` and every property required).

Both CLIs use their own login. Nothing here assumes API access from a subscription.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from studio.domain import Plan, Review, TaskSpec


class LeadError(Exception):
    def __init__(self, message: str, kind: str = "error") -> None:
        super().__init__(message)
        self.kind = kind  # error | timeout | not_installed | cancelled | bad_output


def _obj(props: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": props,
        "required": list(props),
    }


_STR = {"type": "string"}
_STRS = {"type": "array", "items": _STR}

PLAN_SCHEMA = _obj(
    {
        "summary": _STR,
        "steps": _STRS,
        "files_to_touch": _STRS,
        "risks": _STRS,
        "complexity": {"type": "string", "enum": ["trivial", "moderate", "hard"]},
        "recommended_implementer": {"type": "string", "enum": ["worker", "strong_agent"]},
    }
)

REVIEW_SCHEMA = _obj(
    {
        "verdict": {"type": "string", "enum": ["accept", "request_changes", "reject"]},
        "summary": _STR,
        "findings": {
            "type": "array",
            "items": _obj(
                {
                    "severity": {"type": "string", "enum": ["blocker", "major", "minor"]},
                    "file": _STR,
                    "problem": _STR,
                    "fix": _STR,
                }
            ),
        },
        "criteria": {
            "type": "array",
            "items": _obj({"criterion": _STR, "met": {"type": "boolean"}, "evidence": _STR}),
        },
    }
)


def plan_prompt(spec: TaskSpec) -> str:
    return "\n".join(
        [
            "You are the lead engineer. Plan a change that a smaller, cheaper coding model will",
            "implement with file read/write tools. Inspect the repository read-only. Do not edit.",
            "Return a short concrete plan. Mark complexity 'hard' and recommend 'strong_agent'",
            "only if a small model is unlikely to succeed.",
            "",
            f"TITLE: {spec.title}",
            f"GOAL:\n{spec.goal}",
            "ALLOWED PATHS: " + ", ".join(spec.allowed_paths),
            "ACCEPTANCE CRITERIA:\n" + "\n".join(f"- {c}" for c in spec.acceptance_criteria),
            "ACCEPTANCE COMMANDS:\n" + "\n".join(f"- {c}" for c in spec.acceptance_commands),
        ]
    )


def review_prompt(
    spec: TaskSpec, diff: str, verification: dict[str, Any], worker_summary: str
) -> str:
    checks = "\n".join(
        f"- `{c['command']}` exit={c['exit_code']} timed_out={c['timed_out']}\n"
        f"{str(c['output'])[-2500:]}"
        for c in verification["commands"]
    )
    return "\n".join(
        [
            "You are an independent code reviewer. Judge the DIFF against the GOAL and every",
            "ACCEPTANCE CRITERION. Do not trust the worker's summary; it is an unverified claim.",
            "The independent verification below was run by the orchestrator, not the worker.",
            "You may read files in the repository to check context. Do not edit anything.",
            "Verdict 'accept' only if every criterion is met and there is no blocker or major",
            "finding. 'request_changes' for fixable problems. 'reject' if the approach is wrong.",
            "",
            f"GOAL:\n{spec.goal}",
            "ALLOWED PATHS: " + ", ".join(spec.allowed_paths),
            "ACCEPTANCE CRITERIA:\n" + "\n".join(f"- {c}" for c in spec.acceptance_criteria),
            "",
            "INDEPENDENT VERIFICATION:",
            f"passed={verification['passed']} changed={verification['changed_files']} "
            f"out_of_scope={verification['out_of_scope']}",
            checks,
            "",
            "WORKER SUMMARY (unverified claim):",
            worker_summary[:2000] or "(none)",
            "",
            "DIFF:",
            diff[:60_000] if diff else "(empty diff)",
        ]
    )


class Lead(Protocol):
    name: str

    def available(self) -> dict[str, Any]: ...

    def plan(self, spec: TaskSpec, cwd: Path, should_stop: Callable[[], bool]) -> Plan: ...

    def review(
        self, spec: TaskSpec, cwd: Path, prompt: str, should_stop: Callable[[], bool]
    ) -> Review: ...


def _run(
    argv: list[str], prompt: str, cwd: Path, timeout_s: int, should_stop: Callable[[], bool]
) -> subprocess.CompletedProcess[str]:
    """Run a CLI, killable on cancel and on timeout."""
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    deadline = time.monotonic() + timeout_s
    try:
        assert proc.stdin is not None
        proc.stdin.write(prompt)
        proc.stdin.close()
    except OSError:
        pass
    while True:
        try:
            out, err = proc.communicate(timeout=1.0)
            return subprocess.CompletedProcess(argv, proc.returncode, out, err)
        except subprocess.TimeoutExpired:
            if should_stop():
                proc.kill()
                proc.communicate()
                raise LeadError("cancelled by user", "cancelled") from None
            if time.monotonic() > deadline:
                proc.kill()
                proc.communicate()
                raise LeadError(f"timed out after {timeout_s}s", "timeout") from None


def _validate[T: BaseModel](model: type[T], payload: Any) -> T:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise LeadError(f"structured output invalid: {exc}", "bad_output") from exc


class ClaudeLead:
    name = "claude"

    def __init__(self, executable: str = "claude", timeout_s: int = 600) -> None:
        self.executable = executable
        self.timeout_s = timeout_s

    def available(self) -> dict[str, Any]:
        return {"installed": shutil.which(self.executable) is not None}

    def _ask(self, prompt: str, schema: dict[str, Any], cwd: Path, stop: Callable[[], bool]) -> Any:
        binary = shutil.which(self.executable)
        if not binary:
            raise LeadError("claude CLI not installed", "not_installed")
        argv = [
            binary,
            "-p",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema),
            "--permission-mode",
            "plan",
            "--tools",
            "Read",
            "Grep",
            "Glob",
            "--strict-mcp-config",
            "--setting-sources",
            "project",
            "--no-session-persistence",
        ]
        result = _run(argv, prompt, cwd, self.timeout_s, stop)
        if result.returncode != 0:
            raise LeadError(
                f"claude exited {result.returncode}: {(result.stderr or result.stdout)[-600:]}"
            )
        try:
            envelope = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise LeadError(f"claude output not JSON: {result.stdout[:300]}", "bad_output") from exc
        if envelope.get("is_error"):
            raise LeadError(f"claude reported error: {str(envelope.get('result'))[:500]}")
        structured = envelope.get("structured_output")
        if not isinstance(structured, dict):
            raise LeadError("claude response has no structured_output", "bad_output")
        return structured

    def plan(self, spec: TaskSpec, cwd: Path, should_stop: Callable[[], bool]) -> Plan:
        return _validate(Plan, self._ask(plan_prompt(spec), PLAN_SCHEMA, cwd, should_stop))

    def review(
        self, spec: TaskSpec, cwd: Path, prompt: str, should_stop: Callable[[], bool]
    ) -> Review:
        return _validate(Review, self._ask(prompt, REVIEW_SCHEMA, cwd, should_stop))


class CodexLead:
    name = "codex"

    def __init__(self, executable: str = "codex", timeout_s: int = 600) -> None:
        self.executable = executable
        self.timeout_s = timeout_s

    def available(self) -> dict[str, Any]:
        return {"installed": shutil.which(self.executable) is not None}

    def _ask(self, prompt: str, schema: dict[str, Any], cwd: Path, stop: Callable[[], bool]) -> Any:
        binary = shutil.which(self.executable)
        if not binary:
            raise LeadError("codex CLI not installed", "not_installed")
        with tempfile.TemporaryDirectory(prefix="studio-codex-") as tmp:
            schema_path = Path(tmp) / "schema.json"
            out_path = Path(tmp) / "out.json"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")
            argv = [
                binary,
                "exec",
                "-C",
                str(cwd),
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "--ephemeral",
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(out_path),
                "-",
            ]
            result = _run(argv, prompt, cwd, self.timeout_s, stop)
            if result.returncode != 0:
                raise LeadError(
                    f"codex exited {result.returncode}: {(result.stderr or result.stdout)[-600:]}"
                )
            try:
                return json.loads(out_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise LeadError(f"codex output unreadable: {exc}", "bad_output") from exc

    def plan(self, spec: TaskSpec, cwd: Path, should_stop: Callable[[], bool]) -> Plan:
        return _validate(Plan, self._ask(plan_prompt(spec), PLAN_SCHEMA, cwd, should_stop))

    def review(
        self, spec: TaskSpec, cwd: Path, prompt: str, should_stop: Callable[[], bool]
    ) -> Review:
        return _validate(Review, self._ask(prompt, REVIEW_SCHEMA, cwd, should_stop))
