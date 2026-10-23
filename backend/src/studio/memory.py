"""Continuing a project from the shared memory, and proposing what to write back.

Reading: the project note plus the open handoffs that mention it, through `Vault` (so
personal and secret notes never leave). Writing: Autopilot only *proposes*; Studio turns the
proposal into exactly two kinds of note change (a dated append to the project note, a new
handoff) and writes one only when the user approves it.
"""

from __future__ import annotations

from datetime import date
from pathlib import PurePosixPath
from typing import Any

from studio.domain import MemoryProposal
from studio.messages import tr
from studio.sandbox import Denied
from studio.vault import Vault

MAX_HANDOFFS = 3
MAX_UPDATE_CHARS = 4_000
MAX_ITEMS = 15
MAX_ITEM_CHARS = 500


def project_memory(vault: Vault, note_rel: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Notes to send for a project: (path, text) pairs and paths refused by the policy.

    Raises Denied when the project note itself may not be shared.
    """
    note = vault.read(note_rel)
    notes = [(note.path, note.text)]
    key = PurePosixPath(note.path).stem.lower()
    refused: list[str] = []
    for rel in vault.open_handoffs():
        if len(notes) > MAX_HANDOFFS:
            break
        try:
            raw = (vault.root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if key not in rel.lower() and key not in raw.lower():
            continue
        try:
            handoff = vault.read(rel)
        except Denied:
            refused.append(rel)
            continue
        notes.append((handoff.path, handoff.text))
    return notes, refused


def build_proposals(
    vault: Vault,
    proposal: MemoryProposal,
    note_rel: str,
    run_id: str,
    title: str,
    today: date | None = None,
    author: str = "Autopilot",
    lang: str | None = None,
) -> list[dict[str, Any]]:
    day = (today or date.today()).isoformat()
    stem = PurePosixPath(note_rel).stem
    link = f"[[{vault.root.name}/{note_rel.removesuffix('.md')}|{stem}]]"
    out: list[dict[str, Any]] = []
    if proposal.project_update.strip():
        out.append(
            {
                "kind": "append",
                "path": note_rel,
                "content": f"## {day} — AI Bridge Studio ({author})\n"
                + tr(lang, "Run `{run_id}`: {title}", run_id=run_id, title=title)
                + "\n\n"
                + proposal.project_update.strip()[:MAX_UPDATE_CHARS],
                "status": "proposed",
            }
        )
    if proposal.handoff_needed:
        topic = (proposal.handoff_topic.strip() or title)[:120]
        body = [
            "---",
            "type: handoff",
            "from: studio",
            f"to: {proposal.handoff_to}",
            "status: open",
            f"date: {day}",
            "tags: [handoff]",
            "---",
            f"# {topic}",
            "",
            tr(lang, "Related: {link} · Studio run `{run_id}`", link=link, run_id=run_id),
            "",
            tr(lang, "**What is done (verified):**"),
            *_bullets(proposal.handoff_done),
            tr(lang, "**What remains / open points:**"),
            *_bullets(proposal.handoff_open),
            tr(lang, "**Files and places (paths, branches):**"),
            *_bullets(proposal.handoff_files),
            tr(lang, "**What not to do:**"),
            *_bullets(proposal.handoff_avoid),
            "",
            tr(lang, "## Answer"),
        ]
        out.append(
            {
                "kind": "new",
                "path": vault.handoff_name(topic, proposal.handoff_to, day),
                "content": "\n".join(body),
                "status": "proposed",
            }
        )
    return out


def clip(text: str, limit: int) -> str:
    """Collapse whitespace and shorten at a word boundary, marking the cut with an ellipsis."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    cut = flat[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:")
    return cut + "…"


def fallback_proposal(
    goal: str,
    changed: list[str],
    commands: list[str],
    review_summary: str,
    title: str = "",
    lang: str | None = None,
) -> MemoryProposal:
    """Studio's own proposal from run facts, used when the model does not deliver one.

    Only facts Studio checked itself: changed files, the acceptance commands that passed,
    and the review summary. Nothing is invented, so it is safe to show and edit.
    """
    files = changed[:MAX_ITEMS]
    review = clip(review_summary, 400)
    done = [
        tr(lang, "Accepted after independent verification: {command}", command=c)
        for c in commands[:5]
    ] or [tr(lang, "Accepted after independent verification (no acceptance commands)")]
    review_line = tr(lang, "Review: {summary}", summary=review)
    lines = ["- " + tr(lang, "Goal: {goal}", goal=clip(goal, 300))]
    if files:
        changed_files = ", ".join(f"`{f}`" for f in files)
        lines.append("- " + tr(lang, "Changed files: {files}", files=changed_files))
    lines += [f"- {d}" for d in done]
    if review:
        lines.append(f"- {review_line}")
    lines.append("- " + tr(lang, "Next step: check the change (made on free models) and merge it."))
    return MemoryProposal(
        project_update="\n".join(lines),
        handoff_needed=True,
        handoff_to="claude",
        handoff_topic=clip(title or goal, 80),
        handoff_done=done + [review_line] * bool(review),
        handoff_open=[
            tr(lang, "Check the change from AI Bridge Studio and decide about merging the branch.")
        ],
        handoff_files=files,
        handoff_avoid=[
            tr(
                lang,
                "Do not overwrite the change without reading the review and the test results.",
            )
        ],
    )


def _bullets(items: list[str]) -> list[str]:
    kept = [clip(i, MAX_ITEM_CHARS) for i in items if i.strip()][:MAX_ITEMS]
    return [f"- {i}" for i in kept] or ["- —"]
