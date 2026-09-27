"""xref-package ingestion (AutoCAD eTransmit zip → one merged DXF).

A synthetic package mirrors the Moscow ИРД delivery: a base general plan with
external references to the work boundary (drawn as two open halves, as in the
real pilot data), topographic plan (tp), utilities (up), survey frames (brd)
and the cover-fill plan ("заливки"). References are DXF, so no DWG converter
is needed; paths are Windows-style and relative to the base drawing.
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import ezdxf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config  # noqa: E402
from app.services import cad_package, classification  # noqa: E402
from app.services.constraints import build_constraints  # noqa: E402
from app.services.dxf_parser import parse_dxf  # noqa: E402
from app.services.geometry import build_geometry_index, detect_site_boundary  # noqa: E402
from app.services.rules import load_registry  # noqa: E402

UP = "output[1]_3_ДЖКХ-25_00001up"
TP = "output[1]_3_ДЖКХ-25_00001tp"
BRD = "output[1]_brd"
WB = "10000176_Границы работ_Тест"
COVERS = "10000176_Заливки"
GONE = "_01_борт"


def _dxf_bytes(build) -> bytes:
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 6
    build(doc, doc.modelspace())
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8")


def _hatch(msp, layer, pts):
    h = msp.add_hatch(dxfattribs={"layer": layer})
    h.paths.add_polyline_path(pts, is_closed=True)


def _base(doc, msp):
    msp.add_lwpolyline([(30, 30), (32, 30), (32, 32)], close=True, dxfattribs={"layer": "ДВ_ГП_П_МАФ"})
    for name, rel in ((WB, rf".\ссылки\{WB}.dxf"), (TP, rf".\3ДЖКХ-25_00001\{TP}.dxf"),
                      (UP, rf".\3ДЖКХ-25_00001\{UP}.dxf"), (BRD, rf".\3ДЖКХ-25_00001\{BRD}.dxf"),
                      (COVERS, rf".\ссылки\{COVERS}.dxf"), (GONE, rf".\подгрузки\{GONE}.dwg")):
        doc.add_xref_def(rel, name)
        msp.add_blockref(name, (0, 0))


def _work_boundary(doc, msp):
    # one 100 × 60 area drawn as two open halves whose ends nearly meet
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 60)], dxfattribs={"layer": "ДВ_ГП_П_Граница работ"})
    msp.add_lwpolyline([(100.6, 60.4), (0, 60), (0.05, 0.02)], dxfattribs={"layer": "ДВ_ГП_П_Граница работ"})


def _tp(doc, msp):
    msp.add_lwpolyline([(10, 10), (20, 10), (20, 20), (10, 20)], close=True, dxfattribs={"layer": "Здания"})
    msp.add_line((0, 5), (100, 5), dxfattribs={"layer": "Бортовой камень"})
    msp.add_lwpolyline([(1000, 1000), (1010, 1000), (1010, 1010)], close=True,
                       dxfattribs={"layer": "Здания"})  # far outside — clipped away


def _up(doc, msp):
    msp.add_line((60, 0), (60, 60), dxfattribs={"layer": "Теплосеть"})
    msp.add_line((0, 25), (100, 25), dxfattribs={"layer": "Водосток"})


def _brd(doc, msp):
    msp.add_lwpolyline([(-50, -50), (150, -50), (150, 110), (-50, 110)], close=True,
                       dxfattribs={"layer": "Граница заказа"})


def _covers(doc, msp):
    _hatch(msp, "_ГЗН-ГЗН", [(40, 30), (90, 30), (90, 55), (40, 55)])
    _hatch(msp, "_АБ ТР-АБ ТР", [(0, 5), (100, 5), (100, 8), (0, 8)])
    _hatch(msp, "ДВ_ОФР_Рамка", [(-40, -40), (140, -40), (140, 100), (-40, 100)])


def _package(tmp_path: Path, with_report: bool = True) -> Path:
    root = "Генплан_Тест - Standard"
    files = {
        f"{root}/_06_Генплан_Тест.dxf": _dxf_bytes(_base),
        f"{root}/ссылки/{WB}.dxf": _dxf_bytes(_work_boundary),
        f"{root}/ссылки/{COVERS}.dxf": _dxf_bytes(_covers),
        f"{root}/3ДЖКХ-25_00001/{TP}.dxf": _dxf_bytes(_tp),
        f"{root}/3ДЖКХ-25_00001/{UP}.dxf": _dxf_bytes(_up),
        f"{root}/3ДЖКХ-25_00001/{BRD}.dxf": _dxf_bytes(_brd),
    }
    if with_report:
        files[f"{root}/_06_Генплан_Тест.txt"] = (
            "Комплект файлов - Отчет:\n\nБазовый чертеж:\n\t_06_Генплан_Тест.dxf\n\n"
            "Внешние ссылки из чертежа AutoCAD:\n").encode("cp1251")
    z = tmp_path / "package.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return z


@pytest.fixture()
def merged(tmp_path):
    res = cad_package.ingest_package(_package(tmp_path), tmp_path / "work")
    doc = parse_dxf(res.merged_dxf, "pkg", "package.zip", "PACKAGE", list(res.log))
    classification.classify_document(doc)
    cad_package.apply_source_classification(doc, res.sources)
    return res, doc


def test_sources_resolved_and_classified_by_kind(merged):
    res, _ = merged
    by_name = {s.name: s for s in res.sources}
    assert res.base_file.endswith("_06_Генплан_Тест.dxf")
    assert {by_name[n].kind for n in (WB, TP, UP, BRD, COVERS)} == {
        "work_boundary", "tp", "up", "brd", "covers"}
    assert all(by_name[n].status == "merged" for n in (WB, TP, UP, BRD, COVERS))
    assert by_name[GONE].status == "missing"
    # the far-away building is clipped (work boundary + 15 m), the rest kept
    assert by_name[TP].entities_in == 3 and by_name[TP].entities_kept == 2
    assert res.clip["mode"] == "work_boundary"


def test_layers_renamed_like_autocad_bind(merged):
    _, doc = merged
    names = {li.name for li in doc.layers}
    assert f"{UP}$0$Теплосеть" in names
    assert f"{TP}$0$Здания" in names
    assert "ДВ_ГП_П_МАФ" in names  # base drawing layers keep their names


def test_source_aware_classification(merged):
    _, doc = merged
    cls = {li.name: li.classification for li in doc.layers}
    wb = cls[f"{WB}$0$ДВ_ГП_П_Граница работ"]
    assert wb.category == "SITE_BOUNDARY" and wb.source == "package"
    # survey order boundary: named like a boundary, but it is not the site
    assert cls[f"{BRD}$0$Граница заказа"].category == "ANNOTATION"
    assert cls[f"{UP}$0$Теплосеть"].category == "UTILITY_HEAT"
    assert cls[f"{UP}$0$Водосток"].category == "UTILITY_SEWER"
    assert cls[f"{TP}$0$Бортовой камень"].category == "CURB"
    lawn = cls[f"{COVERS}$0$_ГЗН-ГЗН"]
    assert lawn.category == "GREEN_AREA" and lawn.matchedKeyword.startswith("cover:")
    assert cls[f"{COVERS}$0$_АБ ТР-АБ ТР"].category == "SIDEWALK"
    assert cls[f"{COVERS}$0$ДВ_ОФР_Рамка"].category == "ANNOTATION"


def test_site_is_the_stitched_work_boundary_and_domain_the_lawns(merged):
    _, doc = merged
    idx = build_geometry_index(doc)
    site, src = detect_site_boundary(doc, idx)
    assert src == "detected"
    assert abs(site.area - 100 * 60) < 60  # two open halves → one ring, not the brd frame
    cons = build_constraints(doc, idx, load_registry())
    assert cons.allowed_basis == "cover_lawns"
    lawn = 50 * 25
    assert 0 < cons.allowed_area.area < lawn  # lawn minus setbacks (heat main, storm sewer)
    assert cons.allowed_area.within(cons.planting_domain.buffer(0.01))


def test_base_found_without_report(tmp_path):
    res = cad_package.ingest_package(_package(tmp_path, with_report=False), tmp_path / "w")
    assert res.base_file.endswith("_06_Генплан_Тест.dxf")


def test_zip_slip_is_refused(tmp_path):
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("../escape.dxf", b"0\nEOF\n")
    with pytest.raises(cad_package.PackageError) as exc:
        cad_package.ingest_package(z, tmp_path / "w")
    assert exc.value.code == "INVALID_FILE"


def test_windows_zip_names_decoded_from_cp866():
    info = zipfile.ZipInfo("x")
    info.filename = "ссылки/Границы работ.dwg".encode("cp866").decode("cp437")
    info.flag_bits = 0
    assert cad_package._member_name(info) == "ссылки/Границы работ.dwg"


def test_classifier_strips_bind_prefix_and_reads_cover_codes():
    c = classification.classify_layer_name
    assert c("x$0$Теплосеть").category == "UTILITY_HEAT"
    assert c("$130$БР 1000 300 150").category == "CURB"
    assert c("ГП1 (300х150)").category == "CURB"
    assert c("ДВ_ГП_П_МАФ").category != "CURB"
    assert c("_АБ ТР-ГЗН (2 и более метров)").category == "GREEN_AREA"
    assert c("_ГЗН-АБ ПЧ").category == "ROAD"
    assert c("_ГЗН-ПЛ ТР").category == "SIDEWALK"


def test_upload_zip_package_endpoint(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.regulations import storage
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.setattr(storage, "_initialized", False)
    config.ensure_dirs()
    from app.main import app
    with TestClient(app) as client:
        z = _package(tmp_path)
        r = client.post("/api/projects", files={"file": ("package.zip", z.read_bytes(), "application/zip")})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["format"] == "PACKAGE"
        kinds = {s["kind"]: s["status"] for s in body["package"]["sources"]}
        assert kinds["up"] == "merged" and kinds["work_boundary"] == "merged"
        pid = body["projectId"]
        cons = client.get(f"/api/projects/{pid}/constraints").json()
        assert cons["siteBoundarySource"] == "detected" and cons["allowedBasis"] == "cover_lawns"

        bad = client.post("/api/projects", files={"file": ("x.zip", b"not a zip", "application/zip")})
        assert bad.status_code == 422
