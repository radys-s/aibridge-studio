import { expect, test } from "@playwright/test";
import { join } from "node:path";

test("memory registers fixture project and personal remains disclosure blocked", async ({ page }) => {
  await page.goto("/#/memory");
  await expect(page.getByRole("heading", { name: "Projektová paměť" })).toBeVisible();
  await page.getByLabel("Cesta Git projektu").fill(join(process.env.STUDIO_E2E_TMP!, "calc"));
  await page.getByRole("button", { name: "Přidat projekt" }).click();
  await expect(page.getByRole("heading", { name: "Aktivní znalosti" })).toBeVisible();
  await page.getByRole("button", { name: "Osobní", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Osobní režim" })).toBeVisible();
  await expect(page.getByText("Cloud je zablokovaný:", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Schválit rozsah a zapnout hledání" }).click();
  await page.getByRole("button", { name: "Nová osobní relace" }).click();
  await expect(page.getByRole("heading", { name: "Osobní znalost (jen lokálně)" })).toBeVisible();
  await page.getByLabel("Krátké tvrzení").fill("Synthetic geometry practice objective");
  await page.getByRole("button", { name: "Navrhnout znalost" }).click();
  await page.getByRole("button", { name: "Schválit do šifrované paměti" }).click();
  await expect(page.getByRole("heading", { name: "Schválené osobní znalosti" })).toBeVisible();
  await page.reload();
  await expect(page.getByText("Synthetic geometry practice objective")).toBeVisible();
  await expect(page.getByRole("button", { name: "Připravit náhled pro cloud" })).toBeDisabled();
});

for (const width of [1440, 390]) {
  for (const theme of ["light", "dark"] as const) {
    test(`knowledge visual ${width} ${theme}`, async ({ page }, info) => {
      await page.setViewportSize({ width, height: 1000 });
      await page.emulateMedia({ reducedMotion: "reduce" });
      await page.addInitScript((t) => localStorage.setItem("studio-theme", t), theme);
      for (const view of ["memory", "personal"]) {
        await page.goto(`/#/${view}`);
        await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
        await page.keyboard.press("Tab");
        expect(await page.evaluate(() => document.activeElement?.tagName)).not.toBe("BODY");
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
        await page.screenshot({ path: info.outputPath(`${view}-${width}-${theme}.png`), fullPage: true });
      }
    });
  }
}
