import { expect, test } from "@playwright/test";
import { join } from "node:path";

const ARTIFACTS = join(process.env.STUDIO_E2E_TMP!, "screenshots");

test("project switching clears preview and repository before a delayed load; errors block submit", async ({ page }) => {
  const notes = ["02 Projekty/Alpha.md", "02 Projekty/Beta.md", "02 Projekty/Broken.md"];
  await page.route("**/api/vault", (route) => route.fulfill({ json: { available: true, projects: notes } }));
  let finishBeta: (() => void) | undefined;
  await page.route("**/api/vault/project?*", async (route) => {
    const note = new URL(route.request().url()).searchParams.get("note")!;
    if (note.endsWith("Broken.md")) return route.fulfill({ status: 400, json: { detail: "Poznámka není dostupná" } });
    if (note.endsWith("Beta.md")) await new Promise<void>((resolve) => { finishBeta = resolve; });
    return route.fulfill({ json: { note, title: note.split("/").pop(), repo_hint: `C:/fixture/${note.includes("Alpha") ? "alpha" : "beta"}`, preview: `preview ${note}`, handoffs: [], refused: [] } });
  });
  await page.goto("/");
  const picker = page.getByLabel("Projekt");
  const repo = page.locator(".repo-field input");
  const submit = page.getByRole("button", { name: "Spustit", exact: true });
  await picker.selectOption(notes[0]);
  await expect(repo).toHaveValue("C:/fixture/alpha");
  await picker.selectOption(notes[1]);
  await expect(repo).toHaveValue("");
  await expect(page.locator(".project-info")).toHaveCount(0);
  await expect(submit).toBeDisabled();
  await expect.poll(() => !!finishBeta).toBe(true);
  await picker.selectOption(notes[0]);
  await expect(repo).toHaveValue("C:/fixture/alpha");
  finishBeta!();
  await expect(repo).toHaveValue("C:/fixture/alpha");
  await picker.selectOption(notes[2]);
  await expect(page.getByRole("alert")).toContainText("Oprav výběr");
  await expect(submit).toBeDisabled();
  await page.getByRole("radio", { name: "Nové zadání" }).click();
  await expect(submit).toBeEnabled();
});

test("button and repeated Ctrl+Enter create one task, retaining explicit saved approval", async ({ page }) => {
  await page.route("**/api/vault", (route) => route.fulfill({ json: { available: false, projects: [] } }));
  await page.route("**/api/settings", async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    await route.fulfill({ json: { ...data, defaults: { ...data.defaults, auto_approve: true } } });
  });
  let calls = 0;
  let release: (() => void) | undefined;
  let sent: { auto_approve: boolean } | undefined;
  await page.route("**/api/tasks", async (route) => {
    calls++;
    sent = route.request().postDataJSON();
    await new Promise<void>((resolve) => { release = resolve; });
    await route.fulfill({ status: 400, json: { detail: "test response" } });
  });
  await page.goto("/");
  await expect(page.getByLabel("Schválit návrh zadání automaticky")).toBeChecked();
  await page.getByLabel("Zadání", { exact: true }).fill("Add median with tests");
  await page.locator(".repo-field input").fill("C:/fixture/calc");
  await page.getByRole("button", { name: "Spustit", exact: true }).click();
  await page.getByLabel("Zadání", { exact: true }).press("Control+Enter");
  await page.getByLabel("Zadání", { exact: true }).press("Control+Enter");
  await expect.poll(() => calls).toBe(1);
  expect(sent?.auto_approve).toBe(true);
  release!();
  await expect(page.getByRole("alert")).toContainText("test response");
});

test("manual draft approval remains the default", async ({ page }) => {
  let approved: { edits?: { acceptance_criteria?: string[] } } | undefined;
  await page.route("**/api/runs/*/approve", (route) => {
    approved = route.request().postDataJSON();
    return route.fulfill({ json: { ok: true } });
  });
  await page.route("**/api/settings", async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    await route.fulfill({ json: { ...data, defaults: { ...data.defaults, auto_approve: false } } });
  });
  await page.goto("/");
  await page.getByLabel("Projekt").selectOption({ label: "Calc" });
  await expect(page.getByLabel("Schválit návrh zadání automaticky")).not.toBeChecked();
  await page.getByRole("button", { name: "Spustit", exact: true }).click();
  await expect(page.getByRole("button", { name: "Schválit a plánovat" })).toBeVisible({ timeout: 30_000 });
  await expect(page.locator('.stage.waiting')).toContainText("Zadání");
  await page.getByRole("button", { name: "Schválit a plánovat" }).click();
  await expect.poll(() => approved?.edits?.acceptance_criteria?.length ?? 0).toBeGreaterThan(0);
  await page.getByRole("button", { name: "Diff", exact: true }).click();
  await expect(page.getByRole("tab", { name: "Diff", exact: true })).toBeFocused();
});

test("self-review waits for a decision and sensitive verification or review cannot be bypassed", async ({ page }) => {
  const criterion = "works";
  const fixture = {
    run: { id: "review-fixture", state: "needs_decision", phase: "review", iteration: 1, workspace: "C:/fixture/calc", source_commit: null, iteration_base: null, decision_reason: "Nezávislý model není dostupný.", cancel_requested: false, implementer: "worker", escalations: 0, pending: "self_review", sensitive_ok: false, created_at: 1, updated_at: 1, finished_at: null, feedback: [], plan: null,
      spec: { title: "Review fixture", goal: "Improve calc", repo_path: "C:/fixture/calc", allowed_paths: ["calc/*.py"], acceptance_criteria: [criterion], acceptance_commands: [], orchestration: { mode: "autopilot", primary: "autopilot", plan: true }, implementer: "worker", escalation: "ask", auto_approve: false } },
    steps: [{ id: 1, kind: "review", iteration: 1, status: "done", actor: "autopilot", output: { verdict: "accept", summary: "ok", findings: [], criteria: [{ criterion, met: true, evidence: "test" }], independent: false, acceptance_valid: true }, error: null, started_at: 1, finished_at: 2 }],
    events: [], messages: [], agent_events: [], model_calls: [], usage: { api: { tokens: 0, cost: 0, calls: 0 }, subscription: { tokens: 0, cost: 0, calls: 0 }, total: { tokens: 0, cost: 0, calls: 0 } },
  };
  await page.route("**/api/runs/review-fixture", (route) => route.fulfill({ json: fixture }));
  await page.route("**/api/runs/review-fixture/stream?*", (route) => route.fulfill({ contentType: "text/event-stream", body: ": fixture\n\n" }));
  await page.goto("/#/run/review-fixture");
  await expect(page.getByRole("button", { name: "Přijmout změnu", exact: true })).toBeVisible();
  await expect(page.locator('.stage.waiting')).toContainText("Recenze");
  for (const width of [1440, 390]) {
    for (const theme of ["light", "dark"]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.evaluate((value) => document.documentElement.setAttribute("data-theme", value), theme);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await page.screenshot({ path: join(ARTIFACTS, `visual-review-${width}-${theme}.png`), fullPage: true });
    }
  }
  for (const phase of ["work", "verify", "review"]) {
    fixture.run.phase = phase;
    fixture.run.pending = "sensitive";
    fixture.run.decision_reason = "Výstup obsahuje citlivé údaje.";
    await page.reload();
    await expect(page.getByText("Výstup obsahuje citlivé údaje.", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Nejsou tajné, pokračovat" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Přijmout změnu", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Zkontrolovat znovu" })).toBeVisible();
  }
  fixture.run.phase = "draft";
  await page.reload();
  await expect(page.getByRole("button", { name: "Nejsou tajné, pokračovat" })).toBeVisible();
});

for (const width of [1440, 390]) {
  for (const theme of ["light", "dark"]) {
    test(`visual fixture ${width}px ${theme}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 1000 });
      await page.emulateMedia({ reducedMotion: "reduce" });
      await page.addInitScript((value) => localStorage.setItem("studio-theme", value), theme);
      await page.goto("/");
      await page.getByLabel("Projekt").selectOption({ label: "Calc" });
      await expect(page.getByRole("button", { name: "Spustit", exact: true })).toBeEnabled();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await page.screenshot({ path: join(ARTIFACTS, `visual-composer-${width}-${theme}.png`), fullPage: true });
      await page.goto("/#/settings");
      await expect(page.getByRole("heading", { name: "Modely", exact: true })).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await page.screenshot({ path: join(ARTIFACTS, `visual-settings-${width}-${theme}.png`), fullPage: true });
    });
  }
}
