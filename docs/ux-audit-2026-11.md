# UX audit, November 2026

Date: 2026-11-18. Build: `main` at 63e6dcd (after the cyber dashboard redesign, #10).
Goal: fewer steps, fewer errors and clearer screens for a real person; no new features.

## How it was tested

Studio ran on hub2 (Linux) from a scratch data directory, never against a real database:

- **Fresh install, no gateway:** OmniRoute not installed, no Refrakt token. First-time user.
- **Scripted gateway** (`STUDIO_FAKE_GATEWAY=1`) with the E2E fixture vault and repo: returning
  user who continues a project; full run from draft to accepted, memory proposal and apply.
- **Live Refrakt** (`REFRAKT_GATEWAY_TOKEN`, no OmniRoute): one small real task on a throwaway
  repo ("add `sub(a, b)` and a test"). Draft, plan, work and verification passed on
  `refrakt/groq/openai/gpt-oss-120b`; the review step then stopped the run (see F5).

Each journey was walked at 1280 × 800 and 360 × 740, dark (the default) and light, in the T3
browser preview. UI text was read as a native Czech speaker would.

## Journeys and steps today

| Journey | Steps today | Where it breaks |
|---|---|---|
| First start → first task, no gateway | type goal, type repo, Spustit → run fails in 64 ms | Nothing before the click says Studio cannot run; the failure is English and names no next step (F2, F5) |
| Continue a project on Linux | pick project, **type the full repo path**, Spustit | The repo hint only matches `C:\` paths (F3) |
| Open an existing task on a phone | not possible from the list | The task list is squeezed to a 14 px strip (F1) |
| Open the details (tests, review, models) on a phone | tap Podrobnosti, then scroll ~2,900 px (4–5 swipes) | The inspector opens below the whole thread (F4) |
| A run that needs a decision | read an English internal error, guess between 3–4 buttons | No Czech text, no recommended action (F5) |
| Overview, settings | readable | Wrong plurals, Refrakt invisible in the status (F6, F7) |

## Friction points, ranked by impact ÷ effort

Effort: S = under an hour, one or two files; M = a few hours or several components;
L = needs a design decision or backend/prompt work.

| # | Problem | Evidence | Impact | Effort |
|---|---|---|---|---|
| F1 | **Phone: the task list is invisible.** At ≤ 760 px the sidebar is capped at 45 vh and the brand, telemetry strip, button, search and footer take all of it; the list gets 14 px. The repo heading "// CALC" shows, the task under it does not. A returning user cannot open a task on a phone. | 360 × 740: `nav.run-list` box height 14 px, task row at y 227–271 hidden under the footer (y 232). | High | S |
| F2 | **A run can be started with no gateway.** With neither OmniRoute nor Refrakt answering, Spustit creates a run that fails at once ("drafting by autopilot failed: OmniRoute is not reachable: OmniRoute not reachable at …"). The composer only says "OmniRoute neběží" in small print and never what to do. | Fresh install: run `00b61d4c6a05` failed after 64 ms. | High | S |
| F3 | **Project pick leaves Repozitář empty on Linux.** `Vault.repo_hint` only matches Windows paths, so on hub2 the user types the full path by hand. The placeholder `C:\cesta\k\repu` is also Windows-only. Already listed as open in the 2026-11-16 audit; it keeps three Playwright tests red on Linux. | Note says `Repozitář: /tmp/…/calc`; field stays empty after picking "Calc". | High (hub use) | S |
| F4 | **Podrobnosti does nothing visible on a phone or narrow window.** Below 1100 px the inspector is stacked under the thread; at 360 px it opens ~2,900 px below the button. (Diff works: it focuses the Diff tab, which scrolls.) | 360 × 740, scrolled to the top: after tapping Podrobnosti `scrollY` stays 0 and `.inspector` top = 2,942 px; after Diff it is in view. | High (phone) | S |
| F5 | **Decision reasons are raw English internals with no next step.** Examples seen: "drafting by autopilot failed: OmniRoute is not reachable…", "all lead models failed, are excluded or are cooling down: … (cooldown)", "Review by autopilot: Review must assess every task criterion exactly once; missing or duplicate coverage." The bar then offers Zkusit fázi znovu / Vrátit k opravě / Předat silnějšímu modelu / Opustit with no hint which fits. | Live Refrakt run `d3baac969396` (the reviewer answered in English, so criteria did not match exactly). | High | M |
| F6 | **Sidebar status says the gateway is down when Refrakt works.** With only Refrakt, the sidebar shows a red "OmniRoute" dot and no Refrakt at all; its tooltip is the exception name "ConnectError". The composer status line does show "Refrakt běží". | Refrakt-only instance, sidebar footer. | Medium | S |
| F7 | **Wrong Czech plurals.** "1 záznamů", "2 záznamů", "3 nástrojů", "2 změněných souborů", "Groq: 1 dotazů dnes", "1 úkolů", "1 tvých rozhodnutí"; `DiffView` would print "0 soubory". Three separate ad-hoc plural rules exist. | Thread, Settings → Dnes, Přehled. | Medium (every run screen) | S |
| F8 | **Phone: the sidebar fills the first screen.** At 360 px the composer heading starts at y 333 and Spustit is below the fold; on a run, the header starts below the sidebar too. | 360 × 740 screenshots. | Medium | M |
| F9 | **Settings never mention Refrakt unless it is already set up.** There is no "Brána" section: how to connect Refrakt (`REFRAKT_GATEWAY_TOKEN`) is only in the README, and each model row repeats "OmniRoute nedostupný" nine times. | Settings on the fresh install. | Medium | M |
| F10 | **Model-written text stays English in a Czech UI.** Plan summary, worker summary and review reasoning came back in English; the plan line mixes "Složitost: trivial · doporučený implementátor: worker". | Live run `d3baac969396`. | Medium | L (prompts) |

Smaller notes, not ranked: footer and header buttons are 28 px high on desktop (44 px on phones,
fine); "8.7 s" and "$0.000" use a decimal point; the inspector tab bar is cut off at 1280 px
("UDÁLOST…"); fresh views fade in, so the first screenshot after navigation is empty for ~0.3 s.

Checked and fine: no horizontal overflow at 360 px; touch targets ≥ 44 px at 360 px; visible
focus styles exist; the empty-form error ("Napiš, co se má udělat, a vyber repozitář.") is clear;
the draft card ("Uprav, co nesedí, a schval") and the memory card explain themselves.

## Plan

Implement the top five, all S or M, as small PRs from `main`:

1. F1 + F4: phone layout (task list visible, inspector scrolls into view).
2. F3: repository hint for POSIX paths and a neutral placeholder.
3. F2 + F6: no-gateway notice in the composer and an honest sidebar status.
4. F5: Czech decision reasons with the recommended next step.
5. F7: one Czech plural helper.

F8–F10 are the next candidates.
