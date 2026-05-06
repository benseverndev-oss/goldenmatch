import { test, expect } from "@playwright/test";

// Single end-to-end path: home → run → cluster detail. If this passes, the
// frontend bundle, the Vite proxy, the FastAPI backend, run discovery,
// cluster summaries, and cluster detail are all wired correctly.
test("home -> run -> cluster", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("20260101_000000")).toBeVisible();

  await page.getByText("20260101_000000").click();
  await expect(page).toHaveURL(/runs\/20260101_000000/);

  // Click cluster_id 1 in the table. The first cell on that row matches.
  await page.getByRole("cell", { name: "1", exact: true }).first().click();
  // The fixture cluster has Sony DSC-T77 Silver as a member row and again
  // inside the pair field-breakdown — assert at least one renders rather
  // than fight strict-mode disambiguation.
  await expect(page.getByText("Sony DSC-T77 Silver").first()).toBeVisible();
});
