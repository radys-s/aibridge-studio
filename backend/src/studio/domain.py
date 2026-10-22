"""Domain types: task specification, budgets, run states, structured agent outputs."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


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
    # The lead drafted scope/criteria/commands from the user's prompt; the user approves.
    # Not active either: it waits for a person.
    AWAITING_APPROVAL = "awaiting_approval"


ACTIVE_STATES = frozenset(
    {RunState.QUEUED, RunState.PLANNING, RunState.WORKING, RunState.VERIFYING, RunState.REVIEWING}
)
TERMINAL_STATES = frozenset({RunState.ACCEPTED, RunState.ABANDONED})

# Studio's own orchestrator on free models (ADR 0003). Claude and Codex are never routed
# through Autopilot; Claude Code and Codex are separate, optional leads.
AUTOPILOT: Literal["autopilot"] = "autopilot"
AnyLead = Literal["autopilot"]
# Run history from before ADR 0004 still names these as authors and implementers.
LEGACY_LEADS = ("claude", "codex")
Implementer = Literal["auto", "worker", "freebuff", "autopilot"]
Escalation = Literal["off", "ask", "auto"]


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
    agent_timeout_s: int = Field(900, ge=10, le=7200)
    max_escalations: int = Field(1, ge=0, le=3)


class Orchestration(BaseModel):
    """Who leads: always Autopilot (ADR 0004). `plan` says whether a plan step runs.

    Specs stored before ADR 0004 (modes claude/codex/cross/duo, second opinions, CLI
    model and effort) load as Autopilot; their other fields are dropped.
    """

    mode: Literal["autopilot"] = "autopilot"
    primary: AnyLead = "autopilot"
    plan: bool = True

    @model_validator(mode="before")
    @classmethod
    def _legacy(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {"plan": bool(data.get("plan", True))}
        return data

    def planners(self) -> list[AnyLead]:
        return [AUTOPILOT] if self.plan else []

    def reviewers(self) -> list[AnyLead]:
        return [AUTOPILOT]


class Message(BaseModel):
    author: Literal["user", "autopilot", "claude", "codex", "studio"]
    text: str


class TaskSpec(BaseModel):
    """What the user asks for. Before work starts it has a scope and checkable criteria.

    The user may give only `goal` (free text); the lead then drafts title, scope, criteria
    and commands, and the user approves them before anything is planned or changed.
    """

    title: str = Field("", max_length=200)
    goal: str = Field(min_length=3, max_length=20_000)
    repo_path: str
    allowed_paths: list[str] = Field(
        default_factory=list, description="Glob patterns the implementer may change"
    )
    acceptance_criteria: list[str] = Field(default_factory=list)
    acceptance_commands: list[str] = Field(
        default_factory=list, description="Run by Studio, not the implementer"
    )
    orchestration: Orchestration = Field(default_factory=lambda: Orchestration())
    implementer: Implementer = Field(
        "auto",
        description="auto = cheap worker, escalated by policy; worker = tool loop via "
        "OmniRoute; freebuff = the user runs Freebuff by hand; autopilot = lead implements",
    )
    escalation: Escalation = "ask"
    auto_approve: bool = Field(False, description="skip approval of a draft without questions")
    project_note: str | None = Field(
        None,
        max_length=300,
        description="vault note (e.g. '02 Projekty/X.md') this task continues; Studio sends "
        "it and the project's open handoffs to the lead, filtered by the vault policy",
    )
    budget: Budget = Field(default_factory=lambda: Budget())

    @model_validator(mode="before")
    @classmethod
    def _legacy(cls, data: Any) -> Any:
        """Older specs: Phase 1 planner/reviewer and implementer=external; before ADR 0004
        claude/codex implementers. All of them continue as Autopilot runs."""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        planner = data.pop("planner", None)
        data.pop("reviewer", None)
        data.pop("external_agent", None)
        if data.get("implementer") == "external":
            data["implementer"] = "freebuff"
        if data.get("implementer") in LEGACY_LEADS:
            data["implementer"] = AUTOPILOT
        if planner == "none" and "orchestration" not in data:
            data["orchestration"] = {"plan": False}
        return data

    @field_validator("allowed_paths", "acceptance_criteria", "acceptance_commands")
    @classmethod
    def _non_blank(cls, items: list[str]) -> list[str]:
        return [i.strip() for i in items if i.strip()]

    @field_validator("goal", "title", mode="before")
    @classmethod
    def _strip(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _title(self) -> TaskSpec:
        if not self.title.strip():
            self.title = self.goal.strip().splitlines()[0][:80]
        return self

    def missing(self) -> list[str]:
        return [
            name
            for name in ("allowed_paths", "acceptance_criteria", "acceptance_commands")
            if not getattr(self, name)
        ]

    @property
    def complete(self) -> bool:
        return not self.missing()


class Draft(BaseModel):
    title: str
    goal: str
    allowed_paths: list[str]
    acceptance_criteria: list[str]
    acceptance_commands: list[str]
    questions: list[str] = Field(default_factory=list)
    notes: str = ""

    @field_validator("notes", mode="before")
    @classmethod
    def _join_notes(cls, value: Any) -> Any:
        if isinstance(value, list):
            return "\n".join(str(v) for v in value)
        return value


class ImplementReport(BaseModel):
    summary: str
    files_changed: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    summary: str
    steps: list[str]
    files_to_touch: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    complexity: Literal["trivial", "moderate", "hard"] = "moderate"
    recommended_implementer: Literal["worker", "strong_agent"] = "worker"
    questions: list[str] = Field(default_factory=list)


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


class MemoryProposal(BaseModel):
    project_update: str = ""
    handoff_needed: bool = False
    handoff_to: Literal["claude", "codex"] = "claude"
    handoff_topic: str = ""
    handoff_done: list[str] = Field(default_factory=list)
    handoff_open: list[str] = Field(default_factory=list)
    handoff_files: list[str] = Field(default_factory=list)
    handoff_avoid: list[str] = Field(default_factory=list)
