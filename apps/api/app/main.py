"""GreenPlan API — automatic tree/shrub placement on CAD site plans."""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from . import config, pipeline, store
from .models.ir import CATEGORIES, CadDocument, LayerClassification
from .services import classification, dxf_export
from .services.dwg_converter import DwgConversionError, DwgConverter, detect_converter
from .services.explain import build_explanation
from .services.geometry import as_polygon_list
from .services.llm import LocalLLM, capabilities as llm_capabilities
from .services.rules import load_registry, load_species, save_override
from .services.validator import Placement, RuleDistanceOracle
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
                         "path": conv.path, "detail": conv.detail},
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
    if ext == ".dwg":
        if not head.startswith(b"AC10") and not head.startswith(b"AC1."):
            _err(422, "INVALID_FILE", "File does not look like a DWG (bad signature).")
    else:
        text_ok = b"SECTION" in head or head.lstrip()[:3] in (b"999", b"0\r\n", b"0\n")
        if b"AutoCAD Binary DXF" in head:
            text_ok = True
        if not text_ok:
            _err(422, "INVALID_FILE", "File does not look like a DXF (no SECTION header).")


def _document_response(pid: str, doc: CadDocument, state: str) -> dict:
    ents = []
    for e in doc.entities[:config.MAX_RENDER_ENTITIES]:
        g = e.geometry
        item: dict = {"id": e.id, "sourceHandle": e.sourceHandle, "type": e.type,
                      "layer": e.layer, "kind": e.kind}
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
        "conversionLog": doc.conversionLog,
    }


@app.post("/api/projects", status_code=201)
async def create_project(file: UploadFile = File(...)):
    fname = _sanitize_filename(file.filename or "upload")
    ext = Path(fname).suffix.lower()
    if ext not in config.ALLOWED_EXTENSIONS:
        _err(422, "UNSUPPORTED_EXTENSION", f"Only .dxf/.dwg supported, got '{ext}'.")

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

    fmt = "DWG" if ext == ".dwg" else "DXF"
    pid = store.create_project(Path(fname).stem, fname, fmt, str(upload_path),
                               pipeline.file_hash(upload_path))

    # convert if needed
    dxf_path = upload_path
    conversion_log: list[str] = []
    if fmt == "DWG":
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
    from .services.dxf_parser import parse_dxf
    try:
        doc = await asyncio.to_thread(parse_dxf, dxf_path, pid, fname, fmt, conversion_log)
    except Exception as exc:  # noqa: BLE001
        store.update_project(pid, state="FAILED")
        _err(422, "PARSE_FAILED", f"DXF parsing failed: {exc}")

    store.update_project(pid, state="CLASSIFYING")
    classification.classify_document(doc)
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

    for p in rec["placements"]:
        pl = Placement(id=p["id"], kind=p["kind"], speciesId=p["speciesId"],
                       x=p["position"][0], y=p["position"][1],
                       crownRadiusM=p["crownRadiusM"], score=p["score"])
        exp = build_explanation(pl, rec["index"], registry, oracle,
                                constraints.site_boundary)
        failing = []
        checks = []
        for c in exp["checks"]:
            if c["result"] == "n/a":
                continue
            checks_total += 1
            doc_name = c["source"]["document"]
            bd = by_document.setdefault(doc_name, {"checks": 0, "passed": 0, "failed": 0})
            br = by_rule.setdefault(c["ruleId"], {
                "checks": 0, "passed": 0, "failed": 0,
                "document": doc_name, "effectiveSource": c["effectiveSource"],
                "requiredM": c["requiredM"]})
            bd["checks"] += 1
            br["checks"] += 1
            if c["result"] == "passed":
                bd["passed"] += 1
                br["passed"] += 1
            else:
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
            "failingRules": failing, "checks": checks,
        })

    reg_rules = [r for r in registry.rules if r.id.startswith("REG_")]
    return {
        "iterationId": rec["iterationId"],
        "rulesetVersion": registry.version,
        "documentDerivedRules": len(reg_rules),
        "verifiedRules": sum(1 for r in registry.rules if r.effective_source == "verified"),
        "assumedRules": sum(1 for r in registry.rules if r.effective_source == "assumption"),
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
                 "metrics", "judge", "runtimeMs")


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
                            constraints.site_boundary)

    # optional local-LLM narrative from deterministic facts only
    llm = LocalLLM()
    if llm.enabled and await llm.is_reachable():
        try:
            from pydantic import BaseModel as BM

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
def export_dxf(pid: str):
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
    out = config.OUTPUT_DIR / f"{stem}_greening.dxf"
    src_dxf = Path(proj["dxf_path"])
    if not src_dxf.exists():  # e.g. server disk replaced — restore from DB blob
        if not store.restore_file_blob(pid, "source_dxf", src_dxf):
            _err(409, "SOURCE_MISSING", "Original DXF unavailable for export.")
    dxf_export.export_greening_dxf(
        src_dxf, out, placements,
        constraints.allowed_area, constraints.exclusion_zones,
        rt.doc.unitsScaleToMeters, rec["index"], registry.version)
    return FileResponse(out, filename=out.name, media_type="application/dxf")


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
                                              constraints.site_boundary))

    report = {
        "project": {"id": pid, "fileName": proj["file_name"],
                    "fileHash": proj["file_hash"], "units": rt.doc.units,
                    "cadVersion": rt.doc.cadVersion},
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
        "disclaimer": "Rules with status=missing_source_data use explicitly flagged ASSUMED "
                      "demo distances (see effectiveSource per check); they are NOT verified "
                      "regulatory values.",
    }
    stem = Path(proj["file_name"]).stem
    out = config.OUTPUT_DIR / f"{stem}_greening_report.json"
    dxf_export.export_report(out, report)
    return FileResponse(out, filename=out.name, media_type="application/json")
