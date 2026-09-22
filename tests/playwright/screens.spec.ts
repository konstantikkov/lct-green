import { test, expect, type Page, type TestInfo } from "@playwright/test";
import * as path from "path";

/**
 * Screenshot suite: captures each disclosure stage into
 * pw-screens/<viewport>/<state>.png for the three configured viewports.
 */

function shotPath(testInfo: TestInfo, state: string): string {
  return path.join(__dirname, "pw-screens", testInfo.project.name, `${state}.png`);
}

async function shoot(page: Page, testInfo: TestInfo, state: string) {
  await page.waitForTimeout(600); // let canvas / transitions settle
  await page.screenshot({ path: shotPath(testInfo, state), fullPage: false });
}

const t = (page: Page, id: string) => page.locator(`[data-testid="${id}"]:visible`).first();

test("screenshot every stage state", async ({ page }, testInfo) => {
  // --- empty ---------------------------------------------------------------
  await page.goto("/");
  await expect(t(page, "empty-state")).toBeVisible();
  await shoot(page, testInfo, "empty");

  // --- imported ------------------------------------------------------------
  await t(page, "open-example").click();
  await expect(t(page, "interpretation-summary")).toBeVisible({ timeout: 120_000 });
  // Wait for the auto-built constraints legend so overlays are on screen.
  await expect(t(page, "overlay-legend")).toBeVisible({ timeout: 120_000 });
  await shoot(page, testInfo, "imported");

  // --- generated -----------------------------------------------------------
  await t(page, "generate-button").click();
  await expect(t(page, "quality-card")).toBeVisible({ timeout: 240_000 });
  await shoot(page, testInfo, "generated");

  // --- regulations-view ----------------------------------------------------
  await t(page, "nav-regulations").click();
  await expect(t(page, "regulations-view")).toBeVisible();
  await shoot(page, testInfo, "regulations-view");

  // --- runs-view -------------------------------------------------------------
  await t(page, "nav-runs").click();
  await expect(t(page, "runs-view")).toBeVisible();
  await shoot(page, testInfo, "runs-view");
});
