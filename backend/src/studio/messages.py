"""Text Studio writes for the user (events, messages, memory notes) in English or Czech.

The English text is the message id and `CS` maps it to Czech; `{name}` placeholders are
filled from keyword arguments. The language is the user's preference at write time, so stored
text never changes afterwards. Prompts stay in English (prompts.py); the chat history a lead
sees carries the user's language, like the user's own messages.

Known gap: decision reasons and agent/exception details are still English only.
"""

from __future__ import annotations

from typing import Literal

Language = Literal["en", "cs"]

CS: dict[str, str] = {
    # Run lifecycle (db.py, runner.py)
    "Task received and queued": "Úkol přijat a zařazen do fronty",
    "Run interrupted by a restart; waiting to continue": (
        "Běh přerušen restartem; čeká na pokračování"
    ),
    "Brief approved automatically": "Zadání schváleno automaticky",
    "The user asked to interrupt": "Uživatel požádal o přerušení",
    "Continuing from phase {phase}": "Pokračování od fáze {phase}",
    "The user sent the task back for another fix": "Uživatel vrátil úkol k další opravě",
    "Retrying phase {phase}": "Opakuji fázi {phase}",
    "The user answered; the lead continues": "Odpověď uživatele; lead pokračuje",
    "The user approved the brief": "Uživatel schválil zadání",
    "{name} takes over the implementation (by hand)": "Implementaci přebírá {name} (ručně)",
    "{name} takes over the implementation.": "Implementaci přebírá {name}.",
    "The user kept the cheap worker": "Uživatel ponechal levného workera",
    "The user confirmed a change approved only by self-review": (
        "Uživatel potvrdil změnu schválenou jen sebe-review"
    ),
    "Written to memory: {path}": "Zapsáno do paměti: {path}",
    "The change is in branch {branch}": "Změna je ve větvi {branch}",
    "The change is in the repository as branch `{branch}` (commit {commit}). "
    "Your current branch is unchanged; merging is up to you.": (
        "Změna je v repozitáři jako větev `{branch}` (commit {commit}). "
        "Tvoje aktuální větev zůstala beze změny; sloučení je na tobě."
    ),
    "Proposal discarded: {path}": "Návrh zahozen: {path}",
    "The user confirmed the files found are not secret": (
        "Uživatel potvrdil, že nalezené soubory nejsou tajné"
    ),
    "The user abandoned the task": "Uživatel úkol opustil",
    "The user reported that {agent} finished; verifying": (
        "Uživatel ohlásil konec práce {agent}; ověřuji"
    ),
    "Terminal opened for {agent}": "Otevřen terminál pro {agent}",
    "Run interrupted; it can continue or be abandoned": "Běh přerušen; lze pokračovat nebo opustit",
    "Unexpected error: {error}": "Neočekávaná chyba: {error}",
    "Isolated working copy from commit {commit}": "Izolovaná pracovní kopie z commitu {commit}",
    "Memory notes sent to the model: {notes}": "Poznámky z paměti poslané modelu: {notes}",
    "{name} drafts the brief": "{name} navrhuje zadání",
    "Studio will not run these commands: {commands}": "Studio tyto příkazy nespustí: {commands}",
    "The lead has questions about the brief": "Lead má otázky k zadání",
    "The brief waits for approval": "Zadání čeká na schválení",
    "and": "a",
    "{names} prepare the plan": "{names} připravuje plán",
    "Before the implementation I need answers to:": "Před implementací potřebuji odpovědět:",
    "The plan recommends a strong agent: {lead}": "Plán doporučuje silného agenta: {lead}",
    "The iteration checkpoint was not written; the work stays": (
        "Checkpoint iterace nebyl zapsán; práce zůstává"
    ),
    "The worker starts iteration {n}": "Worker začíná iteraci {n}",
    "The worker ended with status {status}; verifying": "Worker skončil stavem {status}; ověřuji",
    "{why}; {lead} takes over the implementation": "{why}; implementaci přebírá {lead}",
    "The cheap worker was not enough ({why}). {lead} takes over.": (
        "Levný worker nestačil ({why}). Přebírá {lead}."
    ),
    "Waiting for {agent}: start it in the working copy, then click Done": (
        "Čeká na {agent}: spusť ho v pracovní kopii a pak klikni Hotovo"
    ),
    "Independent verification passed": "Nezávislé ověření prošlo",
    "Independent verification failed": "Nezávislé ověření selhalo",
    "{names} review the diff independently": "{names} nezávisle reviduje diff",
    "No other free model is free; review with the same model": (
        "Jiný free model není volný; review stejným modelem"
    ),
    "{name} answered accept but has blocking findings": (
        "{name} napsal accept, ale má blokující nálezy"
    ),
    "The review accepted the change ({names})": "Review přijalo změnu ({names})",
    "Autopilot gave no memory proposal ({error}); Studio built it from verified facts": (
        "Autopilot návrh do paměti nedodal ({error}); Studio ho sestavilo z ověřených faktů"
    ),
    "The memory proposal is ready; it is written only after approval": (
        "Návrh zápisu do paměti je připravený; zapíše se až po schválení"
    ),
    "The memory proposal failed: {error}": "Návrh zápisu do paměti selhal: {error}",
    "{why}; next iteration {n}": "{why}; další iterace {n}",
    # Brief shown in the thread (runner.draft_message)
    "Brief: **{title}**": "Návrh zadání: **{title}**",
    "May change:": "Smí měnit:",
    "Criteria:": "Kritéria:",
    "Verification commands:": "Ověřovací příkazy:",
    "Questions:": "Otázky:",
    # Memory notes (memory.py)
    "Run `{run_id}`: {title}": "Běh `{run_id}`: {title}",
    "Related: {link} · Studio run `{run_id}`": "Souvisí: {link} · běh Studia `{run_id}`",
    "**What is done (verified):**": "**Co je hotové (ověřeno):**",
    "**What remains / open points:**": "**Co zbývá / otevřené body:**",
    "**Files and places (paths, branches):**": "**Soubory a místa (cesty, větve):**",
    "**What not to do:**": "**Co nedělat:**",
    "## Answer": "## Odpověď",
    "Accepted after independent verification: {command}": (
        "Přijato po nezávislém ověření: {command}"
    ),
    "Accepted after independent verification (no acceptance commands)": (
        "Přijato po nezávislém ověření (bez akceptačních příkazů)"
    ),
    "Goal: {goal}": "Cíl: {goal}",
    "Changed files: {files}": "Změněné soubory: {files}",
    "Review: {summary}": "Recenze: {summary}",
    "Next step: check the change (made on free models) and merge it.": (
        "Další krok: zkontrolovat změnu (vznikla na free modelech) a sloučit."
    ),
    "Check the change from AI Bridge Studio and decide about merging the branch.": (
        "Zkontrolovat změnu z AI Bridge Studia a rozhodnout o sloučení větve."
    ),
    "Do not overwrite the change without reading the review and the test results.": (
        "Nepřepisovat změnu bez přečtení recenze a výsledků testů."
    ),
}


def tr(lang: str | None, text: str, **params: object) -> str:
    """`text` in `lang` (English when unknown), with `{name}` placeholders filled."""
    out = CS.get(text, text) if lang == "cs" else text
    return out.format(**params) if params else out
