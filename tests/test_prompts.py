"""Every lead prompt keeps the agent inside the working copy."""

from __future__ import annotations

from studio.agents import prompts
from studio.domain import Plan, TaskSpec


def test_every_lead_prompt_confines_the_agent_to_the_repository() -> None:
    spec = TaskSpec(goal="add f()", repo_path=".")
    plan = Plan(summary="s", steps=["a"])
    verification = {"passed": True, "changed_files": [], "out_of_scope": [], "commands": []}
    texts = [
        prompts.draft_prompt(spec, []),
        prompts.plan_prompt(spec, []),
        prompts.implement_prompt(spec, plan, [], []),
        prompts.review_prompt(spec, "", verification, "", "worker"),
    ]
    assert all(prompts._CONFINE in t for t in texts)
