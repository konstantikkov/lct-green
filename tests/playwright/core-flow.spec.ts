import { test, expect, type Page } from "@playwright/test";

/**
 * Core demo flow (45–60 s narrative), workspace-tab structure:
 * dashboard → upload example → Интерпретация → Ограничения → Генерация →
 * generate → Результат (quality card) → Аудит соответствия → export.
 */

const t = (page: Page, id: string) => page.locator(`[data-testid="${id}"]:visible`).first();

/** The step-by-step wizard opens automatically after every upload; the
 *  classic workspace flow dismisses it via «Пропустить мастер». */
async function dismissWizard(page: Page) {
  const skip = t(page, "wizard-skip");
  if (await skip.isVisible().catch(() => false)) {
    await skip.click();
    await expect(page.locator('[data-testid="wizard"]')).toHaveCount(0);
  }
}

async function openExampleProject(page: Page) {
  await page.goto("/");
  await expect(t(page, "admin-dashboard")).toBeVisible();
  await t(page, "open-example").click();
  // Upload + parse + classify are synchronous server-side; generous timeout.
  await expect(t(page, "workspace-tabs")).toBeVisible({ timeout: 120_000 });
  await dismissWizard(page);
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

test("wizard flow: upload → ограничения → условия → генерация → итоги", async ({ page }) => {
  // The 7B extraction model is single-slot and takes minutes per condition:
  // run the LLM-heavy wizard path once (1440x900), not on every viewport.
  test.skip(test.info().project.name !== "1440x900",
    "LLM-backed wizard flow runs on a single viewport");
  test.setTimeout(1_200_000);
  // Conditions (step 2) need the local LLM; check capabilities first.
  const llmAvailable = await page.request
    .get("/api/capabilities")
    .then(async (r) => {
      if (!r.ok()) return false;
      const caps = (await r.json()) as { localLlm?: { enabled?: boolean; reachable?: boolean } };
      return Boolean(caps.localLlm?.enabled && caps.localLlm?.reachable);
    })
    .catch(() => false);

  await page.goto("/");
  await expect(t(page, "admin-dashboard")).toBeVisible();
  await t(page, "open-example").click();

  // The wizard opens automatically after a successful upload.
  await expect(t(page, "wizard")).toBeVisible({ timeout: 120_000 });
  await expect(t(page, "wizard-step-1")).toBeVisible();
  // Step 1 shows the interpretation summary and the active rule counters.
  await expect(t(page, "wizard-step-1")).toContainText("Действующие правила");

  // → Step 2 (Условия). Optional: parse a free-text condition when LLM is up.
  await t(page, "wizard-next").click();
  await expect(t(page, "wizard-step-2")).toBeVisible();
  if (llmAvailable) {
    await t(page, "condition-text").fill(
      "Расстояние от дерева до здания должно быть не менее 5 метров.",
    );
    await t(page, "parse-text-condition").click();
    await expect(t(page, "condition-card")).toBeVisible({ timeout: 480_000 });
  }

  // → Step 3 (Генерация): generate inside the wizard, progress bar visible.
  await t(page, "wizard-next").click();
  await expect(t(page, "wizard-step-3")).toBeVisible();
  await t(page, "wizard-generate").click();
  await expect(t(page, "wizard-progress")).toBeVisible({ timeout: 60_000 });
  await expect(t(page, "wizard-progress")).toContainText("%");

  // COMPLETED auto-advances to Итоги; the compliance report loads inline.
  await expect(t(page, "wizard-step-4")).toBeVisible({ timeout: 240_000 });
  await expect(t(page, "wizard-report")).toContainText(/\d+.*посадок/s, { timeout: 60_000 });
  await expect(t(page, "wizard-step-4")).toContainText("деревьев");

  // Готово closes the wizard into the workspace Результат tab.
  await t(page, "wizard-done").click();
  await expect(page.locator('[data-testid="wizard"]')).toHaveCount(0);
  await expect(t(page, "quality-card")).toBeVisible();
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
