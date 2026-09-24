import { test, expect, type Page, type TestInfo } from "@playwright/test";
import * as path from "path";

/**
 * Screenshot suite: captures each workspace state into
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
  // --- dashboard (start screen) ---------------------------------------------
  await page.goto("/");
  await expect(t(page, "admin-dashboard")).toBeVisible();
  await shoot(page, testInfo, "dashboard");

  // --- interpretation-tab ----------------------------------------------------
  await t(page, "open-example").click();
  await expect(t(page, "workspace-tabs")).toBeVisible({ timeout: 120_000 });
  await expect(t(page, "layer-summary")).toBeVisible();
  await shoot(page, testInfo, "interpretation-tab");

  // --- constraints-tab -------------------------------------------------------
  await t(page, "tab-constraints").click();
  await expect(t(page, "overlay-legend")).toBeVisible({ timeout: 120_000 });
  await shoot(page, testInfo, "constraints-tab");

  // --- generated-result (auto-advance to Результат) --------------------------
  await t(page, "generate-button").click();
  await expect(t(page, "quality-card")).toBeVisible({ timeout: 240_000 });
  await shoot(page, testInfo, "generated-result");

  // --- audit-view -------------------------------------------------------------
  await t(page, "open-audit").click();
  await expect(t(page, "audit-view")).toBeVisible();
  await expect(t(page, "audit-summary")).toBeVisible({ timeout: 60_000 });
  await shoot(page, testInfo, "audit-view");

  // --- regulations-view --------------------------------------------------------
  await t(page, "nav-regulations").click();
  await expect(t(page, "regulations-view")).toBeVisible();
  await shoot(page, testInfo, "regulations-view");
});
