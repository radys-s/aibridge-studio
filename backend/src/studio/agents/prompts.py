"""Prompts and strict JSON schemas for the leads. English: they are read by models."""

from __future__ import annotations

from typing import Any

from studio.domain import Message, Plan, TaskSpec
from studio.sandbox import ALLOWED_COMMANDS, ALLOWED_GIT


def _obj(props: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": props,
        "required": list(props),
    }


_STR = {"type": "string"}
_STRS = {"type": "array", "items": _STR}

DRAFT_SCHEMA = _obj(
    {
        "title": _STR,
        "goal": _STR,
        "allowed_paths": _STRS,
        "acceptance_criteria": _STRS,
        "acceptance_commands": _STRS,
        "questions": _STRS,
        "notes": _STR,
    }
)

PLAN_SCHEMA = _obj(
    {
        "summary": _STR,
        "steps": _STRS,
        "files_to_touch": _STRS,
        "risks": _STRS,
        "complexity": {"type": "string", "enum": ["trivial", "moderate", "hard"]},
        "recommended_implementer": {"type": "string", "enum": ["worker", "strong_agent"]},
        "questions": _STRS,
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

IMPLEMENT_SCHEMA = _obj({"summary": _STR, "files_changed": _STRS, "concerns": _STRS})


def _conversation(messages: list[Message]) -> list[str]:
    if not messages:
        return []
    out = ["", "CONVERSATION WITH THE USER (newest last):"]
    for m in messages[-12:]:
        out.append(f"[{m.author}] {m.text[:2000]}")
    return out


def _spec_lines(spec: TaskSpec) -> list[str]:
    return [
        f"TITLE: {spec.title}",
        f"GOAL:\n{spec.goal}",
        "ALLOWED PATHS: " + ", ".join(spec.allowed_paths),
        "ACCEPTANCE CRITERIA:\n" + "\n".join(f"- {c}" for c in spec.acceptance_criteria),
        "ACCEPTANCE COMMANDS:\n" + "\n".join(f"- {c}" for c in spec.acceptance_commands),
    ]


def draft_prompt(spec: TaskSpec, messages: list[Message]) -> str:
    commands = ", ".join(sorted(ALLOWED_COMMANDS))
    git = ", ".join(sorted(ALLOWED_GIT))
    return "\n".join(
        [
            "You are the lead engineer. The user wrote a request in free text. Inspect the",
            "repository read-only and turn the request into a precise, checkable task.",
            "Do not edit anything.",
            "- title: short imperative title in the user's language.",
            "- goal: the request restated precisely (keep the user's language).",
            "- allowed_paths: narrowest glob patterns (relative, forward slashes) of files the",
            "  implementer may change, including tests it should add.",
            "- acceptance_criteria: observable, checkable statements.",
            "- acceptance_commands: commands that prove the criteria (tests, type checks).",
            f"  Only these programs can run: {commands}; git only with: {git}.",
            "  No shell operators, pipes or environment variable assignments.",
            "- questions: only questions whose answer changes the task; empty if it is clear.",
            "- notes: anything the user should know before approving (risks, assumptions).",
            "",
            f"REQUEST:\n{spec.goal}",
            *(
                ["", "USER-PROVIDED FIELDS (keep unless clearly wrong):", *_spec_lines(spec)]
                if spec.allowed_paths or spec.acceptance_criteria or spec.acceptance_commands
                else []
            ),
            *_conversation(messages),
        ]
    )


def plan_prompt(spec: TaskSpec, messages: list[Message]) -> str:
    return "\n".join(
        [
            "You are the lead engineer. Plan a change that a smaller, cheaper coding model will",
            "implement with file read/write tools. Inspect the repository read-only. Do not edit.",
            "Return a short concrete plan. Mark complexity 'hard' and recommend 'strong_agent'",
            "only if a small model is unlikely to succeed. Put questions only if the plan cannot",
            "be made without the user's answer.",
            "",
            *_spec_lines(spec),
            *_conversation(messages),
        ]
    )


def merge_prompt(spec: TaskSpec, plans: dict[str, Plan]) -> str:
    blocks = []
    for name, plan in plans.items():
        blocks += ["", f"PLAN FROM {name.upper()}:", plan.model_dump_json(indent=1)]
    return "\n".join(
        [
            "You are the lead engineer. Two leads planned the same task independently.",
            "Merge them into one plan: keep steps both agree on, resolve conflicts explicitly in",
            "favour of the safer correct option, keep every real risk and question either raised.",
            "Inspect the repository read-only if needed. Do not edit.",
            "",
            *_spec_lines(spec),
            *blocks,
        ]
    )


def implement_prompt(
    spec: TaskSpec, plan: Plan | None, feedback: list[str], messages: list[Message]
) -> str:
    lines = [
        "You are implementing this task directly in the working copy (cwd).",
        "Change only files matching ALLOWED PATHS; other changes fail verification.",
        "Do not run git commit/reset/rebase; leave changes in the working tree.",
        "Do not read or create credential files. You cannot run commands; the orchestrator",
        "runs the acceptance commands after you finish, so make the code right by reading it.",
        "",
        *_spec_lines(spec),
    ]
    if plan:
        lines += ["", "PLAN:", plan.summary, *[f"{i}. {s}" for i, s in enumerate(plan.steps, 1)]]
    if feedback:
        lines += ["", "FIX THESE PROBLEMS FROM THE PREVIOUS ATTEMPT:", *feedback]
    lines += _conversation(messages)
    lines += ["", "Return a summary, the files you changed and any concerns."]
    return "\n".join(lines)


def review_prompt(
    spec: TaskSpec,
    diff: str,
    verification: dict[str, Any],
    implementer_summary: str,
    implementer: str,
) -> str:
    checks = "\n".join(
        f"- `{c['command']}` exit={c['exit_code']} timed_out={c['timed_out']}\n"
        f"{str(c['output'])[-2500:]}"
        for c in verification["commands"]
    )
    return "\n".join(
        [
            "You are an independent code reviewer. Judge the DIFF against the GOAL and every",
            "ACCEPTANCE CRITERION. Do not trust the implementer's summary; it is an unverified",
            "claim. The independent verification below was run by the orchestrator.",
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
            f"IMPLEMENTER: {implementer}",
            "IMPLEMENTER SUMMARY (unverified claim):",
            implementer_summary[:2000] or "(none)",
            "",
            "DIFF:",
            diff[:60_000] if diff else "(empty diff)",
        ]
    )


def probe_prompt() -> str:
    return 'Reply with the JSON object {"ok": true}. Do not use tools.'


PROBE_SCHEMA = _obj({"ok": {"type": "boolean"}})
