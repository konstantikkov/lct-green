"""Illustrative growth model for the age slider and age-scaled panoramas.

Dependency-free: Blender's bundled Python imports this file next to
blender_scene.py. Mirrored by apps/web/src/viewer/growth.ts; both are checked
against tests/golden/growth_points.json. Visualization only — norm checks and
the DXF export always use the mature catalog parameters.

`species` is a load_species() record (camelCase keys). Size grows from the
planting stock to the mature parameters along u·(1.5 − 0.5·u), u = min(t / T, 1):
growth slows to a third of its initial pace as the plant reaches maturity at T.
"""
from __future__ import annotations

def _progress(species: dict, years: float | None) -> float:
    if years is None:
        return 1.0
    t_mature = float((species.get("growth") or {}).get("yearsToMature") or 0)
    if t_mature <= 0:
        return 1.0
    u = min(max(float(years), 0.0) / t_mature, 1.0)
    return u * (1.5 - 0.5 * u)


def _lerp(start: float, mature: float, f: float) -> float:
    return start + (mature - start) * f


def crown_at(species: dict, years: float | None) -> float:
    """Crown radius, m, `years` after planting (None = mature)."""
    mature = float(species["parameters"]["crownRadiusM"])
    start = (species.get("plantingStock") or {}).get("crownRadiusM")
    if start is None:
        return mature
    return _lerp(min(float(start), mature), mature, _progress(species, years))


def height_at(species: dict, years: float | None) -> float:
    """Plant height, m, `years` after planting (None = mature)."""
    mature = float(species["parameters"]["matureHeightM"])
    h = (species.get("plantingStock") or {}).get("heightM")
    if not h:
        return mature
    start = sum(h) / len(h) if isinstance(h, (list, tuple)) else float(h)
    return _lerp(min(start, mature), mature, _progress(species, years))


def scale_at(species: dict | None, years: float | None) -> tuple[float, float]:
    """(crown, height) ratios to the mature size; (1, 1) for unknown species."""
    if species is None or years is None:
        return 1.0, 1.0
    p = species["parameters"]
    return (crown_at(species, years) / float(p["crownRadiusM"]),
            height_at(species, years) / float(p["matureHeightM"]))
