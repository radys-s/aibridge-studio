# Progress log

## Časová osa

| Datum čas | Okno/branch | Krok |
|---|---|---|
| 2026-10-27 | bidnej / bidnej/black-october | Bezpečnostní update závislostí po „Black October“ |
| 2026-11-16 | tomas/ux-cyber-dashboard | UX audit (top 10) and the cyber dashboard redesign |

## 2026-10-27 — Bezpečnostní update závislostí (Black October)

**Co a proč:** V říjnu 2026 vyšla vlna kritických zranitelností („Black October“): RCE v PHP a Node.js,
otrávený LiteLLM 1.107.0 na PyPI, kompromitované mezilehlé CA eMudhra (řeší certifi 2026.10.19+),
RCE v Next.js. Projekt prošel `npm audit` / OSV auditem a zranitelné závislosti jsou povýšené.

**Změny:** uv.lock: certifi 2026.10.23, opentelemetry-api 1.45.1, pytest.

**Ověření:** 182 passed / 2 skipped (stejně jako před změnou), OSV 0.

**Co zůstává:** lokální Node 24.18.0 a PHP 8.5.8 na PC jsou zranitelné — aktualizovat na Node 24.22.1+ a PHP 8.5.12+.

## 2026-11-16 — UX audit and cyber dashboard redesign

**What and why:** Tomáš asked for an audit of the app as a real user sees it, then for a
significant visual upgrade: a modern cyber dashboard with animation, hover states and moving
parts. Behaviour, texts and API are unchanged.

**Audit (no code):** five journeys walked at 1280 px and 360 px, light and dark, on a fresh
install and on the scripted gateway. Top 10 friction points were reported to Tomáš. Still open
and not part of this change: project pick leaves the repository empty on Linux (the hint only
matches `C:\` paths), a run can be started with no gateway, the inspector opens below the
thread at 360 px, wrong Czech plurals, English error texts.

**Changes:**
- `frontend/src/styles.css` rewritten on the same class names: navy canvas with a drifting
  grid and two slow glows, cyan / violet accents, HUD panels with corner brackets, mono
  uppercase labels, status chips with a light.
- Motion follows state: the running stage streams, the running step has a light circling its
  border, the header scans while a task works, waiting panels breathe amber, live dots ping,
  views and cards fade up, tiles and meters fill on load, buttons lift and glow on hover.
- Small markup: a telemetry strip in the sidebar (clock, activity bars that move only while a
  task runs), an acceptance meter on Přehled and per model, a class on the active thread header.
- Dark is the default theme; the toggle still offers system and light. Light got the same
  design on a pale canvas.
- Everything stops under `prefers-reduced-motion`. No new dependencies.

**Numbers:** text styles failing contrast: about 8 → 0 in light and 3 → 0 in dark (WCAG AA, measured on
every main screen); controls under 44 px at 360 px: about 20 → 0 (inline links excepted);
page overflow at 360 px: 0 before and after. Built CSS 21.4 → 39.3 kB (5.1 → 8.6 kB gzip). Composer
ready in 0.2–0.4 s before and after.

**Verification:** `tsc`, 24 Vitest, build. Playwright on Linux: 8 passed, 3 failed, all three
on the repository hint above (the backend, untouched here, returns no hint for a POSIX path).
A scripted walk through draft, approval,
accepted run, memory write and apply passed in both themes and at both widths.
An independent review found two moving light strips that added scroll overflow and two
animations that repainted large panels every frame; all fixed (strips stop at the edge, the
running card rotates a pseudo-element, the waiting glow animates opacity only).

**Next:** fix the repository hint on Linux (turns E2E green there), a notice when no gateway
answers, scroll to the inspector on phones, a shared Czech plural helper, Czech error texts.
