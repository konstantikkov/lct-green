"""Growth model: Python (app/services/growth.py) and TypeScript
(apps/web/src/viewer/growth.ts) agree on the shared checkpoints; age never
reaches norm checks or the export."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.growth import crown_at, height_at, scale_at  # noqa: E402
from app.services.rules import load_species  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
POINTS = Path(__file__).with_name("golden") / "growth_points.json"
WEB = REPO / "apps" / "web"


def _points():
    return json.loads(POINTS.read_text(encoding="utf-8"))["points"]


def test_python_matches_shared_checkpoints():
    species = {s["id"]: s for s in load_species()}
    pts = _points()
    assert {p["speciesId"] for p in pts} == set(species)
    for p in pts:
        sp = species[p["speciesId"]]
        assert crown_at(sp, p["years"]) == pytest.approx(p["crownM"], abs=1e-4), p
        assert height_at(sp, p["years"]) == pytest.approx(p["heightM"], abs=1e-4), p


def test_growth_starts_at_stock_and_ends_mature():
    for sp in load_species():
        stock, mature = sp["plantingStock"], sp["parameters"]
        assert crown_at(sp, 0) == pytest.approx(stock["crownRadiusM"])
        assert height_at(sp, 0) == pytest.approx(sum(stock["heightM"]) / 2)
        t = sp["growth"]["yearsToMature"]
        assert crown_at(sp, t) == pytest.approx(mature["crownRadiusM"])
        assert height_at(sp, t + 50) == pytest.approx(mature["matureHeightM"])
        sizes = [height_at(sp, y) for y in (0, 5, 15, 30)]
        assert sizes == sorted(sizes)
        assert scale_at(sp, None) == (1.0, 1.0)
    assert scale_at(None, 5) == (1.0, 1.0)


@pytest.mark.skipif(not (WEB / "node_modules" / "esbuild").is_dir() or not shutil.which("node"),
                    reason="web dependencies (npm ci) or node not installed")
def test_typescript_matches_shared_checkpoints(tmp_path):
    bundle = tmp_path / "growth.mjs"
    esbuild = WEB / "node_modules" / ".bin" / ("esbuild.cmd" if sys.platform == "win32" else "esbuild")
    subprocess.run([str(esbuild), str(WEB / "src" / "viewer" / "growth.ts"), "--bundle",
                    "--format=esm", f"--outfile={bundle}"], check=True, capture_output=True)
    species = tmp_path / "species.json"
    species.write_text(json.dumps(load_species()), encoding="utf-8")
    script = tmp_path / "check.mjs"
    script.write_text(
        "import { crownAt, heightAt } from './growth.mjs';\n"
        "import { readFileSync } from 'node:fs';\n"
        "const [sp, pts] = process.argv.slice(2).map(f => JSON.parse(readFileSync(f, 'utf8')));\n"
        "const byId = Object.fromEntries(sp.map(s => [s.id, s]));\n"
        "console.log(JSON.stringify(pts.points.map(p => [crownAt(byId[p.speciesId], p.years),"
        " heightAt(byId[p.speciesId], p.years)])));\n", encoding="utf-8")
    out = subprocess.run(["node", str(script), str(species), str(POINTS)],
                         check=True, capture_output=True, text=True).stdout
    got = json.loads(out)
    for p, (crown, height) in zip(_points(), got):
        assert crown == pytest.approx(p["crownM"], abs=1e-4), p
        assert height == pytest.approx(p["heightM"], abs=1e-4), p
