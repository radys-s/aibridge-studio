"""The lead protocol: who drafts, plans, reviews and (on takeover) implements.

Since ADR 0004 the only lead is Autopilot (`agents/api_lead.py`) on free models through
OmniRoute. The Claude Code and Codex CLI leads were removed: Tomáš uses those directly, and
Studio is the tool for after their limits run out.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ValidationError

Mode = Literal["read", "write", "submit"]  # submit = no tools but the final submit


class LeadError(Exception):
    def __init__(self, message: str, kind: str = "error") -> None:
        super().__init__(message)
        # error | timeout | not_installed | cancelled | bad_output | no_model | budget
        # | gateway_down
        self.kind = kind


@dataclass(frozen=True)
class AgentEvent:
    """One line of what a lead is doing, for the live view. Never fed back to a model."""

    kind: str  # text | tool | tool_result | status | error
    text: str
    data: dict[str, Any] | None = None


@dataclass
class LeadUsage:
    actual_model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


@dataclass
class CallContext:
    timeout_s: int
    should_stop: Callable[[], bool]
    on_event: Callable[[AgentEvent], None] = lambda _e: None
    usage: LeadUsage = field(default_factory=LeadUsage)
    # Autopilot records its own gateway calls and routes through the router.
    run_id: str | None = None
    step_id: int | None = None
    role: str = "lead"
    scope: tuple[str, ...] = ()
    complexity: str = "moderate"
    exclude_models: frozenset[str] = frozenset()
    max_tokens: int = 400_000
    max_cost_usd: float = 0.5


class Lead(Protocol):
    name: str

    def available(self) -> dict[str, Any]: ...

    def ask(
        self, prompt: str, schema: dict[str, Any], cwd: Path, ctx: CallContext, mode: Mode = ...
    ) -> dict[str, Any]: ...


def validate[T: BaseModel](model: type[T], payload: Any) -> T:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise LeadError(f"structured output invalid: {exc}", "bad_output") from exc
