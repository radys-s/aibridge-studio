"""Domain types: task specification, budgets, run states, structured agent outputs."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class RunState(StrEnum):
    QUEUED = "queued"
    PLANNING = "planning"
    WORKING = "working"
    VERIFYING = "verifying"
    REVIEWING = "reviewing"
    ACCEPTED = "accepted"
    NEEDS_DECISION = "needs_decision"
    INTERRUPTED = "interrupted"
    ABANDONED = "abandoned"
    # The working copy is handed to an external agent the user drives by hand (Freebuff).
    # Not active: nothing runs in Studio, so a restart leaves it as it is.
    AWAITING_EXTERNAL = "awaiting_external"


ACTIVE_STATES = frozenset(
    {RunState.QUEUED, RunState.PLANNING, RunState.WORKING, RunState.VERIFYING, RunState.REVIEWING}
)
TERMINAL_STATES = frozenset({RunState.ACCEPTED, RunState.ABANDONED})


class StepKind(StrEnum):
    PLAN = "plan"
    WORK = "work"
    VERIFY = "verify"
    REVIEW = "review"


class StepStatus(StrEnum):
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class Budget(BaseModel):
    max_iterations: int = Field(2, ge=1, le=5)
    max_worker_turns: int = Field(30, ge=1, le=200)
    max_worker_seconds: int = Field(900, ge=10, le=7200)
    max_tokens: int = Field(400_000, ge=1_000)
    max_cost_usd: float = Field(0.50, ge=0)
    command_timeout_s: int = Field(120, ge=1, le=1800)
    agent_timeout_s: int = Field(600, ge=10, le=3600)


class TaskSpec(BaseModel):
    """What the user asks for. Every task has a goal, a scope and checkable criteria."""

    title: str = Field(min_length=3, max_length=200)
    goal: str = Field(min_length=10, max_length=20_000)
    repo_path: str
    allowed_paths: list[str] = Field(
        min_length=1, description="Glob patterns the worker may change"
    )
    acceptance_criteria: list[str] = Field(min_length=1)
    acceptance_commands: list[str] = Field(
        min_length=1, description="Run by Studio, not the worker"
    )
    planner: Literal["claude", "codex", "none"] = "claude"
    reviewer: Literal["claude", "codex"] = "codex"
    implementer: Literal["worker", "external"] = Field(
        "worker",
        description="worker = tool loop via OmniRoute; external = the user runs an agent "
        "(Freebuff) in the working copy by hand and tells Studio when it is done",
    )
    external_agent: Literal["freebuff"] = "freebuff"
    budget: Budget = Field(default_factory=lambda: Budget())

    @field_validator("allowed_paths", "acceptance_criteria", "acceptance_commands")
    @classmethod
    def _non_blank(cls, items: list[str]) -> list[str]:
        cleaned = [i.strip() for i in items if i.strip()]
        if not cleaned:
            raise ValueError("must contain at least one non-empty item")
        return cleaned


class Plan(BaseModel):
    summary: str
    steps: list[str]
    files_to_touch: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    complexity: Literal["trivial", "moderate", "hard"] = "moderate"
    recommended_implementer: Literal["worker", "strong_agent"] = "worker"


class Finding(BaseModel):
    severity: Literal["blocker", "major", "minor"]
    file: str = ""
    problem: str
    fix: str = ""


class CriterionAssessment(BaseModel):
    criterion: str
    met: bool
    evidence: str = ""


class Review(BaseModel):
    verdict: Literal["accept", "request_changes", "reject"]
    summary: str
    findings: list[Finding] = Field(default_factory=list)
    criteria: list[CriterionAssessment] = Field(default_factory=list)
