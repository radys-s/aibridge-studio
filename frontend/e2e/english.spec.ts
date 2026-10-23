import { expect, test } from "@playwright/test";

// The other specs run in Czech (playwright.config.ts); this one checks the English default
// and the language switch in Settings.
test.use({ locale: "en-US" });

test("english: the UI follows the browser language and switches in Settings", async ({ page }) => {
  await page.goto("/#/settings");
  await expect(page.getByRole("heading", { name: "Settings", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Models", exact: true })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.getByRole("button", { name: "New task" })).toBeVisible();

  const language = page.locator("section", { has: page.getByRole("heading", { name: "Language" }) });
  await language.getByRole("combobox").selectOption("cs");
  await expect(page.getByRole("heading", { name: "Nastavení", exact: true })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "cs");
  const settings = await (await page.request.get("/api/settings")).json();
  expect(settings.language).toBe("cs");

  await page.locator("section", { has: page.getByRole("heading", { name: "Jazyk" }) }).getByRole("combobox").selectOption("en");
  await expect(page.getByRole("heading", { name: "Settings", exact: true })).toBeVisible();
});
