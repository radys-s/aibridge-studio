import { expect, test } from "@playwright/test";
import { join } from "node:path";

const REPO = join(process.env.STUDIO_E2E_TMP!, "calc");

test.use({ viewport: { width: 360, height: 740 } });

test("phone: the task list shows tasks and Podrobnosti brings the details into view", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("radio", { name: "Nové zadání" }).click();
  await page.getByLabel("Zadání", { exact: true }).fill("Telefon: přidej median()");
  await page.locator(".repo-field input").fill(REPO);
  await page.getByRole("button", { name: "Spustit" }).click();
  await expect(page).toHaveURL(/#\/run\//);

  // The run is listed and can be tapped: not squeezed under the sidebar footer.
  const item = page.locator(".run-item.selected");
  await page.evaluate(() => window.scrollTo(0, 0));
  await expect(item).toBeInViewport({ ratio: 1 });
  const box = (await item.boundingBox())!;
  const foot = (await page.locator(".sidebar-foot").boundingBox())!;
  expect(box.y + box.height).toBeLessThanOrEqual(foot.y);

  // Below 1100 px the inspector sits under the thread; opening it must bring it into view.
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.getByRole("button", { name: "Podrobnosti", exact: true }).click();
  await expect(page.locator(".inspector")).toBeInViewport();
});
