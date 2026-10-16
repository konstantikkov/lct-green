"""GreenPlan API — automatic tree/shrub placement on CAD site plans."""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import config, pipeline, store
from .models.ir import CATEGORIES, CadDocument, LayerClassification
from .services import cad_package, classification, dxf_export
from .services.dwg_converter import DwgConversionError, DwgConverter, detect_converter
from .services.explain import build_explanation
from .services.geometry import as_polygon_list
from .services.lawn import LAWN_RULE_ID, lawn_geometry
from .services.llm import LocalLLM, capabilities as llm_capabilities
from .services.rejections import cite
from .services.rules import load_registry, load_species, save_override
from .services.validator import Placement, RuleDistanceOracle, validate_plan
from .regulations.router import router as regulations_router

app = FastAPI(title="GreenPlan API", version=config.ALGORITHM_VERSION)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)
app.include_router(regulations_router)


@app.on_event("startup")
def _startup() -> None:
    store.init_db()


def _err(status: int, code: str, message: str):
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


# ---------------------------------------------------------------------------
# capabilities
# ---------------------------------------------------------------------------

@app.get("/api/capabilities")
async def get_capabilities():
    conv = detect_converter()
    return {
        "dwgConverter": {"available": conv.available, "kind": conv.kind,
                         "path": conv.path, "detail": conv.detail,
                         "fallback": "libredwg" if conv.fallback else None},
        "localLlm": await llm_capabilities(),
    }


# ---------------------------------------------------------------------------
# projects & upload
# ---------------------------------------------------------------------------

_SAFE_NAME = re.compile(r"[^\w.\-]+", re.UNICODE)


def _sanitize_filename(name: str) -> str:
    name = Path(name).name
    return _SAFE_NAME.sub("_", name)[:120] or "upload"


def _validate_signature(path: Path, ext: str) -> None:
    head = path.open("rb").read(256)
    if ext == ".zip":
        if not head.startswith(b"PK\x03\x04"):
            _err(422, "INVALID_FILE", "File does not look like a ZIP archive.")
    elif ext == ".dwg":
        if not head.startswith(b"AC10") and not head.startswith(b"AC1."):
            _err(422, "INVALID_FILE", "File does not look like a DWG (bad signature).")
    else:
        text_ok = b"SECTION" in head or head.lstrip()[:3] in (b"999", b"0\r\n", b"0\n")
        if b"AutoCAD Binary DXF" in head:
            text_ok = True
        if not text_ok:
            _err(422, "INVALID_FILE", "File does not look like a DXF (no SECTION header).")


def _render_selection(doc: CadDocument) -> list:
    """All imported geometry, including unclassified and annotation layers."""
    return doc.entities


def _document_response(pid: str, doc: CadDocument, state: str) -> dict:
    ents = []
    for e in _render_selection(doc):
        g = e.geometry
        item: dict = {"id": e.id, "sourceHandle": e.sourceHandle, "type": e.type,
                      "layer": e.layer, "kind": e.kind, "color": e.attributes.get("color")}
        if e.kind in ("polyline", "polygon"):
            item["points"] = [c for xy in g.get("points", []) for c in xy]
            item["closed"] = e.kind == "polygon" or bool(g.get("closed"))
            if g.get("radius") is not None:
                item["radius"] = g["radius"]
        elif e.kind == "point":
            item["position"] = list(g.get("position", (0, 0)))
            item["points"] = []
        elif e.kind == "text":
            item["position"] = list(g.get("position", (0, 0)))
            item["text"] = (g.get("text") or "")[:120]
            item["points"] = []
        ents.append(item)
    return {
        "projectId": pid,
        "fileName": doc.fileName,
        "format": doc.format,
        "cadVersion": doc.cadVersion,
        "units": doc.units,
        "unitsScaleToMeters": doc.unitsScaleToMeters,
        "entityCount": doc.entityCount,
        "layerCount": len(doc.layers),
        "extents": {"min": list(doc.extents.min), "max": list(doc.extents.max)},
        "state": state,
        "layers": [l.model_dump() for l in doc.layers],
        "entities": ents,
        "conversionLog": [_json_safe(m) for m in doc.conversionLog],
        # eTransmit package composition (sources, what merged, clip region)
        "package": _json_safe(store.load_json(pid, "package")),
    }


def _json_safe(obj):
    """Strings from CAD files may still carry undecodable bytes (lone
    surrogates) that JSON rendering refuses — re-decode them."""
    from .services.dxf_parser import clean_text
    if isinstance(obj, str):
        return clean_text(obj)
    if isinstance(obj, list):
        return [_json_safe(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    return obj


@app.post("/api/projects", status_code=201)
async def create_project(file: UploadFile = File(...)):
    fname = _sanitize_filename(file.filename or "upload")
    ext = Path(fname).suffix.lower()
    if ext not in config.ALLOWED_EXTENSIONS:
        _err(422, "UNSUPPORTED_EXTENSION", f"Only .dxf/.dwg/.zip supported, got '{ext}'.")

    config.ensure_dirs()
    tmp_name = f"{uuid.uuid4().hex}{ext}"
    upload_path = config.DATA_DIR / "uploads" / tmp_name
    size = 0
    with upload_path.open("wb") as out:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > config.MAX_UPLOAD_BYTES:
                out.close()
                upload_path.unlink(missing_ok=True)
                _err(413, "FILE_TOO_LARGE", "Upload exceeds size limit.")
            out.write(chunk)
    _validate_signature(upload_path, ext)

    fmt = {".dwg": "DWG", ".zip": "PACKAGE"}.get(ext, "DXF")
    pid = store.create_project(Path(fname).stem, fname, fmt, str(upload_path),
                               pipeline.file_hash(upload_path))

    # convert if needed
    dxf_path = upload_path
    conversion_log: list[str] = []
    package: cad_package.PackageResult | None = None
    if fmt == "PACKAGE":
        # base drawing + xrefs → one merged DXF clipped to the work boundary
        store.update_project(pid, state="CONVERTING")
        workdir = config.DATA_DIR / "uploads" / f"{uuid.uuid4().hex}_pkg"
        try:
            package = await asyncio.to_thread(cad_package.ingest_package_cached, upload_path, workdir)
        except cad_package.PackageError as exc:
            store.update_project(pid, state="FAILED")
            shutil.rmtree(workdir, ignore_errors=True)
            _err(422, exc.code, str(exc))
        except DwgConversionError as exc:
            store.update_project(pid, state="FAILED")
            shutil.rmtree(workdir, ignore_errors=True)
            _err(422, exc.code, str(exc))
        finally:
            for tmp in ("unpacked", "converted"):
                shutil.rmtree(workdir / tmp, ignore_errors=True)
        dxf_path = package.merged_dxf
        conversion_log.extend(package.log)
    elif fmt == "DWG":
        store.update_project(pid, state="CONVERTING")
        converter = DwgConverter()
        target = config.DATA_DIR / "uploads" / f"{uuid.uuid4().hex}.dxf"
        try:
            dxf_path = await asyncio.to_thread(converter.convert_to_dxf, upload_path, target)
            conversion_log.append(f"DWG converted via {converter.info.kind} ({converter.info.path})")
        except DwgConversionError as exc:
            store.update_project(pid, state="FAILED")
            _err(422, exc.code, str(exc))

    store.update_project(pid, dxf_path=str(dxf_path), state="PARSING")
    from .services.dxf_parser import parse_dxf_cached
    try:
        doc = await asyncio.to_thread(parse_dxf_cached, dxf_path, pid, fname, fmt, conversion_log)
    except Exception as exc:  # noqa: BLE001
        store.update_project(pid, state="FAILED")
        _err(422, "PARSE_FAILED", f"DXF parsing failed: {exc}")

    store.update_project(pid, state="CLASSIFYING")
    classification.classify_document(doc)
    if package is not None:
        doc.conversionLog.extend(cad_package.apply_source_classification(
            doc, package.sources, package.boundary_layers))
        store.save_json(pid, "package", package.to_api())
    llm = LocalLLM()
    try:
        await asyncio.wait_for(classification.refine_with_llm(doc, llm), timeout=25)
    except Exception:
        pass

    rt = pipeline.put_runtime(pid, doc)
    pipeline.ensure_chunks(rt)
    store.save_json(pid, "document", doc.model_dump())
    # lightweight meta for fast admin/dashboard listings (document blob is huge)
    store.save_json(pid, "meta", {
        "format": fmt, "cadVersion": doc.cadVersion, "units": doc.units,
        "entityCount": doc.entityCount, "layerCount": len(doc.layers),
        "uncertainLayers": sum(1 for l in doc.layers if l.classification.requiresReview),
    })
    try:
        store.save_file_blob(pid, "source_dxf", Path(dxf_path))
    except Exception:
        pass  # durability extra; disk copy remains authoritative
    store.update_project(pid, state="UPLOADED")
    return _document_response(pid, doc, "UPLOADED")


@app.get("/api/projects")
def list_projects():
    return [{"id": p["id"], "name": p["name"], "fileName": p["file_name"],
             "createdAt": p["created_at"], "state": p["state"]}
            for p in store.list_projects()]


@app.get("/api/projects/summary")
def list_projects_summary():
    """Enriched listing for the admin start screen: CAD meta (stored at upload),
    iteration count and selected-result metrics, export availability."""
    out = []
    for p in store.list_projects():
        meta = store.load_json(p["id"], "meta") or {}
        iters = store.list_iterations(p["id"])
        selected = next((r for r in iters if r.get("selected")), iters[-1] if iters else None)
        result = None
        if selected:
            m = selected.get("metrics", {})
            result = {
                "iterationCount": len(iters),
                "treeCount": m.get("treeCount"), "shrubCount": m.get("shrubCount"),
                "hardViolations": len(selected.get("validation", {}).get("hardViolations", [])),
                "coverageScore": m.get("coverageScore"),
                "distributionScore": m.get("distributionScore"),
                "judgeDecision": (selected.get("judge") or {}).get("decision"),
            }
        out.append({
            "id": p["id"], "name": p["name"], "fileName": p["file_name"],
            "createdAt": p["created_at"], "state": p["state"],
            "format": meta.get("format"), "units": meta.get("units"),
            "entityCount": meta.get("entityCount"), "layerCount": meta.get("layerCount"),
            "uncertainLayers": meta.get("uncertainLayers"),
            "result": result,
            "exports": {"dxf": f"/api/projects/{p['id']}/export/dxf",
                        "report": f"/api/projects/{p['id']}/export/report"} if result else None,
        })
    return out


@app.get("/api/projects/{pid}/compliance")
def compliance_audit(pid: str):
    """Document-law compliance audit for the selected iteration: every placement
    is checked against every effective rule (YAML registry + ACTIVE document-
    derived REG_* rules) with exact distances and source citations. Deterministic
    geometry only — this is the legal-audit view over the validator/explanation
    machinery, not an LLM opinion."""
    rt = _get_rt(pid)
    rec = _selected_iteration(pid)
    registry = pipeline.ensure_registry(rt)
    idx = pipeline.ensure_geometry(rt)
    constraints = pipeline.ensure_constraints(rt)
    oracle = RuleDistanceOracle(idx)

    by_document: dict[str, dict] = {}
    by_rule: dict[str, dict] = {}
    placements_out = []
    compliant = 0
    checks_total = 0

    all_placements = [Placement(id=p["id"], kind=p["kind"], speciesId=p["speciesId"],
                                x=p["position"][0], y=p["position"][1],
                                crownRadiusM=p["crownRadiusM"], score=p["score"])
                      for p in rec["placements"]]

    for p, pl in zip(rec["placements"], all_placements):
        exp = build_explanation(pl, rec["index"], registry, oracle,
                                constraints.site_boundary,
                                site_boundary_source=constraints.site_boundary_kind)
        failing = []
        checks = []
        for c in exp["checks"]:
            if c["result"] == "NOT_APPLICABLE":
                continue
            checks_total += 1
            doc_name = c["source"]["document"]
            bd = by_document.setdefault(doc_name, {"checks": 0, "passed": 0,
                                                   "failed": 0, "unverified": 0})
            br = by_rule.setdefault(c["ruleId"], {
                "checks": 0, "passed": 0, "failed": 0, "unverified": 0,
                "document": doc_name, "effectiveSource": c["effectiveSource"],
                "requiredM": c["requiredM"]})
            bd["checks"] += 1
            br["checks"] += 1
            if c["result"] == "PASS":
                bd["passed"] += 1
                br["passed"] += 1
            elif c["result"] == "UNVERIFIED":
                bd["unverified"] += 1
                br["unverified"] += 1
            else:  # FAIL
                bd["failed"] += 1
                br["failed"] += 1
                failing.append(c["ruleId"])
            checks.append(c)
        verdict = "COMPLIANT" if not failing else "VIOLATIONS"
        if not failing:
            compliant += 1
        placements_out.append({
            "id": p["id"], "kind": p["kind"], "speciesId": p["speciesId"],
            "position": p["position"], "verdict": verdict,
            "failingRules": failing,
            "checks": checks,
        })

    # deterministic regulatory verdict, recomputed against the CURRENT
    # effective registry (this endpoint is a fresh audit, not a cached view)
    revalidation = validate_plan(all_placements, idx, registry,
                                 constraints.site_boundary,
                                 constraints.allowed_area,
                                 site_boundary_source=constraints.site_boundary_kind,
                                 visibility_zones=constraints.visibility_zones())

    reg_rules = [r for r in registry.rules if r.id.startswith("REG_")]
    return {
        "iterationId": rec["iterationId"],
        "rulesetVersion": registry.version,
        "documentDerivedRules": len(reg_rules),
        "verifiedRules": sum(1 for r in registry.rules if r.effective_source == "verified"),
        "assumedRules": sum(1 for r in registry.rules if r.effective_source == "assumption"),
        "regulatory": revalidation.get("regulatory"),
        "summary": {
            "placements": len(placements_out),
            "compliant": compliant,
            "withViolations": len(placements_out) - compliant,
            "checksTotal": checks_total,
        },
        "byDocument": by_document,
        "byRule": by_rule,
        "placements": placements_out,
    }


def _get_rt(pid: str) -> pipeline.ProjectRuntime:
    rt = pipeline.get_runtime(pid)
    if rt is None:
        _err(404, "PROJECT_NOT_FOUND", f"No project {pid}")
    return rt


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    if not store.delete_project(pid):
        _err(404, "PROJECT_NOT_FOUND", f"No project {pid}")
    with pipeline._rt_lock:
        pipeline._runtimes.pop(pid, None)
    return {"deleted": pid}


@app.get("/api/projects/{pid}/document")
def get_document(pid: str):
    rt = _get_rt(pid)
    p = store.get_project(pid) or {}
    return _document_response(pid, rt.doc, p.get("state", "UPLOADED"))


@app.get("/api/projects/{pid}/chunks")
def get_chunks(pid: str):
    rt = _get_rt(pid)
    chunks = pipeline.ensure_chunks(rt)
    return [{k: v for k, v in c.items() if k != "entityIds"} for c in chunks]


class ClassificationPatch(BaseModel):
    category: str


@app.patch("/api/projects/{pid}/layers/{layer_name}/classification")
def patch_layer(pid: str, layer_name: str, patch: ClassificationPatch):
    if patch.category not in CATEGORIES:
        _err(422, "INVALID_CATEGORY", f"Unknown category {patch.category}")
    rt = _get_rt(pid)
    lm = rt.doc.layer_map()
    li = lm.get(layer_name)
    if li is None:
        _err(404, "LAYER_NOT_FOUND", f"No layer '{layer_name}'")
    li.classification = LayerClassification(
        category=patch.category, confidence=1.0, source="user", requiresReview=False)
    rt.invalidate_semantics()
    store.save_json(pid, "document", rt.doc.model_dump())
    return li.model_dump()


# ---------------------------------------------------------------------------
# rules & species
# ---------------------------------------------------------------------------

@app.get("/api/rules")
def get_rules():
    reg = load_registry()
    return [r.to_api() for r in reg.rules]


class AssumptionPatch(BaseModel):
    assumedDistanceM: float | None = None
    enabled: bool | None = None


class RulePatch(BaseModel):
    enabled: bool | None = None
    assumption: AssumptionPatch | None = None


@app.patch("/api/rules/{rule_id}")
def patch_rule(rule_id: str, patch: RulePatch):
    reg = load_registry()
    rule = reg.get(rule_id)
    if rule is None:
        _err(404, "RULE_NOT_FOUND", f"No rule {rule_id}")
    save_override(rule_id, {
        "enabled": patch.enabled,
        "assumption": patch.assumption.model_dump() if patch.assumption else None,
    })
    # constraints caches for all runtimes are invalidated lazily on next build
    for rt in list(pipeline._runtimes.values()):
        rt.invalidate_semantics()
    reg = load_registry()
    return reg.get(rule_id).to_api()


@app.get("/api/species")
def get_species():
    return load_species()


# ---------------------------------------------------------------------------
# constraints
# ---------------------------------------------------------------------------

@app.get("/api/projects/{pid}/constraints")
def get_constraints(pid: str):
    rt = _get_rt(pid)
    result = pipeline.ensure_constraints(rt)
    return result.to_api()


# ---------------------------------------------------------------------------
# generation runs
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    plantKinds: list[str] = ["TREE"]
    speciesIds: list[str] = []
    strategy: str = "balanced"
    targetCount: int | None = None
    seed: int = config.PLACEMENT_RANDOM_SEED
    maxIterations: int = config.MAX_ITERATIONS_DEFAULT
    useLlmJudge: bool = True


@app.post("/api/projects/{pid}/generate", status_code=202)
def generate(pid: str, req: GenerateRequest):
    _get_rt(pid)  # ensure loaded
    run_id = pipeline.start_generation(pid, req.model_dump())
    return {"runId": run_id}


@app.get("/api/projects/{pid}/runs/{run_id}")
def run_status(pid: str, run_id: str):
    st = pipeline.get_run_status(run_id)
    if st is None:
        _err(404, "RUN_NOT_FOUND", f"No run {run_id}")
    return st


@app.get("/api/projects/{pid}/runs/{run_id}/events")
async def run_events(pid: str, run_id: str):
    async def gen():
        last_version = -1
        for _ in range(1200):  # max ~10 min
            raw = pipeline._run_status.get(run_id)
            if raw is None:
                break
            if raw["_version"] != last_version:
                last_version = raw["_version"]
                yield f"data: {json.dumps(pipeline.get_run_status(run_id))}\n\n"
            if raw["state"] in ("COMPLETED", "FAILED"):
                break
            await asyncio.sleep(0.4)
        yield "event: end\ndata: {}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


# ---------------------------------------------------------------------------
# iterations & explanations
# ---------------------------------------------------------------------------

_SUMMARY_KEYS = ("iterationId", "index", "runId", "selected", "parameters", "seed",
                 "metrics", "regulatory", "judge", "runtimeMs")


@app.get("/api/projects/{pid}/iterations")
def list_iterations(pid: str):
    _get_rt(pid)
    out = []
    for rec in store.list_iterations(pid):
        s = {k: rec.get(k) for k in _SUMMARY_KEYS}
        s["metrics"] = {**s["metrics"], "hardViolations":
                        len(rec.get("validation", {}).get("hardViolations", []))}
        out.append(s)
    return out


def _find_iteration(pid: str, iteration_id: str) -> dict:
    for rec in store.list_iterations(pid):
        if rec["iterationId"] == iteration_id:
            return rec
    _err(404, "ITERATION_NOT_FOUND", f"No iteration {iteration_id}")


@app.get("/api/projects/{pid}/iterations/{iteration_id}")
def get_iteration(pid: str, iteration_id: str):
    _get_rt(pid)
    return _find_iteration(pid, iteration_id)


@app.get("/api/projects/{pid}/placements/check")
def live_position_check(pid: str, x: float, y: float, kind: str = "TREE",
                        speciesId: str = "tree_linden",
                        excludeId: str | None = None):
    """Stateless live check for drag preview: how does a hypothetical position
    correlate with every effective document condition. Deterministic geometry
    only; no writes. Called throttled while the user drags a planting."""
    rt = _get_rt(pid)
    registry = pipeline.ensure_registry(rt)
    idx = pipeline.ensure_geometry(rt)
    constraints = pipeline.ensure_constraints(rt)
    species = next((s for s in load_species() if s["id"] == speciesId), None)
    crown = species["parameters"]["crownRadiusM"] if species else 2.0

    pl = Placement(id=excludeId or "probe", kind=kind, speciesId=speciesId,
                   x=x, y=y, crownRadiusM=crown)
    oracle = RuleDistanceOracle(idx)
    exp = build_explanation(pl, 0, registry, oracle, constraints.site_boundary,
                            site_boundary_source=constraints.site_boundary_kind)

    # same-kind spacing vs other placements of the selected iteration
    spacing = None
    try:
        rec = _selected_iteration(pid)
        others = [q for q in rec["placements"]
                  if q["kind"] == kind and q["id"] != excludeId]
        if others:
            import math as _m
            nearest = min(_m.dist((x, y), tuple(q["position"])) for q in others)
            srule = next((r for r in registry.rules_for(kind, {"min_spacing"})
                          if r.effective_distance_m), None)
            if srule:
                spacing = {"ruleId": srule.id, "nearestM": round(nearest, 2),
                           "requiredM": srule.effective_distance_m,
                           "role": srule.role,
                           "result": "PASS" if nearest >= srule.effective_distance_m - 1e-9
                                     else ("WARNING" if srule.role == "design_heuristic"
                                           else "FAIL")}
    except HTTPException:
        pass  # no generation yet — pure position probe

    from shapely.geometry import Point as _P
    inside_allowed = bool(constraints.allowed_area and
                          constraints.allowed_area.covers(_P(x, y)))
    in_visibility = any(z.geometry.covers(_P(x, y))
                        for z in constraints.visibility_zones())

    checks = [c for c in exp["checks"] if c["result"] != "NOT_APPLICABLE"]
    has_fail = any(c["result"] == "FAIL" for c in checks) or in_visibility or \
               (spacing is not None and spacing["result"] == "FAIL")
    has_unverified = any(c["result"] == "UNVERIFIED" for c in checks)
    verdict = ("VIOLATIONS" if has_fail
               else "UNVERIFIED" if has_unverified else "OK")
    return {"position": [round(x, 3), round(y, 3)], "verdict": verdict,
            "insideAllowed": inside_allowed, "inVisibilityTriangle": in_visibility,
            "spacing": spacing, "checks": checks}


class MoveRequest(BaseModel):
    position: tuple[float, float]
    iterationId: str | None = None


@app.post("/api/projects/{pid}/placements/{placement_id}/move")
def move_placement(pid: str, placement_id: str, body: MoveRequest):
    """Persist a manual move: update the placement, recompute its distances,
    re-run the FULL deterministic validation + regulatory verdict for the
    iteration and store it (with a manual-edit audit trail)."""
    import time as _time
    rt = _get_rt(pid)
    registry = pipeline.ensure_registry(rt)
    idx = pipeline.ensure_geometry(rt)
    constraints = pipeline.ensure_constraints(rt)

    rec = (_find_iteration(pid, body.iterationId) if body.iterationId
           else _selected_iteration(pid))
    target = next((q for q in rec["placements"] if q["id"] == placement_id), None)
    if target is None:
        _err(404, "PLACEMENT_NOT_FOUND", f"No placement {placement_id} in iteration")

    old_pos = list(target["position"])
    nx, ny = float(body.position[0]), float(body.position[1])
    target["position"] = [round(nx, 3), round(ny, 3)]
    target["manuallyMoved"] = True

    # recompute this placement's stored distances
    from .services.candidates import DistanceOracle
    d_oracle = DistanceOracle(idx, constraints.site_boundary)
    from shapely.geometry import Point as _P
    feats = d_oracle.features_for(_P(nx, ny))
    target["distances"] = {k: (round(v, 2) if v is not None else None)
                           for k, v in feats.items()}

    placements = [Placement(id=q["id"], kind=q["kind"], speciesId=q["speciesId"],
                            x=q["position"][0], y=q["position"][1],
                            crownRadiusM=q["crownRadiusM"], score=q.get("score", 0))
                  for q in rec["placements"]]
    validation = validate_plan(placements, idx, registry,
                               constraints.site_boundary, constraints.allowed_area,
                               site_boundary_source=constraints.site_boundary_kind,
                               visibility_zones=constraints.visibility_zones())
    rec["validation"] = validation
    rec["regulatory"] = validation.get("regulatory")
    rec["metrics"] = {**rec.get("metrics", {}), **validation.get("metrics", {})}
    rec.setdefault("manualEdits", []).append({
        "placementId": placement_id, "from": old_pos,
        "to": target["position"], "at": _time.time()})
    store.save_json(pid, f"iteration_{rec['runId']}_{rec['index']}", rec)

    return {"placement": target, "validation": {
                "valid": validation["valid"],
                "hardViolations": validation["hardViolations"],
                "warnings": validation.get("warnings", [])},
            "regulatory": validation.get("regulatory"),
            "metrics": rec["metrics"]}


@app.get("/api/projects/{pid}/placements/{placement_id}/explanation")
async def get_explanation(pid: str, placement_id: str):
    rt = _get_rt(pid)
    registry = pipeline.ensure_registry(rt)
    idx = pipeline.ensure_geometry(rt)
    constraints = pipeline.ensure_constraints(rt)

    found = None
    for rec in store.list_iterations(pid):
        for p in rec["placements"]:
            if p["id"] == placement_id:
                found = (rec, p)
    if not found:
        _err(404, "PLACEMENT_NOT_FOUND", f"No placement {placement_id}")
    rec, p = found
    placement = Placement(id=p["id"], kind=p["kind"], speciesId=p["speciesId"],
                          x=p["position"][0], y=p["position"][1],
                          crownRadiusM=p["crownRadiusM"], score=p["score"])
    oracle = RuleDistanceOracle(idx)
    exp = build_explanation(placement, rec["index"], registry, oracle,
                            constraints.site_boundary,
                            site_boundary_source=constraints.site_boundary_kind)

    # optional local-LLM narrative from deterministic facts only
    llm = LocalLLM()
    if llm.enabled and await llm.is_reachable():
        try:
            from pydantic import BaseModel, Field as BM

            class Narrative(BM):
                narrative: str

            facts = {"summary": exp["summary"],
                     "checks": [{k: c[k] for k in ("ruleId", "result", "actualM", "requiredM")}
                                for c in exp["checks"]]}
            r = await asyncio.wait_for(llm.structured_generate(Narrative, [
                {"role": "system",
                 "content": "Rephrase placement-check facts as one short human paragraph (Russian). "
                            "Do NOT add numbers, documents or clauses not present in the facts."},
                {"role": "user", "content": json.dumps(facts, ensure_ascii=False)},
            ]), timeout=20)
            exp["llmNarrative"] = r.narrative[:1200]
        except Exception:
            pass
    return exp


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

def _selected_iteration(pid: str) -> dict:
    recs = store.list_iterations(pid)
    if not recs:
        _err(409, "NO_RESULT", "No generation results yet.")
    sel = next((r for r in recs if r.get("selected")), recs[-1])
    return sel


@app.get("/api/projects/{pid}/export/dxf")
def export_dxf(pid: str, layers: str = "all"):
    """layers=all — source drawing + AI_GREENING_* layers (for xref packages:
    the merged, work-boundary-clipped underlay); layers=result — ONLY the
    AI_GREENING_* layers in the same coordinates, to attach to the designer's
    own general plan as an external reference."""
    if layers not in ("all", "result"):
        _err(422, "INVALID_PARAM", "layers must be 'all' or 'result'")
    rt = _get_rt(pid)
    proj = store.get_project(pid)
    rec = _selected_iteration(pid)
    constraints = pipeline.ensure_constraints(rt)
    registry = pipeline.ensure_registry(rt)

    placements = [Placement(id=p["id"], kind=p["kind"], speciesId=p["speciesId"],
                            x=p["position"][0], y=p["position"][1],
                            crownRadiusM=p["crownRadiusM"], score=p["score"])
                  for p in rec["placements"]]
    stem = Path(proj["file_name"]).stem
    if layers == "result":
        out = config.OUTPUT_DIR / f"{stem}_greening_layers.dxf"
        src_dxf = dxf_export.empty_like(rt.doc.unitsScaleToMeters,
                                        config.OUTPUT_DIR / f"{stem}_empty.dxf")
    else:
        out = config.OUTPUT_DIR / f"{stem}_greening.dxf"
        src_dxf = Path(proj["dxf_path"])
        if not src_dxf.exists():  # e.g. server disk replaced — restore from DB blob
            if not store.restore_file_blob(pid, "source_dxf", src_dxf):
                _err(409, "SOURCE_MISSING", "Original DXF unavailable for export.")
    dxf_export.export_greening_dxf(
        src_dxf, out, placements,
        constraints.allowed_area, constraints.exclusion_zones,
        rt.doc.unitsScaleToMeters, rec["index"], registry.version,
        rejected_samples=(rec.get("rejections") or {}).get("samples"),
        lawn=lawn_geometry(rec.get("lawn")))
    return FileResponse(out, filename=out.name, media_type="application/dxf")


_OBJECT_NAMES = {"BUILDING": "здание", "ROAD": "проезжая часть", "SIDEWALK": "тротуар",
                 "PARKING": "парковка", "CURB": "бортовой камень"}
_GEOMETRIC_REASONS = {
    "OUTSIDE_SITE": "вне границы участка",
    "NOT_PLANTING_AREA": "вне области посадки (не проектный газон / не зона озеленения)",
    "NARROW_STRIP": "узкая полоса меньше 1 м между запретными зонами",
}


def _cited_rejections(rej: dict | None, registry) -> dict | None:
    """Rejection reasons with the norm behind each: rule ids resolve to their
    source (document, clause, also cited acts); geometric reasons get a
    description; OUTSIDE_SITE cites the inside-site rule."""
    from .services.rejections import cite
    if not rej:
        return rej
    out = []
    for item in rej.get("byReason", []):
        key = item["key"]
        entry = dict(item)
        if key.startswith("ON_OBJECT:"):
            cat = key.split(":", 1)[1]
            entry["description"] = f"на объекте: {_OBJECT_NAMES.get(cat, cat)}"
        elif key in _GEOMETRIC_REASONS:
            entry["description"] = _GEOMETRIC_REASONS[key]
            if key == "OUTSIDE_SITE":
                entry["source"] = cite(registry, "PLANTING_INSIDE_SITE_BOUNDARY")
        else:
            entry["source"] = cite(registry, key)
            role = (entry["source"] or {}).get("role")
            if role == "safe_buffer":
                entry["description"] = "технологический буфер проекта — не нормативное требование"
            elif role == "design_heuristic":
                entry["description"] = "проектная рекомендация — не нормативное требование"
        out.append(entry)
    return {**rej, "byReason": out}


@app.get("/api/projects/{pid}/export/report")
def export_report(pid: str):
    rt = _get_rt(pid)
    proj = store.get_project(pid)
    rec = _selected_iteration(pid)
    registry = pipeline.ensure_registry(rt)
    idx = pipeline.ensure_geometry(rt)
    constraints = pipeline.ensure_constraints(rt)
    oracle = RuleDistanceOracle(idx)

    explanations = []
    for p in rec["placements"]:
        pl = Placement(id=p["id"], kind=p["kind"], speciesId=p["speciesId"],
                       x=p["position"][0], y=p["position"][1],
                       crownRadiusM=p["crownRadiusM"], score=p["score"])
        explanations.append(build_explanation(pl, rec["index"], registry, oracle,
                                              constraints.site_boundary,
                                              site_boundary_source=constraints.site_boundary_kind))

    regulatory = rec.get("regulatory") or rec.get("validation", {}).get("regulatory")
    reg_status = (regulatory or {}).get("status")
    if reg_status == "COMPLIANT":
        headline = ("РЕГУЛЯТОРНЫЙ СТАТУС: COMPLIANT — все применимые жесткие "
                    "проверки пройдены по верифицированным значениям.")
    else:
        headline = (f"РЕГУЛЯТОРНЫЙ СТАТУС: {reg_status or 'НЕ ОПРЕДЕЛЕН'} — "
                    "нормативное соответствие НЕ УСТАНОВЛЕНО (regulatory "
                    "compliance NOT established). См. блок 'regulatory': "
                    "непроверенные значения и блокеры перечислены там.")

    report = {
        "project": {"id": pid, "fileName": proj["file_name"],
                    "fileHash": proj["file_hash"], "units": rt.doc.units,
                    "cadVersion": rt.doc.cadVersion},
        # deterministic regulatory verdict — prominent, never derived from the
        # LLM judge (whose decision is only about optimization continuation)
        "headline": headline,
        "regulatory": regulatory,
        "observability": rec.get("observability", {}),
        "parameters": rec.get("parameters", {}),
        "seed": rec.get("seed"),
        "metrics": rec.get("metrics", {}),
        "validation": rec.get("validation", {}),
        "judge": rec.get("judge"),
        "rulesApplied": [r.to_api() for r in registry.rules],
        "inactiveRules": constraints.inactive_rules,
        "repairLog": constraints.repair_log,
        "placements": rec.get("placements", []),
        "explanations": explanations,
        "rejections": _cited_rejections(rec.get("rejections"), registry),
        # recommended lawn (ТЗ §2 п. 3) with its norm; geometry is in the DXF
        "lawn": ({**{k: v for k, v in rec["lawn"].items() if k != "polygons"},
                  "source": cite(registry, LAWN_RULE_ID)} if rec.get("lawn") else None),
        "disclaimer": headline + " Rules with status=missing_source_data use explicitly "
                      "flagged ASSUMED demo distances (see effectiveSource per check); "
                      "they are NOT verified regulatory values. The LLM judge decision "
                      "(ACCEPT/RERUN) concerns optimization quality only and NEVER "
                      "asserts regulatory validity.",
    }
    stem = Path(proj["file_name"]).stem
    out = config.OUTPUT_DIR / f"{stem}_greening_report.json"
    dxf_export.export_report(out, report)
    return FileResponse(out, filename=out.name, media_type="application/json")

# Portable Cycles package + original CAD depiction, independent of classifications.
@app.get('/api/projects/{pid}/export/render-bundle')
def export_render_bundle(pid: str):
    from .services.local_render import write_bundle
    rt = _get_rt(pid)
    rec = _selected_iteration(pid)
    # Bonus visualization always follows a successful final DXF export.
    dxf_response = export_dxf(pid)
    out = config.OUTPUT_DIR / f'{pid}_local_render.zip'
    write_bundle(out, rt.doc, rec, Path(dxf_response.path))
    return FileResponse(out, filename=out.name, media_type='application/zip')


@app.get('/api/projects/{pid}/cad-preview.svg')
def cad_preview(pid: str):
    """Source modelspace via ezdxf's CAD drawing frontend (text, hatch, blocks).

    Includes every source layer irrespective of its semantic classification;
    original CAD visibility and styles are respected. This is a 2D CAD preview,
    not a promise of identical rendering of proprietary AutoCAD proxy entities.
    """
    from ezdxf.addons.drawing import Frontend, RenderContext, layout, svg
    from ezdxf.addons.drawing.config import Configuration, ColorPolicy, BackgroundPolicy
    from .services.dxf_parser import _read_dxf
    _get_rt(pid)
    proj = store.get_project(pid)
    src = Path(proj['dxf_path'])
    if not src.exists() and not store.restore_file_blob(pid, 'source_dxf', src):
        _err(409, 'SOURCE_MISSING', 'Source drawing unavailable.')
    doc = _read_dxf(src, [])
    backend = svg.SVGBackend()
    conf = Configuration(color_policy=ColorPolicy.COLOR,
                         background_policy=BackgroundPolicy.WHITE)
    Frontend(RenderContext(doc), backend, config=conf).draw_layout(doc.modelspace(), finalize=True)
    out = config.OUTPUT_DIR / f'{pid}_cad.svg'
    out.write_text(backend.get_string(layout.Page(0, 0)), encoding='utf-8')
    return FileResponse(out, media_type='image/svg+xml')

class LayerMappingRequest(BaseModel):
    mappings: dict[str, str]


@app.put('/api/projects/{pid}/layer-mapping')
def set_layer_mapping(pid: str, request: LayerMappingRequest):
    """User-defined semantic mapping for arbitrary CAD names; drawing unaffected."""
    rt = _get_rt(pid)
    lm = rt.doc.layer_map()
    invalid = {name: category for name, category in request.mappings.items()
               if name not in lm or category not in CATEGORIES}
    if invalid:
        _err(422, 'INVALID_MAPPING', 'Every key must be an imported layer and every value a supported category.')
    for name, category in request.mappings.items():
        lm[name].classification = LayerClassification(category=category, confidence=1,
                                                       source='user', requiresReview=False)
    rt.invalidate_semantics()
    store.save_json(pid, 'document', rt.doc.model_dump())
    return {'layers': [li.model_dump() for li in rt.doc.layers], 'regenerateRequired': True}


@app.post('/api/projects/{pid}/refresh-import')
def refresh_import(pid: str):
    """Refresh saved preview geometry; preserve user mappings and original input.

    Existing results are retained as historical results; regenerate after refresh
    before relying on their compliance checks against the expanded import.
    """
    from .services.dxf_parser import parse_dxf
    old = _get_rt(pid)
    proj = store.get_project(pid)
    src = Path(proj['dxf_path'])
    if not src.exists() and not store.restore_file_blob(pid, 'source_dxf', src):
        _err(409, 'SOURCE_MISSING', 'Source drawing unavailable.')
    doc = parse_dxf(src, old.doc.documentId, old.doc.fileName, old.doc.format)
    classification.classify_document(doc)
    old_layers = old.doc.layer_map()
    for li in doc.layers:
        previous = old_layers.get(li.name)
        if previous and previous.classification.source in ('user', 'package'):
            li.classification = previous.classification
    doc.conversionLog.append('Import refreshed; regenerate the plan before checking compliance against the expanded geometry.')
    pipeline.put_runtime(pid, doc)
    store.save_json(pid, 'document', doc.model_dump())
    return {'regenerateRequired': True, 'entityCount': len(doc.entities), 'layerCount': len(doc.layers)}

class RenderSettings(BaseModel):
    detail: Literal["presentation", "cad"] = "presentation"
    view: Literal["aerial", "pedestrian"] = "aerial"
    width: int = Field(default=1280, ge=320, le=3840)
    height: int = Field(default=720, ge=240, le=2160)
    samples: int = Field(default=128, ge=16, le=512)
    azimuth: float = Field(default=225, ge=0, le=360)
    elevation: float = Field(default=23, ge=5, le=85)
    distance: float = Field(default=.8, ge=.2, le=3)
    exposure: float = Field(default=-.2, ge=-4, le=4)
    target_x: float | None = Field(default=None, allow_inf_nan=False)
    target_y: float | None = Field(default=None, allow_inf_nan=False)


@app.post('/api/projects/{pid}/renders', status_code=202)
def create_render(pid: str, settings: RenderSettings):
    """Ray trace the selected plan with local CPU Blender; bounded single-worker queue."""
    from .services import render_jobs
    from .services.local_render import scene_data
    rt = _get_rt(pid)
    rec = _selected_iteration(pid)
    export_dxf(pid)
    try:
        jid = render_jobs.submit(pid, scene_data(rt.doc, rec), settings.model_dump())
    except FileNotFoundError as exc:
        _err(503, 'BLENDER_UNAVAILABLE', str(exc))
    except RuntimeError as exc:
        _err(429, 'RENDER_QUEUE_FULL', str(exc))
    return {'jobId':jid, 'statusUrl':f'/api/projects/{pid}/renders/{jid}',
            'imageUrl':f'/api/projects/{pid}/renders/{jid}/image'}


@app.get('/api/projects/{pid}/renders/{jid}')
def get_render_status(pid: str, jid: str):
    from .services import render_jobs
    _get_rt(pid)
    try:
        return render_jobs.status(pid,jid)
    except (ValueError, FileNotFoundError):
        _err(404, 'RENDER_NOT_FOUND', 'No such render.')


@app.get('/api/projects/{pid}/renders/{jid}/image')
def get_render_image(pid: str, jid: str):
    from .services import render_jobs
    _get_rt(pid)
    try:
        if render_jobs.status(pid,jid)['state'] != 'COMPLETED':
            _err(409, 'RENDER_NOT_READY', 'The render is not complete.')
        image = render_jobs.job_dir(pid,jid)/'render.png'
    except (ValueError, FileNotFoundError):
        _err(404, 'RENDER_NOT_FOUND', 'No such render.')
    return FileResponse(image, media_type='image/png', filename=f'{pid}_render.png')
