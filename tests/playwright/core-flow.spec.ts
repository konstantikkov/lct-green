import { test, expect, type Page } from "@playwright/test";

/**
 * Core demo flow (45–60 s narrative):
 * upload example → drawing appears → interpretation summary → generate →
 * quality card → (best-effort) tree click → export available.
 */

async function openExampleProject(page: Page) {
  await page.goto("/");
  await expect(t(page, "empty-state")).toBeVisible();
  await t(page, "open-example").click();
  // Upload + parse + classify are synchronous server-side; generous timeout.
  await expect(t(page, "interpretation-summary")).toBeVisible({ timeout: 120_000 });
}

const t = (page: Page, id: string) => page.locator(`[data-testid="${id}"]:visible`).first();

test("upload example → generate → quality card → export", async ({ page }) => {
  await openExampleProject(page);

  // The canvas is the hero — the 2D drawing must be mounted.
  await expect(page.locator("canvas")).toBeVisible();

  // Compact semantic layer summary is the default (no raw 29-layer dump).
  await expect(t(page, "layer-summary")).toBeVisible();

  // Constraints are fetched automatically; wait for the overlay legend
  // (progress line "Building constraint zones…" may flash by too fast to assert).
  await expect(t(page, "overlay-legend")).toBeVisible({ timeout: 120_000 });

  // Advanced settings stay folded by default.
  await expect(t(page, "advanced-toggle")).toBeVisible();

  // Generate from the prominent TopBar button.
  const generate = t(page, "generate-button");
  await expect(generate).toBeEnabled();
  await generate.click();

  // Full pipeline (candidates → optimize → validate → judge) — long timeout.
  await expect(t(page, "quality-card")).toBeVisible({ timeout: 240_000 });

  // Quality review card: hard-constraint verdict + deterministic bars.
  await expect(t(page, "quality-card")).toContainText(/Hard constraints|hard violation/);
  await expect(t(page, "quality-card")).toContainText("Spatial quality");

  // Best-effort: click near the canvas center to try selecting a tree.
  // Selection depends on where trees landed, so this must not fail the test.
  const canvas = page.locator("canvas").first();
  const box = await canvas.boundingBox();
  if (box) {
    const candidates: [number, number][] = [
      [0.5, 0.5], [0.45, 0.45], [0.55, 0.55], [0.4, 0.6], [0.6, 0.4],
    ];
    for (const [fx, fy] of candidates) {
      await canvas.click({ position: { x: box.width * fx, y: box.height * fy } });
      await page.waitForTimeout(400);
      if (await t(page, "inspector").isVisible().catch(() => false)) {
        // WHY panel: regulatory checks for the selected tree.
        await expect(t(page, "inspector")).toContainText(/Regulatory checks|Exclusion zone/);
        break;
      }
    }
  }

  // Export actions are available once a plan exists.
  await expect(t(page, "export-dxf")).toBeEnabled();
  await expect(t(page, "export-report")).toBeEnabled();
});

test("uncertain layers surface as a single review chip", async ({ page }) => {
  await openExampleProject(page);
  // The chip is data-dependent: assert only that, when present, it expands
  // into the review-filtered raw layer list.
  const chip = t(page, "review-chip");
  if (await chip.isVisible().catch(() => false)) {
    await expect(chip).toContainText(/require review/);
    await chip.click();
    await expect(t(page, "layer-summary")).toBeHidden();
  } else {
    await expect(t(page, "layer-summary")).toBeVisible();
  }
});
