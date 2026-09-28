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
    cad_package.apply_source_classification(doc, res.sources, res.boundary_layers)
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


def _dangling_pointers(path: Path) -> int:
    """Pointer group codes naming objects the file does not define — AutoCAD
    rejects such a file as damaged even though ezdxf reads it."""
    pointer = set(range(320, 370)) | set(range(390, 400)) | {480, 481, 1005}
    lines = [ln.rstrip("\r") for ln in path.read_text(encoding="utf-8").split("\n")]
    defined, refs, section, prev = set(), [], None, None
    for i in range(0, len(lines) - 1, 2):
        code, val = int(lines[i]), lines[i + 1].strip()
        if code == 2 and prev == "SECTION":
            section = val
        if code == 0:
            prev = val
            continue
        if code in (5, 105) and section != "HEADER":
            defined.add(val.upper())
        elif code in pointer and val not in ("", "0"):
            refs.append(val.upper())
    return sum(1 for h in refs if h not in defined)


def test_merged_package_is_autocad_safe(tmp_path):
    """Associative hatches, layer material/plot-style handles and complex
    linetypes keep SOURCE handles after merging — all must be re-pointed."""
    root = tmp_path / "pkg"

    def _tp_with_foreign_pointers(doc, msp):
        _tp(doc, msp)
        doc.layers.add("Леса и газоны")
        boundary = msp.add_lwpolyline([(40, 30), (45, 30), (45, 35)], close=True,
                                      dxfattribs={"layer": "Леса и газоны"})
        h = msp.add_hatch(dxfattribs={"layer": "Леса и газоны"})
        path = h.paths.add_polyline_path([(40, 30), (45, 30), (45, 35)], is_closed=True)
        path.source_boundary_objects = [boundary.dxf.handle]
        h.dxf.associative = 1
        msp.delete_entity(boundary)            # boundary entity not merged
        doc.layers.get("Леса и газоны").dxf.material_handle = "FFFF1"
    z = _package(tmp_path)
    # the same package with the tp swapped for a pointer-laden variant
    name = f"Генплан_Тест - Standard/3ДЖКХ-25_00001/{TP}.dxf"
    data = _dxf_bytes(_tp_with_foreign_pointers)
    z2 = tmp_path / "package2.zip"
    with zipfile.ZipFile(z) as src, zipfile.ZipFile(z2, "w") as dst:
        for info in src.infolist():
            dst.writestr(info.filename, data if info.filename == name else src.read(info))
    res = cad_package.ingest_package(z2, root)
    assert _dangling_pointers(res.merged_dxf) == 0
    assert any("AutoCAD-safe" in m for m in res.log)


def test_export_drops_inserts_of_missing_blocks(tmp_path):
    """LibreDWG may leave INSERTs of blocks it did not convert (the
    "_ArchTick" dimension arrow) in the base drawing itself; AutoCAD rejects
    such a file, so the export must drop them."""
    from app.services.dxf_export import export_greening_dxf
    d = ezdxf.new("R2018")
    blk = d.blocks.new("_ArchTick")
    blk.add_line((0, 0), (1, 1))
    msp = d.modelspace()
    msp.add_blockref("_ArchTick", (5, 5), dxfattribs={"layer": "Размеры"})
    msp.add_line((0, 0), (10, 0))
    d.blocks.delete_block("_ArchTick", safe=False)
    src = tmp_path / "src.dxf"
    d.saveas(src)
    out = tmp_path / "out.dxf"
    export_greening_dxf(src, out, [], None, [], 1.0, 0, "test")
    doc = ezdxf.readfile(out)
    assert not [e for e in doc.modelspace().query("INSERT") if e.dxf.name not in doc.blocks]
    assert len(doc.modelspace().query("LINE")) == 1


def _complex_linetype(doc, name: str, shx: str) -> None:
    """A topographic linetype like "Otkos ukrep": two shape elements, each
    referencing the shape-file style by handle."""
    from ezdxf.entities.ltype import LinetypePattern
    from ezdxf.lldxf.tags import Tags
    from ezdxf.lldxf.types import DXFTag
    style = doc.styles.add_shx(shx)
    lt = doc.linetypes.add(name, pattern=[1.0, 0.5, -0.5])
    h = style.dxf.handle
    lt.pattern_tags = LinetypePattern(Tags([
        DXFTag(72, 65), DXFTag(73, 3), DXFTag(40, 1.0),
        DXFTag(49, 0.0), DXFTag(74, 4), DXFTag(75, 50), DXFTag(340, h), DXFTag(46, 0.005),
        DXFTag(50, 0.0), DXFTag(44, 0.0), DXFTag(45, 0.0),
        DXFTag(49, 0.5), DXFTag(74, 4), DXFTag(75, 119), DXFTag(340, h), DXFTag(46, 0.005),
        DXFTag(50, 0.0), DXFTag(44, 0.0), DXFTag(45, 0.0),
        DXFTag(49, 0.5), DXFTag(74, 0)]))


def test_complex_linetype_every_shape_element_repointed(tmp_path):
    """Every shape element must reference the target's shape-file style —
    the Importer re-points at most the first reference."""
    shx = "dgnlstyle-lib500_top_пцо.shx"

    def _tp_with_ltype(doc, msp):
        _tp(doc, msp)
        for _ in range(40):  # push the style handle away from the target's
            doc.styles.add_shx(f"pad{_}.shx")
        _complex_linetype(doc, "Otkos ukrep", shx)
        msp.add_line((5, 40), (30, 40), dxfattribs={"layer": "Откосы", "linetype": "Otkos ukrep"})

    name = f"Генплан_Тест - Standard/3ДЖКХ-25_00001/{TP}.dxf"
    z, z2 = _package(tmp_path), tmp_path / "package3.zip"
    with zipfile.ZipFile(z) as src, zipfile.ZipFile(z2, "w") as dst:
        for info in src.infolist():
            dst.writestr(info.filename, _dxf_bytes(_tp_with_ltype) if info.filename == name else src.read(info))
    res = cad_package.ingest_package(z2, tmp_path / "w3")
    doc = ezdxf.readfile(str(res.merged_dxf))
    lt = doc.linetypes.get("Otkos ukrep")
    refs = [t.value for t in lt.pattern_tags.tags if t.code == 340]
    assert len(refs) == 2
    for h in refs:
        st = doc.entitydb[h]
        assert st.dxftype() == "STYLE" and st.dxf.flags & 1 and st.dxf.font.lower() == shx.lower()
    assert _dangling_pointers(res.merged_dxf) == 0


def test_source_kinds_other_pilot_namings():
    k = cad_package.source_kind
    assert k("output[1-6]_3_ДЖКХпр-25_00627kl") == "kl"      # Харьковский проезд
    assert k("tp_Макеева С. ул") == "tp"                       # Макеева: prefix form
    assert k("up_Макеева С. ул") == "up"
    assert k("xref_граница_Макеева С. ул") == "work_boundary"
    assert k("01_Границы работ") == "work_boundary"            # 3-я Парковая
    assert k("xref_ОДД_Макеева С. ул") == "other"
    assert k("НО_Харьковский проезд") == "other"
    wb = cad_package.WORK_BOUNDARY_LAYER
    assert wb.search("ДВ_ГП_П_Граница работ") and wb.search("Граница_работ")
    assert not wb.search("_за границами  работ") and not wb.search("вне границ работ")


def _base_with_boundary_layer(doc, msp):
    """Харьковский проезд: no work-boundary file — the outline is a layer of
    the base drawing itself."""
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 60), (0, 60)], close=True,
                       dxfattribs={"layer": "ДВ_ГП_П_Граница работ"})
    for name, rel in ((TP, rf".\Ссылки\{TP}.dxf"), (UP, rf".\Ссылки\{UP}.dxf"),
                      (BRD, rf".\Ссылки\{BRD}.dxf")):
        doc.add_xref_def(rel, name)
        msp.add_blockref(name, (0, 0))


def test_work_boundary_as_a_layer_of_the_base_drawing(tmp_path):
    root = "АПОТ - Standard"
    files = {
        f"{root}/АПОТ.dxf": _dxf_bytes(_base_with_boundary_layer),
        f"{root}/Ссылки/{TP}.dxf": _dxf_bytes(_tp),
        f"{root}/Ссылки/{UP}.dxf": _dxf_bytes(_up),
        f"{root}/Ссылки/{BRD}.dxf": _dxf_bytes(_brd),
    }
    z = tmp_path / "p.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for n, d in files.items():
            zf.writestr(n, d)
    res = cad_package.ingest_package(z, tmp_path / "w")
    assert res.clip["mode"] == "work_boundary_layer"
    assert res.boundary_layers == ["ДВ_ГП_П_Граница работ"]
    tp_src = next(s for s in res.sources if s.name == TP)
    assert tp_src.entities_in == 3 and tp_src.entities_kept == 2  # far building clipped after merge
    doc = parse_dxf(res.merged_dxf, "pkg", "p.zip", "PACKAGE", list(res.log))
    classification.classify_document(doc)
    cad_package.apply_source_classification(doc, res.sources, res.boundary_layers)
    cls = {li.name: li.classification.category for li in doc.layers}
    assert cls["ДВ_ГП_П_Граница работ"] == "SITE_BOUNDARY"
    assert cls[f"{BRD}$0$Граница заказа"] == "ANNOTATION"
    idx = build_geometry_index(doc)
    site, src = detect_site_boundary(doc, idx)
    assert src == "detected" and abs(site.area - 6000) < 1


def test_base_from_the_richest_report_next_to_its_drawing(tmp_path):
    """Two packages zipped together (general plan + dendro plan) and a copy of
    the base drawing in another folder: the report listing more xrefs wins
    and its base is the drawing next to it."""
    z = _package(tmp_path)
    z2 = tmp_path / "two.zip"
    dendro = "дендроплан/Дендро.txt"
    with zipfile.ZipFile(z) as src, zipfile.ZipFile(z2, "w") as dst:
        for info in src.infolist():
            dst.writestr(info.filename, src.read(info))
        dst.writestr("копия/_06_Генплан_Тест.dxf", _dxf_bytes(lambda d, m: None))
        dst.writestr("дендроплан/Дендро.dxf", _dxf_bytes(lambda d, m: None))
        dst.writestr(dendro, ("Базовый чертеж:\n\tДендро.dxf\n\n"
                              "Внешние ссылки из чертежа AutoCAD:\n\tone.dwg\n\n").encode("cp1251"))
        dst.writestr("Генплан_Тест - Standard/_06_Генплан_Тест.txt", (
            "Базовый чертеж:\n\t_06_Генплан_Тест.dxf\n\nВнешние ссылки из чертежа AutoCAD:\n"
            "\ta.dwg\n\tb.dwg\n\tc.dwg\n\n").encode("cp1251"))
    res = cad_package.ingest_package(z2, tmp_path / "w2")
    assert res.base_file.replace("\\", "/") == "Генплан_Тест - Standard/_06_Генплан_Тест.dxf"


def test_nested_xrefs_are_merged(tmp_path):
    """Макеева: base → xref_ИТП → tp_ / up_ (the ИРД hangs one level down)."""
    def _base(doc, msp):
        doc.add_xref_def(r".\xref\xref_ИТП.dxf", "xref_ИТП")
        msp.add_blockref("xref_ИТП", (0, 0))
        doc.add_xref_def(r".\xref\граница.dxf", "xref_граница_Тест")
        msp.add_blockref("xref_граница_Тест", (0, 0))

    def _itp(doc, msp):
        for name in ("tp_Тест", "up_Тест"):
            doc.add_xref_def(rf".\xref_ИТП\{name}.dxf", name)
            msp.add_blockref(name, (0, 0))

    root = "10001121 - Standard"
    files = {
        f"{root}/10001121.dxf": _dxf_bytes(_base),
        f"{root}/xref/xref_ИТП.dxf": _dxf_bytes(_itp),
        f"{root}/xref/xref_ИТП/tp_Тест.dxf": _dxf_bytes(_tp),
        f"{root}/xref/xref_ИТП/up_Тест.dxf": _dxf_bytes(_up),
        f"{root}/xref/граница.dxf": _dxf_bytes(_work_boundary),
    }
    z = tmp_path / "n.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for n, d in files.items():
            zf.writestr(n, d)
    res = cad_package.ingest_package(z, tmp_path / "w")
    by = {s.name: s for s in res.sources}
    assert by["tp_Тест"].status == "merged" and by["tp_Тест"].kind == "tp"
    assert by["up_Тест"].status == "merged" and "nested in xref_ИТП" in by["up_Тест"].detail
    assert by["xref_граница_Тест"].kind == "work_boundary"
    d = ezdxf.readfile(str(res.merged_dxf))
    assert any(e.dxf.layer == "up_Тест$0$Теплосеть" for e in d.modelspace())


def test_block_with_unconverted_nested_block_does_not_drop_source(tmp_path):
    """A block that references a block LibreDWG did not convert (*U…) used to
    make the Importer fail and the whole source was lost."""
    def _tp_bad_block(doc, msp):
        _tp(doc, msp)
        inner = doc.blocks.new("Скамья")
        inner.add_line((0, 0), (1, 0))
        outer = doc.blocks.new("МАФ")
        outer.add_blockref("Скамья", (0, 0))
        outer.add_blockref("Скамья", (2, 0))
        msp.add_blockref("МАФ", (50, 40), dxfattribs={"layer": "МАФ"})
        # simulate the unconverted block: drop the definition, keep the reference
        doc.blocks.delete_block("Скамья", safe=False)

    name = f"Генплан_Тест - Standard/3ДЖКХ-25_00001/{TP}.dxf"
    z, z2 = _package(tmp_path), tmp_path / "b.zip"
    with zipfile.ZipFile(z) as src, zipfile.ZipFile(z2, "w") as dst:
        for info in src.infolist():
            dst.writestr(info.filename, _dxf_bytes(_tp_bad_block) if info.filename == name else src.read(info))
    res = cad_package.ingest_package(z2, tmp_path / "w")
    tp_src = next(s for s in res.sources if s.name == TP)
    assert tp_src.status == "merged"
    assert any("without a block definition dropped" in m for m in res.log)
    assert _dangling_pointers(res.merged_dxf) == 0


def test_unattached_ird_drawing_is_merged(tmp_path):
    """3-я Парковая: the survey underlay is in the package but no drawing
    references it; a copy with the same name in another folder is skipped."""
    z, z2 = _package(tmp_path), tmp_path / "o.zip"
    with zipfile.ZipFile(z) as src, zipfile.ZipFile(z2, "w") as dst:
        for info in src.infolist():
            dst.writestr(info.filename, src.read(info))
        dst.writestr("Вн.ссылки/3_ДЖКХ-25_02294.dxf", _dxf_bytes(_up))
        dst.writestr("копия ИРД/3_ДЖКХ-25_02294.dxf", _dxf_bytes(_up))
    res = cad_package.ingest_package(z2, tmp_path / "w")
    orphans = [s for s in res.sources if s.name == "3_ДЖКХ-25_02294"]
    assert len(orphans) == 1 and orphans[0].status == "merged"
    assert "not attached" in orphans[0].detail


def test_unreadable_libredwg_output_is_repaired(tmp_path):
    """A table record without a name and an MTEXT column type out of range
    break even ezdxf's recover mode — the tag-level repair makes it loadable."""
    from app.services.dxf_parser import _read_dxf
    d = ezdxf.new("R2018")
    d.layers.add("Здания")
    d.modelspace().add_mtext("x", dxfattribs={"layer": "Здания"})
    buf = io.StringIO()
    d.write(buf)
    t = buf.getvalue().replace("  2\nЗдания\n", "", 1)
    i = t.index("AcDbMText")
    t = t[:i] + "AcDbMText\n 75\n256" + t[i + len("AcDbMText"):]
    p = tmp_path / "broken.dxf"
    p.write_text(t, encoding="utf-8")
    log: list[str] = []
    doc = _read_dxf(p, log)
    assert any("tag level" in m for m in log)
    assert len(doc.modelspace().query("MTEXT")) == 1


def test_undecodable_cad_text_is_cleaned():
    """LibreDWG may write cp1251 bytes into a UTF-8 DXF; they arrive as lone
    surrogates, which JSON (document storage, API responses) refuses."""
    from app.services.dxf_parser import clean_text
    raw = "Кабель связи".encode("cp1251").decode("utf-8", "surrogateescape")
    assert clean_text(raw) == "Кабель связи"
    assert clean_text("Теплосеть") == "Теплосеть" and clean_text(None) is None
    # only the undecodable run is re-decoded: a proper UTF-8 xref prefix stays intact
    mixed = "output_ДЖКХup$0$" + "Кабель".encode("cp1251").decode("utf-8", "surrogateescape")
    assert clean_text(mixed) == "output_ДЖКХup$0$Кабель"


def test_distance_to_a_multipart_site_boundary():
    """Two separate work-boundary areas make the site a MultiPolygon; the
    candidate features must still measure the distance to its edge."""
    from shapely.geometry import MultiPolygon, Point, Polygon
    from app.services.candidates import DistanceOracle
    from app.services.geometry import GeometryIndex
    site = MultiPolygon([Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]),
                         Polygon([(20, 0), (30, 0), (30, 10), (20, 10)])])
    oracle = DistanceOracle(GeometryIndex(), site)
    assert abs(oracle.boundary_distance(Point(25, 5)) - 5.0) < 1e-9


def test_empty_copy_of_the_work_boundary_is_not_a_site_part():
    """Харьковский проезд: the base drawing keeps a copy of the work boundary
    5 km beside the drawing; only the part with content is the site."""
    from shapely.geometry import LineString, Polygon
    from app.services.geometry import GeometryIndex, parts_with_content
    from shapely.ops import unary_union
    real = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
    copy = Polygon([(-5000, 0), (-4900, 0), (-4900, 100), (-5000, 100)])
    idx = GeometryIndex()
    idx.by_entity = {"b1": real, "b2": copy,
                     **{f"r{i}": LineString([(10 + i, 10), (10 + i, 20)]) for i in range(50)}}
    idx.by_category = {"SITE_BOUNDARY": ["b1", "b2"], "ROAD": [f"r{i}" for i in range(50)]}
    site = parts_with_content(unary_union([real, copy]), idx)
    assert site.equals(real)
    assert any("work-boundary part" in m for m in idx.repair_log)
    # two real areas, both with content, stay together
    idx.by_entity["t"] = LineString([(-4950, 50), (-4940, 50)])
    idx.by_category["ROAD"].append("t")
    assert parts_with_content(unary_union([real, copy]), idx).area == real.area + copy.area


def test_model_space_lost_by_the_converter_is_detected():
    """Харьковский проезд: LibreDWG loses the model space of the survey plan —
    a few stray objects near the origin, while the header says the drawing
    lies on the site. Such a source must not pass as merged."""
    lost = ezdxf.new("R2018")
    lost.header["$EXTMIN"] = (8650, -8850, 0)
    lost.header["$EXTMAX"] = (9670, -7570, 0)
    for i in range(20):
        lost.modelspace().add_line((0.1 * i, 0), (0.1 * i, 1))
    assert "ODA File Converter" in cad_package._decoded_off_extents(lost)
    ok = ezdxf.new("R2018")
    ok.header["$EXTMIN"] = (8650, -8850, 0)
    ok.header["$EXTMAX"] = (9670, -7570, 0)
    for i in range(20):
        ok.modelspace().add_line((8700 + i, -8000), (8700 + i, -7990))
    assert cad_package._decoded_off_extents(ok) == ""
    # no valid header extents: nothing to compare against
    assert cad_package._decoded_off_extents(ezdxf.new("R2018")) == ""


def test_layout_without_block_record_is_dropped(tmp_path):
    """A paper-space LAYOUT pointing to a missing BLOCK_RECORD makes ezdxf
    refuse the whole file (lighting plan of Харьковский проезд). ezdxf copes
    with the synthetic version, so the tag-level repair is checked directly."""
    from app.services.dxf_parser import _repair_tags
    d = ezdxf.new("R2018")
    d.modelspace().add_line((0, 0), (1, 1), dxfattribs={"layer": "Фонари"})
    buf = io.StringIO()
    d.write(buf)
    t = buf.getvalue()
    lay = d.layouts.get("Layout1").dxf_layout
    # the sheet's LAYOUT object: its AcDbLayout 330 pointer now names nothing
    obj = t.index(f"\n  5\n{lay.dxf.handle}\n")
    ptr = f"330\n{lay.dxf.block_record_handle}\n"
    at = t.index(ptr, obj)
    t = t[:at] + "330\nDEAD\n" + t[at + len(ptr):]
    p = tmp_path / "no.dxf"
    p.write_text(t, encoding="utf-8")
    fixed = tmp_path / "fixed.dxf"
    counts = _repair_tags(p, fixed)
    assert counts["layouts without block record"] == 1
    assert "330\nDEAD\n" not in fixed.read_text(encoding="utf-8")
    assert len(ezdxf.readfile(fixed).modelspace()) == 1


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
