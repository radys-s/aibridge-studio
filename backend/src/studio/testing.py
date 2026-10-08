"""Scripted gateway for end-to-end UI tests (STUDIO_FAKE_GATEWAY=1). Never used otherwise.

It plays a deterministic run on the synthetic fixture repo (`fixtures/synthetic_repo`): the
draft, plan, review and memory answers come back through the `submit` tool, and the worker
adds `median()` with a test. Everything Studio checks itself (scope, acceptance commands,
diff) still runs for real. Health reports `fake: true` and the UI shows it, so a fake run
can never pass for a live one.
"""

from __future__ import annotations

import json
from typing import Any

from studio.gateway import ChatResult

MEDIAN = '''"""Small statistics helpers."""


def mean(values: list[float]) -> float:
    """Arithmetic mean. Raises ValueError for an empty list."""
    if not values:
        raise ValueError("mean() of empty list")
    return sum(values) / len(values)


def median(values: list[float]) -> float:
    """Median. Raises ValueError for an empty list."""
    if not values:
        raise ValueError("median() of empty list")
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2
'''

MEDIAN_TEST = """import unittest

from calc.stats import median


class MedianTest(unittest.TestCase):
    def test_odd(self) -> None:
        self.assertEqual(median([3, 1, 2]), 2)

    def test_even(self) -> None:
        self.assertEqual(median([4, 1, 3, 2]), 2.5)
"""

COMMAND = "python -m unittest discover -s tests -t ."

ANSWERS: dict[str, dict[str, Any]] = {
    "draft": {
        "title": "Přidat median() do calc",
        "goal": "Přidej median() do calc/stats.py ve stylu mean() a otestuj ji.",
        "allowed_paths": ["calc/*.py", "tests/*.py"],
        "acceptance_criteria": ["median() vrací prostřední hodnotu, u sudého počtu průměr"],
        "acceptance_commands": [COMMAND],
        "questions": [],
        "notes": "Testovací běh (fake gateway).",
    },
    "plan": {
        "summary": "Doplnit median() a jeden testovací soubor.",
        "steps": ["Přidat median() do calc/stats.py", "Přidat tests/test_median.py"],
        "files_to_touch": ["calc/stats.py", "tests/test_median.py"],
        "risks": [],
        "complexity": "trivial",
        "recommended_implementer": "worker",
        "questions": [],
    },
    "review": {
        "verdict": "accept",
        "summary": "median() odpovídá zadání a testy procházejí.",
        "findings": [],
        "criteria": [],
    },
    "memory": {
        "project_update": "- Přidána median() s testy (testovací běh).",
        "handoff_needed": False,
        "handoff_to": "claude",
        "handoff_topic": "",
        "handoff_done": [],
        "handoff_open": [],
        "handoff_files": [],
        "handoff_avoid": [],
    },
    "implement": {"summary": "Přidána median().", "files_changed": [], "concerns": []},
}


def _phase(props: dict[str, Any]) -> str:
    if "verdict" in props:
        return "review"
    if "project_update" in props:
        return "memory"
    if "steps" in props:
        return "plan"
    if "acceptance_commands" in props:
        return "draft"
    return "implement"


def _call(name: str, args: dict[str, Any], i: int = 0) -> dict[str, Any]:
    return {
        "id": f"call_{name}_{i}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }


class ScriptedGateway:
    """Answers from the request shape: the offered tools tell which phase is asking."""

    def health(self) -> dict[str, Any]:
        return {"reachable": True, "status": 200, "fake": True}

    def models(self, fresh: bool = False) -> set[str] | None:
        return None

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout_s: float | None = None,
    ) -> ChatResult:
        by_name = {t["function"]["name"]: t["function"] for t in tools or []}
        if "ping" in by_name:  # Settings: "Otestovat"
            calls = [_call("ping", {"ok": True})]
        elif "submit" in by_name:
            props = by_name["submit"].get("parameters", {}).get("properties", {})
            calls = [_call("submit", ANSWERS[_phase(props)])]
        else:  # the worker: write, check, finish
            turn = sum(1 for m in messages if m.get("role") == "assistant")
            if turn == 0:
                calls = [
                    _call("write_file", {"path": "calc/stats.py", "content": MEDIAN}, 1),
                    _call(
                        "write_file", {"path": "tests/test_median.py", "content": MEDIAN_TEST}, 2
                    ),
                ]
            elif turn == 1:
                calls = [_call("run_command", {"command": COMMAND})]
            else:
                calls = [_call("finish", {"summary": "Přidána median() s testem."})]
        return ChatResult(
            message={"role": "assistant", "content": "", "tool_calls": calls},
            finish_reason="tool_calls",
            requested_model=model,
            actual_model=model,
            provider="fake",
            tokens_in=100,
            tokens_out=50,
        )
