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
from studio.sandbox import Denied
from studio.vault import Vault, handoff_name

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
                f"Běh `{run_id}`: {title}\n\n" + proposal.project_update.strip()[:MAX_UPDATE_CHARS],
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
            f"Souvisí: {link} · běh Studia `{run_id}`",
            "",
            "**Co je hotové (ověřeno):**",
            *_bullets(proposal.handoff_done),
            "**Co zbývá / otevřené body:**",
            *_bullets(proposal.handoff_open),
            "**Soubory a místa (cesty, větve):**",
            *_bullets(proposal.handoff_files),
            "**Co nedělat:**",
            *_bullets(proposal.handoff_avoid),
            "",
            "## Odpověď",
        ]
        out.append(
            {
                "kind": "new",
                "path": handoff_name(topic, proposal.handoff_to, day),
                "content": "\n".join(body),
                "status": "proposed",
            }
        )
    return out


def fallback_proposal(
    goal: str,
    changed: list[str],
    commands: list[str],
    review_summary: str,
) -> MemoryProposal:
    """Studio's own proposal from run facts, used when the model does not deliver one.

    Only facts Studio checked itself: changed files, the acceptance commands that passed,
    and the review summary. Nothing is invented, so it is safe to show and edit.
    """
    files = changed[:MAX_ITEMS]
    review = " ".join(review_summary.split())[:400]
    done = [f"Přijato po nezávislém ověření: {c}" for c in commands[:5]] or [
        "Přijato po nezávislém ověření (bez akceptačních příkazů)"
    ]
    lines = [f"- Cíl: {' '.join(goal.split())[:300]}"]
    if files:
        lines.append("- Změněné soubory: " + ", ".join(f"`{f}`" for f in files))
    lines += [f"- {d}" for d in done]
    if review:
        lines.append(f"- Recenze: {review}")
    lines.append("- Další krok: zkontrolovat změnu (vznikla na free modelech) a sloučit.")
    return MemoryProposal(
        project_update="\n".join(lines),
        handoff_needed=True,
        handoff_to="claude",
        handoff_topic=" ".join(goal.split())[:80],
        handoff_done=done + [f"Recenze: {review}"] * bool(review),
        handoff_open=["Zkontrolovat změnu z AI Bridge Studia a rozhodnout o sloučení větve."],
        handoff_files=files,
        handoff_avoid=["Nepřepisovat změnu bez přečtení recenze a výsledků testů."],
    )


def _bullets(items: list[str]) -> list[str]:
    kept = [i.strip()[:MAX_ITEM_CHARS] for i in items if i.strip()][:MAX_ITEMS]
    return [f"- {i}" for i in kept] or ["- —"]
