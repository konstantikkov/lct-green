import type { SpeciesInfo } from "../api/types";

/**
 * Illustrative growth model — mirror of apps/api/app/services/growth.py,
 * checked against apps/api/tests/golden/growth_points.json. Visualization
 * only: norm checks and the DXF export use the mature catalog parameters.
 * Size grows from the planting stock to the mature parameters along
 * u·(1.5 − 0.5·u), u = min(years / yearsToMature, 1). years = null → mature.
 */
type GrowthSpecies = Pick<SpeciesInfo, "parameters" | "plantingStock" | "growth">;

/** Slider stops; null = mature (what the validator checks). */
export const AGE_STOPS: (number | null)[] = [0, 5, 15, 30, null];

function progress(sp: GrowthSpecies, years: number | null): number {
  if (years == null) return 1;
  const t = Number(sp.growth?.yearsToMature ?? 0);
  if (!(t > 0)) return 1;
  const u = Math.min(Math.max(years, 0) / t, 1);
  return u * (1.5 - 0.5 * u);
}

const lerp = (start: number, mature: number, f: number) => start + (mature - start) * f;

/** Crown radius, m, `years` after planting. */
export function crownAt(sp: GrowthSpecies, years: number | null): number {
  const mature = sp.parameters.crownRadiusM;
  const start = sp.plantingStock?.crownRadiusM;
  if (start == null) return mature;
  return lerp(Math.min(start, mature), mature, progress(sp, years));
}

/** Plant height, m, `years` after planting. */
export function heightAt(sp: GrowthSpecies, years: number | null): number {
  const mature = sp.parameters.matureHeightM;
  const h = sp.plantingStock?.heightM;
  if (h == null || (Array.isArray(h) && !h.length)) return mature;
  const start = Array.isArray(h) ? h.reduce((a, b) => a + b, 0) / h.length : h;
  return lerp(Math.min(start, mature), mature, progress(sp, years));
}

/** [crown, height] ratios to the mature size; [1, 1] for unknown species. */
export function scaleAt(sp: GrowthSpecies | undefined, years: number | null): [number, number] {
  if (!sp || years == null) return [1, 1];
  return [crownAt(sp, years) / sp.parameters.crownRadiusM, heightAt(sp, years) / sp.parameters.matureHeightM];
}

export function ageLabel(years: number | null): string {
  if (years == null) return "зрелые";
  if (years === 0) return "при посадке";
  const n = years % 100, d = years % 10;
  const word = n >= 11 && n <= 14 ? "лет" : d === 1 ? "год" : d >= 2 && d <= 4 ? "года" : "лет";
  return `через ${years} ${word}`;
}
