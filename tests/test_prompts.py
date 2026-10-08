"""Every lead prompt keeps the agent inside the working copy."""

from __future__ import annotations

from studio.agents import prompts
from studio.domain import Draft, Plan, TaskSpec


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


def test_draft_notes_may_come_as_a_list() -> None:
    # Groq rejects the whole tool call when arguments do not match the schema (live
    # 2026-10-08: gpt-oss sent notes as an array), so the schema allows both forms.
    assert prompts.DRAFT_SCHEMA["properties"]["notes"] == {
        "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]
    }
    base = {"title": "t", "goal": "g", "allowed_paths": [], "acceptance_criteria": []}
    draft = Draft.model_validate({**base, "acceptance_commands": [], "notes": ["a", "b"]})
    assert draft.notes == "a\nb"
