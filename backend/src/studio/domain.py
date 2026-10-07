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

LeadName = Literal["claude", "codex"]
LEADS: tuple[LeadName, LeadName] = ("claude", "codex")
# Studio's own orchestrator on free models (see ADR 0003). Not a CLI: no model/effort config.
AUTOPILOT: Literal["autopilot"] = "autopilot"
AnyLead = Literal["autopilot", "claude", "codex"]
Effort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]
# Values each CLI accepts. Claude: `claude --help` 2.1.269. Codex: API error message for an
# invalid `model_reasoning_effort`, gpt-5.6-sol via ChatGPT login, verified 2026-10-06.
EFFORTS: dict[str, tuple[str, ...]] = {
    "claude": ("low", "medium", "high", "xhigh", "max"),
    "codex": ("none", "minimal", "low", "medium", "high", "xhigh", "max"),
}
# Above `high` the subscription limits drain fast; the UI asks before using these.
HEAVY_EFFORTS = frozenset({"xhigh", "max"})
Implementer = Literal["auto", "worker", "freebuff", "autopilot", "claude", "codex"]
Escalation = Literal["off", "ask", "auto"]


def other_lead(name: str) -> LeadName:
    return "codex" if name == "claude" else "claude"


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


class LeadConfig(BaseModel):
    """Model and effort for one lead CLI. None = the CLI's own default."""

    model: str | None = Field(None, max_length=100, pattern=r"^[A-Za-z0-9._:/\[\]-]+$")
    effort: Effort | None = None


class Orchestration(BaseModel):
    """Who leads. The single place that decides which lead drafts, plans and reviews.

    autopilot (default): Studio's free-model orchestrator drafts, plans and reviews; the
        review runs on a different model than the implementation. `second_opinion` adds
        optional Claude/Codex reviews that are skipped when their limit is used up.
    claude / codex: one lead does everything (reduced independence, flagged in the UI).
    cross: `primary` drafts and plans, the other lead reviews.
    duo: both plan independently (primary merges), both review; accept needs both.
    The last three are kept for older runs and for use while subscriptions have quota.
    """

    mode: Literal["autopilot", "claude", "codex", "cross", "duo"] = "autopilot"
    primary: AnyLead = "autopilot"
    plan: bool = True
    second_opinion: list[LeadName] = Field(default_factory=list)
    claude: LeadConfig = Field(default_factory=lambda: LeadConfig())
    codex: LeadConfig = Field(default_factory=lambda: LeadConfig())

    @model_validator(mode="after")
    def _check(self) -> Orchestration:
        if self.mode == "claude" or self.mode == "codex" or self.mode == AUTOPILOT:
            self.primary = self.mode
        elif self.primary == AUTOPILOT:
            self.primary = "claude"  # cross/duo pair the two CLI leads
        self.second_opinion = (
            list(dict.fromkeys(self.second_opinion)) if self.mode == AUTOPILOT else []
        )
        for name in LEADS:
            effort = self.config(name).effort
            if effort and effort not in EFFORTS[name]:
                raise ValueError(f"{name} does not support effort '{effort}'")
        return self

    def config(self, name: str) -> LeadConfig:
        if name == "claude":
            return self.claude
        if name == "codex":
            return self.codex
        return LeadConfig()

    @property
    def solo(self) -> bool:
        return self.mode in LEADS

    def planners(self) -> list[AnyLead]:
        if not self.plan:
            return []
        if self.mode == "duo":
            return [self.primary, other_lead(self.primary)]
        return [self.primary]

    def reviewers(self, implementer: str | None = None) -> list[AnyLead]:
        """Required reviewers. Autopilot's optional second opinions are separate."""
        if self.mode == AUTOPILOT:
            return [AUTOPILOT]
        if self.mode in LEADS:
            return [self.primary]
        if self.mode == "duo":
            return [self.primary, other_lead(self.primary)]
        # cross: never let a lead review its own implementation alone.
        if implementer in LEADS:
            return [other_lead(implementer or "")]
        return [other_lead(self.primary)]

    def heavy(self) -> list[str]:
        used = {*self.planners(), *self.reviewers(), *self.second_opinion, self.primary}
        return [n for n in LEADS if n in used and self.config(n).effort in HEAVY_EFFORTS]


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
        "OmniRoute; freebuff = the user runs Freebuff by hand; claude/codex = lead implements",
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
        """Specs stored by Phase 1 used planner/reviewer and implementer=external."""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        planner = data.pop("planner", None)
        reviewer = data.pop("reviewer", None)
        data.pop("external_agent", None)
        if data.get("implementer") == "external":
            data["implementer"] = "freebuff"
        if (planner or reviewer) and "orchestration" not in data:
            reviewer = reviewer or "codex"
            if planner == reviewer:
                orch: dict[str, Any] = {"mode": reviewer}
            elif planner in (None, "none"):
                orch = {"mode": "cross", "primary": other_lead(reviewer), "plan": False}
            else:
                orch = {"mode": "cross", "primary": planner}
            data["orchestration"] = orch
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
