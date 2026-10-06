"""Independent verification: Studio runs the acceptance commands itself after the worker stops.

The worker's own test runs are claims. Only results produced here count as evidence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from studio import workspace
from studio.domain import TaskSpec
from studio.sandbox import Denied, Sandbox, in_scope


def verify(spec: TaskSpec, root: Path, base_commit: str) -> dict[str, Any]:
    sandbox = Sandbox(root, spec.allowed_paths, spec.budget.command_timeout_s)
    changed = workspace.changed_files(root, base_commit)
    out_of_scope = [f for f in changed if not in_scope(f, spec.allowed_paths)]
    commands: list[dict[str, Any]] = []
    for command in spec.acceptance_commands:
        try:
            commands.append(sandbox.run_command(command).as_dict())
        except Denied as exc:
            commands.append(
                {
                    "command": command,
                    "exit_code": None,
                    "output": f"denied: {exc}",
                    "timed_out": False,
                    "seconds": 0,
                }
            )
    passed = (
        bool(changed)
        and not out_of_scope
        and all(c["exit_code"] == 0 and not c["timed_out"] for c in commands)
    )
    return {
        "passed": passed,
        "changed_files": changed,
        "out_of_scope": out_of_scope,
        "empty_diff": not changed,
        "commands": commands,
    }


def failure_feedback(result: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    if result["empty_diff"]:
        notes.append("- No files were changed.")
    if result["out_of_scope"]:
        notes.append("- Revert changes outside the scope: " + ", ".join(result["out_of_scope"]))
    for c in result["commands"]:
        if c["exit_code"] != 0 or c["timed_out"]:
            notes.append(f"- Acceptance command failed: {c['command']}\n{c['output'][-3000:]}")
    return notes
