import { test, expect, type Page } from "@playwright/test";

/**
 * Core demo flow (45–60 s narrative), workspace-tab structure:
 * dashboard → upload example → Интерпретация → Ограничения → Генерация →
 * generate → Результат (quality card) → Аудит соответствия → export.
 */

const t = (page: Page, id: string) => page.locator(`[data-testid="${id}"]:visible`).first();

async function openExampleProject(page: Page) {
  await page.goto("/");
  await expect(t(page, "admin-dashboard")).toBeVisible();
  await t(page, "open-example").click();
  // Upload + parse + classify are synchronous server-side; generous timeout.
  await expect(t(page, "workspace-tabs")).toBeVisible({ timeout: 120_000 });
  // Auto-advance lands on Интерпретация.
  await expect(t(page, "tab-interpretation")).toBeVisible();
}

test("upload example → tabs → generate → результат → аудит → export", async ({ page }) => {
  await openExampleProject(page);

  // The canvas is the hero — the 2D drawing must be mounted.
  await expect(page.locator("canvas")).toBeVisible();

  // Интерпретация panel is open by default with the compact semantic summary.
  await expect(t(page, "layer-summary")).toBeVisible();

  // Ограничения tab: constraints are fetched automatically; wait for the legend.
  await t(page, "tab-constraints").click();
  await expect(t(page, "overlay-legend")).toBeVisible({ timeout: 120_000 });

  // Генерация tab: advanced settings stay folded by default.
  await t(page, "tab-generate").click();
  await expect(t(page, "advanced-toggle")).toBeVisible();

  // Generate from the prominent TopBar button.
  const generate = t(page, "generate-button");
  await expect(generate).toBeEnabled();
  await generate.click();

  // Full pipeline (candidates → optimize → validate → judge) — long timeout.
  // Auto-advance lands on Результат, where the quality card lives.
  await expect(t(page, "quality-card")).toBeVisible({ timeout: 240_000 });
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

  // Compliance audit: the example plan must be fully compliant.
  await t(page, "open-audit").click();
  await expect(t(page, "audit-view")).toBeVisible();
  await expect(t(page, "audit-summary")).toBeVisible({ timeout: 60_000 });
  // Exactly zero violations (not e.g. "10 с нарушениями").
  await expect(t(page, "audit-summary")).toContainText(/(^|\D)0\s*с нарушениями/);

  // Export actions live in the TopBar and are available once a plan exists.
  await expect(t(page, "export-dxf")).toBeEnabled();
  await expect(t(page, "export-report")).toBeEnabled();
});

test("uncertain layers surface as a single review chip", async ({ page }) => {
  await openExampleProject(page);
  // The chip is data-dependent: assert only that, when present, it expands
  // into the review-filtered raw layer list (Интерпретация tab is open).
  const chip = t(page, "review-chip");
  if (await chip.isVisible().catch(() => false)) {
    await expect(chip).toContainText(/require review/);
    await chip.click();
    await expect(t(page, "layer-summary")).toBeHidden();
  } else {
    await expect(t(page, "layer-summary")).toBeVisible();
  }
});
