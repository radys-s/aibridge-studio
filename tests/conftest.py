from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from studio.agents.leads import AgentEvent, LeadError
from studio.agents.prompts import DRAFT_SCHEMA, IMPLEMENT_SCHEMA, PLAN_SCHEMA, REVIEW_SCHEMA
from studio.config import ModelSpec
from studio.db import Database
from studio.domain import Plan, Review, TaskSpec
from studio.gateway import ChatResult, GatewayError
from studio.routing import ModelRouter
from studio.runner import Runner

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic_repo"
MODELS = (
    ModelSpec("test/model-a", frozenset({"coding", "tools"}), 2),
    ModelSpec("test/model-b", frozenset({"coding", "tools"}), 1),
)

MEDIAN_OK = '''"""Small statistics helpers."""


def mean(values: list[float]) -> float:
    """Arithmetic mean. Raises ValueError for an empty list."""
    if not values:
        raise ValueError("mean() of empty list")
    return sum(values) / len(values)


def median(values: list[float]) -> float:
    if not values:
        raise ValueError("median() of empty list")
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2
'''

MEDIAN_BROKEN = MEDIAN_OK.replace("(s[mid - 1] + s[mid]) / 2", "s[mid]")

MEDIAN_TEST = """import unittest

from calc.stats import median


class MedianTest(unittest.TestCase):
    def test_odd(self) -> None:
        self.assertEqual(median([3, 1, 2]), 2)

    def test_even(self) -> None:
        self.assertEqual(median([4, 1, 3, 2]), 2.5)
"""


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    dest = tmp_path / "source"
    shutil.copytree(FIXTURE, dest)
    git(dest, "init", "-q", "-b", "main")
    git(dest, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    git(dest, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    return dest


def make_spec(repo: Path, **kw: Any) -> TaskSpec:
    data: dict[str, Any] = {
        "title": "Add median",
        "goal": "Add median(values) to calc/stats.py with unit tests.",
        "repo_path": str(repo),
        "allowed_paths": ["calc/*.py", "tests/*.py"],
        "acceptance_criteria": ["median works for odd and even lengths", "tests pass"],
        "acceptance_commands": ["python -m unittest discover -s tests -t ."],
        "planner": "claude",
        "reviewer": "codex",
    }
    data.update(kw)
    return TaskSpec.model_validate(data)


def tool_call(name: str, args: dict[str, Any], i: int = 0) -> dict[str, Any]:
    return {
        "id": f"call_{name}_{i}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def assistant(*calls: dict[str, Any], content: str = "") -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = list(calls)
    return msg


class FakeGateway:
    """Scripted gateway. Each script item is a message dict or a GatewayError to raise.

    `per_model` lets a test make one model fail while others answer.
    """

    def __init__(
        self,
        script: list[dict[str, Any] | GatewayError] | None = None,
        per_model: dict[str, GatewayError] | None = None,
        hook: Callable[[int], None] | None = None,
    ) -> None:
        self.script = list(script or [])
        self.per_model = per_model or {}
        self.calls: list[str] = []
        self.hook = hook

    def health(self) -> dict[str, Any]:
        return {"reachable": True, "status": 200}

    def chat(self, model, messages, tools=None, timeout_s=None):  # type: ignore[no-untyped-def]
        self.calls.append(model)
        if self.hook:
            self.hook(len(self.calls))
        if model in self.per_model:
            raise self.per_model[model]
        if not self.script:
            raise AssertionError("gateway script exhausted")
        item = self.script.pop(0)
        if isinstance(item, GatewayError):
            raise item
        return ChatResult(
            message=item,
            finish_reason="tool_calls",
            requested_model=model,
            actual_model=model.split("/")[-1] + "-actual",
            provider="fakeprov",
            tokens_in=100,
            tokens_out=20,
            cost_usd=0.0,
            latency_ms=5,
        )


class FakeLead:
    """Scripted lead. Answers by schema; `errors` maps a call kind to a LeadError to raise.

    `implement` is called with the working copy for write-mode calls (takeover).
    """

    def __init__(
        self,
        name: str,
        reviews: list[Review] | None = None,
        plans: list[Plan] | None = None,
        drafts: list[dict[str, Any]] | None = None,
        implement: Callable[[Path], None] | None = None,
        errors: dict[str, LeadError] | None = None,
    ) -> None:
        self.name = name
        self.reviews = list(reviews or [])
        self.plans = list(plans or [])
        self.drafts = list(drafts or [])
        self.implement = implement
        self.errors = dict(errors or {})
        self.review_prompts: list[str] = []
        self.calls: list[tuple[str, str, str | None, str | None]] = []  # kind, mode, model, effort

    def available(self) -> dict[str, Any]:
        return {"installed": True}

    def ask(self, prompt, schema, cwd, ctx, mode="read"):  # type: ignore[no-untyped-def]
        kind = {
            id(DRAFT_SCHEMA): "draft",
            id(PLAN_SCHEMA): "plan",
            id(REVIEW_SCHEMA): "review",
            id(IMPLEMENT_SCHEMA): "implement",
        }[id(schema)]
        self.calls.append((kind, mode, ctx.config.model, ctx.config.effort))
        ctx.on_event(AgentEvent("text", f"{self.name} {kind}"))
        ctx.usage.actual_model = f"{self.name}-model"
        ctx.usage.tokens_in, ctx.usage.tokens_out = 1000, 100
        if kind in self.errors:
            raise self.errors.pop(kind)
        if kind == "draft":
            return self.drafts.pop(0)
        if kind == "plan":
            if self.plans:
                return self.plans.pop(0).model_dump()
            return Plan(
                summary=f"Add median ({self.name})",
                steps=["edit stats", "add test"],
                complexity="trivial",
            ).model_dump()
        if kind == "review":
            self.review_prompts.append(prompt)
            return self.reviews.pop(0).model_dump()
        assert mode == "write"
        if self.implement:
            self.implement(cwd)
        return {"summary": f"{self.name} implemented", "files_changed": [], "concerns": []}


ACCEPT = Review(verdict="accept", summary="ok", findings=[], criteria=[])


def good_script() -> list[dict[str, Any] | GatewayError]:
    return [
        assistant(tool_call("read_file", {"path": "calc/stats.py"})),
        assistant(
            tool_call("write_file", {"path": "calc/stats.py", "content": MEDIAN_OK}, 1),
            tool_call("write_file", {"path": "tests/test_median.py", "content": MEDIAN_TEST}, 2),
        ),
        assistant(
            tool_call("run_command", {"command": "python -m unittest discover -s tests -t ."})
        ),
        assistant(tool_call("finish", {"summary": "added median"})),
    ]


@pytest.fixture
def studio(tmp_path: Path):  # type: ignore[no-untyped-def]
    def build(
        gateway: FakeGateway, reviews: list[Review], claude: FakeLead | None = None
    ) -> tuple[Runner, Database, FakeLead]:
        db = Database(tmp_path / "data" / "studio.sqlite")
        reviewer = FakeLead("codex", reviews)
        runner = Runner(
            db,
            gateway,
            ModelRouter(db, MODELS),
            {"claude": claude or FakeLead("claude"), "codex": reviewer},
            tmp_path / "data" / "ws",
        )
        return runner, db, reviewer

    return build
