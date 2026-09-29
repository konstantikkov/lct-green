"""Generates the deterministic synthetic DXF fixture used by automated tests.

Site: 200×140 m parcel (meters, $INSUNITS=6) with a building, road+curb,
sidewalk, two utility lines (water + power cable), existing trees, and a
green area — mirroring the semantic categories the classifier detects.
"""
from __future__ import annotations

from pathlib import Path

import ezdxf

OUT = Path(__file__).resolve().parents[3] / "fixtures" / "synthetic_site.dxf"


def build(path: Path = OUT) -> Path:
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 6  # meters
    msp = doc.modelspace()

    layers = {
        "ГРАНИЦА_УЧАСТКА": 1,
        "ЗДАНИЯ": 8,
        "ДОРОГИ_ПРОЕЗДЫ": 252,
        "ТРОТУАРЫ": 253,
        "В1_ВОДОПРОВОД": 5,
        "КЛ-0.4_КАБЕЛЬ": 6,
        "СУЩ_ДЕРЕВЬЯ": 3,
        "ГАЗОНЫ": 84,
        "ТЕКСТ": 7,
    }
    for name, color in layers.items():
        doc.layers.new(name, dxfattribs={"color": color})

    # site boundary 200 x 140
    msp.add_lwpolyline([(0, 0), (200, 0), (200, 140), (0, 140)], close=True,
                       dxfattribs={"layer": "ГРАНИЦА_УЧАСТКА"})

    # building 60 x 30 at (30, 80)
    msp.add_lwpolyline([(30, 80), (90, 80), (90, 110), (30, 110)], close=True,
                       dxfattribs={"layer": "ЗДАНИЯ"})

    # road along the south edge (as a closed strip y: 8..16)
    msp.add_lwpolyline([(0, 8), (200, 8), (200, 16), (0, 16)], close=True,
                       dxfattribs={"layer": "ДОРОГИ_ПРОЕЗДЫ"})

    # sidewalk strip y: 18..21
    msp.add_lwpolyline([(0, 18), (200, 18), (200, 21), (0, 21)], close=True,
                       dxfattribs={"layer": "ТРОТУАРЫ"})

    # water main: diagonal across the west half
    msp.add_lwpolyline([(10, 30), (60, 60), (110, 70)],
                       dxfattribs={"layer": "В1_ВОДОПРОВОД"})
    # power cable: vertical at x=150
    msp.add_line((150, 20), (150, 135), dxfattribs={"layer": "КЛ-0.4_КАБЕЛЬ"})

    # existing trees (circles ~ crowns)
    for cx, cy in [(120, 100), (135, 95), (25, 40), (180, 60)]:
        msp.add_circle((cx, cy), radius=3.0, dxfattribs={"layer": "СУЩ_ДЕРЕВЬЯ"})

    # green area polygon in the north-east
    msp.add_lwpolyline([(140, 110), (195, 110), (195, 135), (140, 135)], close=True,
                       dxfattribs={"layer": "ГАЗОНЫ"})

    msp.add_text("УЧАСТОК БЛАГОУСТРОЙСТВА", dxfattribs={
        "layer": "ТЕКСТ", "height": 3.0, "insert": (60, 130)})

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(str(path))
    return path


if __name__ == "__main__":
    p = build()
    print(f"fixture written: {p}")
