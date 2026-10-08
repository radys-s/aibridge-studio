import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

const TMP = join(here, ".tmp");
const REPO = join(TMP, "calc");
const NOTE = join(TMP, "vault", "02 Projekty", "Calc.md");

test("settings: only Autopilot, privacy badges, test gateway is labelled", async ({ page }) => {
  await page.goto("/#/settings");
  await expect(page.getByRole("heading", { name: "Autopilot a modely" })).toBeVisible();
  await expect(page.getByText("testovací gateway")).toBeVisible();
  await expect(page.getByRole("cell", { name: "groq/openai/gpt-oss-120b" })).toBeVisible();
  await expect(page.getByText("bez tréninku").first()).toBeVisible();
  await expect(page.getByText("pod hranicí soukromí").first()).toBeVisible();
  // Claude/Codex are not offered anywhere in settings (ADR 0004); the one sentence that
  // says so is the only mention.
  const text = await page.locator("main").innerText();
  expect(text.match(/Claude|Codex/g)).toEqual(["Claude", "Codex"]);
  expect(text).toContain("Claude ani Codex přes Studio nejdou");

  // Key sources and the one-click probe (ADR 0005).
  await page.getByText("Kde vzít klíče a kam je vložit").click();
  await expect(page.getByRole("link", { name: "OmniRoute → Providers" })).toHaveAttribute(
    "href",
    "http://localhost:20128/dashboard/providers",
  );
  const row = page.getByRole("row").filter({ has: page.getByRole("cell", { name: "groq/openai/gpt-oss-120b" }) });
  await row.getByRole("button", { name: "Otestovat" }).click();
  await expect(row.getByText(/funguje, volá nástroje/)).toBeVisible();
  const blocked = page.getByRole("row").filter({ has: page.getByRole("cell", { name: "nvidia/moonshotai/kimi-k2.6" }) });
  await expect(blocked.getByRole("button", { name: "Otestovat" })).toBeDisabled();
});

test("composer: shared projects only, repo hint, privacy line", async ({ page }) => {
  await page.goto("/");
  const project = page.getByLabel("Projekt");
  await expect(project.locator("option", { hasText: "Calc" })).toHaveCount(1);
  await expect(project.locator("option", { hasText: "Tajné" })).toHaveCount(0);
  await project.selectOption({ label: "Calc" });
  await expect(page.locator(".repo-field input")).toHaveValue(REPO);
  await expect(page.getByText("Nepošle se").or(page.getByText("otevřené předávky"))).toBeVisible();
  await expect(page.locator(".privacy-line")).toContainText("Data jdou jen k poskytovatelům bez tréninku");
  await expect(page.locator(".privacy-line")).toContainText("Groq");
});

test("project run: accepted, memory written only after click, applied to a branch", async ({ page }) => {
  page.on("dialog", (d) => void d.accept());
  await page.goto("/");
  await page.getByLabel("Projekt").selectOption({ label: "Calc" });
  await expect(page.locator(".repo-field input")).toHaveValue(REPO);
  await page.getByLabel("Schválit návrh zadání automaticky").check();
  await page.getByRole("button", { name: "Spustit" }).click();

  // Wait on the API first, so a failure says where the run stopped instead of "not found".
  let last = "";
  await expect
    .poll(
      async () => {
        const runs = (await (await page.request.get("/api/runs")).json()) as { state: string; decision_reason: string | null }[];
        last = JSON.stringify(runs[0] ?? null);
        return runs[0]?.state;
      },
      { timeout: 90_000, message: () => `run did not reach accepted: ${last}` },
    )
    .toBe("accepted");
  const write = page.getByRole("button", { name: "Zapsat" }).first();
  await expect(write).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("Přijato").first()).toBeVisible();
  expect(readFileSync(NOTE, "utf8")).not.toContain("Přidána median()");
  // A new proposal supersedes the old one: only the latest offers Zapsat.
  const writesBefore = await page.getByRole("button", { name: "Zapsat" }).count();
  await page.getByRole("button", { name: "Navrhnout znovu" }).click();
  await expect(page.getByText("nahrazeno novějším návrhem").first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("button", { name: "Zapsat" })).toHaveCount(writesBefore);
  await expect(page.getByRole("button", { name: "Navrhnout znovu" })).toHaveCount(1);
  await page.getByRole("button", { name: "Zapsat" }).first().click();
  await expect(page.getByText("zapsáno").first()).toBeVisible();
  expect(readFileSync(NOTE, "utf8")).toContain("Přidána median() s testy (testovací běh).");

  await page.getByRole("button", { name: "Použít do repozitáře" }).click();
  await expect(page.getByText(/Ve větvi studio\//)).toBeVisible();
  const branches = execFileSync("git", ["branch", "--list", "studio/*"], { cwd: REPO, encoding: "utf8" });
  expect(branches).toMatch(/studio\//);
  const current = execFileSync("git", ["branch", "--show-current"], { cwd: REPO, encoding: "utf8" });
  expect(current.trim()).toBe("main");
});
