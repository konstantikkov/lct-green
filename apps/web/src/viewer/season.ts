import type { SpeciesInfo } from "../api/types";
import type { Season } from "./PanoramaViewer";

/** Illustrative autumn crown colours by genus (mirrors AUTUMN_TONE in
 *  apps/api/app/services/blender_season.py, simplified to one colour). */
const AUTUMN: Record<string, number> = {
  Tilia: 0xc9a227, Larix: 0xd4a53a, Acer: 0xcf6a1f, Sorbus: 0xb3361f,
  Syringa: 0x7a8740, Cotoneaster: 0xb04a28, Philadelphus: 0xc6b03c,
};

/** Colour to blend into a crown's summer colour, or null for summer. */
export function seasonTint(season: Season, sp: SpeciesInfo | undefined): { color: number; mix: number } | null {
  if (season === "spring") return { color: 0xb5d86a, mix: 0.45 };
  if (season === "autumn") return { color: AUTUMN[(sp?.latin ?? "").split(" ")[0]] ?? 0xc08a30, mix: 0.8 };
  if (season === "winter") return { color: 0x7d6b58, mix: 1 };
  return null;
}
