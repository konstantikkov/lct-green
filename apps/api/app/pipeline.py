"""Generation pipeline orchestrator + in-memory project cache.

Explicit state machine:
UPLOADED → CONVERTING → PARSING → CLASSIFYING → BUILDING_CONSTRAINTS →
GENERATING_CANDIDATES → OPTIMIZING → VALIDATING → JUDGING → (RERUNNING …) → COMPLETED
"""
from __future__ import annotations

import asyncio
import hashlib
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config, store
from .models.ir import CadDocument
from .services import chunking, classification
from .services.candidates import DistanceOracle, generate_candidates
from .services.constraints import ConstraintResult, build_constraints
from .services.geometry import GeometryIndex, as_polygon_list, build_geometry_index
from .services.judge import judge_iteration
from .services.llm import LocalLLM
from .services.optimizer import (OptimizerParams, PlantSpec, auto_target,
                                 hard_filter_candidates, optimize)
from .services.rules import RuleRegistry, load_registry, load_species, blacklist_check
from .services.validator import Placement, RuleDistanceOracle, validate_plan

STAGES = ["GENERATING_CANDIDATES", "OPTIMIZING", "VALIDATING", "JUDGING"]


@dataclass
class ProjectRuntime:
    doc: CadDocument
    geometry: GeometryIndex | None = None
    constraints: ConstraintResult | None = None
    chunks: list[dict] | None = None
    registry: RuleRegistry | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def invalidate_semantics(self) -> None:
        with self.lock:
            self.geometry = None
            self.constraints = None
            self.chunks = None


_runtimes: dict[str, ProjectRuntime] = {}
_run_status: dict[str, dict] = {}
_rt_lock = threading.Lock()


def put_runtime(pid: str, doc: CadDocument) -> ProjectRuntime:
    rt = ProjectRuntime(doc=doc)
    with _rt_lock:
        _runtimes[pid] = rt
    return rt


def get_runtime(pid: str) -> ProjectRuntime | None:
    with _rt_lock:
        rt = _runtimes.get(pid)
    if rt is None:
        # lazy reload from persisted document JSON
        data = store.load_json(pid, "document")
        if data is None:
            return None
        doc = CadDocument.model_validate(data)
        rt = put_runtime(pid, doc)
    return rt


def ensure_geometry(rt: ProjectRuntime) -> GeometryIndex:
    with rt.lock:
        if rt.geometry is None:
            rt.geometry = build_geometry_index(rt.doc)
        return rt.geometry


def ensure_registry(rt: ProjectRuntime) -> RuleRegistry:
    # reload each time overrides may have changed — cheap enough
    rt.registry = load_registry()
    return rt.registry


def ensure_constraints(rt: ProjectRuntime) -> ConstraintResult:
    idx = ensure_geometry(rt)
    registry = ensure_registry(rt)
    with rt.lock:
        if rt.constraints is None:
            rt.constraints = build_constraints(rt.doc, idx, registry)
        return rt.constraints


def ensure_chunks(rt: ProjectRuntime) -> list[dict]:
    with rt.lock:
        if rt.chunks is None:
            rt.chunks = chunking.build_chunks(rt.doc)
        return rt.chunks


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Run status tracking (SSE/poll)
# ---------------------------------------------------------------------------

def _init_status(run_id: str) -> dict:
    st = {
        "runId": run_id, "state": "GENERATING_CANDIDATES",
        "stages": [{"stage": s, "status": "pending", "detail": ""} for s in STAGES],
        "currentIteration": 0, "error": None, "_version": 0,
    }
    _run_status[run_id] = st
    return st


def _set_stage(run_id: str, stage: str, status: str, detail: str = "") -> None:
    st = _run_status.get(run_id)
    if not st:
        return
    st["state"] = stage if status != "done" else st["state"]
    for s in st["stages"]:
        if s["stage"] == stage:
            s["status"] = status
            if detail:
                s["detail"] = detail
    if status == "active":
        st["state"] = stage
    st["_version"] += 1


def get_run_status(run_id: str) -> dict | None:
    st = _run_status.get(run_id)
    if st is None:
        return None
    return {k: v for k, v in st.items() if not k.startswith("_")}


# ---------------------------------------------------------------------------
# Generation run
# ---------------------------------------------------------------------------

def start_generation(pid: str, params: dict) -> str:
    run_id = store.create_run(pid, params)
    _init_status(run_id)
    t = threading.Thread(target=_run_generation, args=(pid, run_id, params), daemon=True)
    t.start()
    return run_id


def _reset_stages(run_id: str, iteration: int) -> None:
    st = _run_status.get(run_id)
    if st:
        st["currentIteration"] = iteration
        for s in st["stages"]:
            s["status"] = "pending"
        st["_version"] += 1


def _run_generation(pid: str, run_id: str, req: dict) -> None:
    try:
        rt = get_runtime(pid)
        if rt is None:
            raise RuntimeError("project not loaded")
        registry = ensure_registry(rt)
        idx = ensure_geometry(rt)
        constraints = ensure_constraints(rt)
        chunks = ensure_chunks(rt)
        species_all = {s["id"]: s for s in load_species()}

        seed = int(req.get("seed", config.PLACEMENT_RANDOM_SEED))
        max_iter = min(6, max(1, int(req.get("maxIterations", config.MAX_ITERATIONS_DEFAULT))))
        strategy = req.get("strategy", "balanced")
        use_judge = bool(req.get("useLlmJudge", True))
        kinds = req.get("plantKinds") or ["TREE"]
        wanted_species = req.get("speciesIds") or []

        # species selection with blacklist enforcement (only when verified list exists)
        selection: list[dict] = []
        rejected_species: list[dict] = []
        for sid in (wanted_species or ["tree_linden", "shrub_lilac"]):
            sp = species_all.get(sid)
            if not sp or sp["kind"] not in kinds:
                continue
            ok, rule_id = blacklist_check(registry, sp["id"], sp.get("latin", ""))
            if ok:
                selection.append(sp)
            else:
                rejected_species.append({"speciesId": sid, "ruleId": rule_id})
        if not selection:
            for sp in species_all.values():
                if sp["kind"] in kinds:
                    selection.append(sp)
                    break

        params = OptimizerParams.for_strategy(strategy)
        if req.get("targetCount"):
            params.targetCount = int(req["targetCount"])

        llm = LocalLLM()
        allowed = constraints.allowed_area
        allowed_polys = as_polygon_list(allowed)
        constraint_summary = {
            "exclusionZoneCount": len(constraints.exclusion_zones),
            "allowedAreaM2": round(sum(p.area for p in allowed_polys), 1),
            "allowedPolygonCount": len(allowed_polys),
            "inactiveRules": constraints.inactive_rules[:10],
            "siteBoundarySource": constraints.site_boundary_source,
        }
        oracle = DistanceOracle(idx, constraints.site_boundary)
        rule_oracle = RuleDistanceOracle(idx)

        existing_by_kind = {
            "TREE": [(g.centroid.x, g.centroid.y) for _, g in idx.geoms_for(["EXISTING_TREE"])],
            "SHRUB": [(g.centroid.x, g.centroid.y) for _, g in idx.geoms_for(["EXISTING_SHRUB"])],
        }

        iterations: list[dict] = []
        selected_idx: int | None = None

        for it in range(max_iter):
            t0 = time.time()
            _reset_stages(run_id, it)
            it_seed = seed + it  # deterministic per-iteration seed

            # --- candidates ---
            _set_stage(run_id, "GENERATING_CANDIDATES", "active")
            all_placements: list[Placement] = []
            rejected_pts: list[list[float]] = []
            candidate_total = 0
            per_kind_valid: dict[str, int] = {}

            placements_pts: list[tuple[float, float]] = []
            from shapely.prepared import prep
            allowed_prepared = prep(allowed) if not allowed.is_empty else None
            allowed_m2 = sum(p.area for p in allowed_polys)
            # sample slightly beyond the allowed area (into exclusion buffers) so
            # rejected candidates are real and visible, but never the whole map
            base_domain = constraints.planting_domain if constraints.planting_domain is not None else allowed
            site_domain = base_domain.buffer(6.0).intersection(
                constraints.site_boundary) if constraints.site_boundary is not None else base_domain.buffer(6.0)

            for sp in selection:
                kind = sp["kind"]
                spacing_rule = next((r for r in registry.rules_for(kind, {"min_spacing"})
                                     if r.effective_distance_m), None)
                spacing = spacing_rule.effective_distance_m if spacing_rule else (5.0 if kind == "TREE" else 1.0)
                # sample across the whole site so exclusion-zone rejections are
                # real (and visible); shrubs use a coarser sampling radius
                sampling_spacing = max(spacing, 2.0 if kind == "SHRUB" else spacing)
                cands = generate_candidates(site_domain, oracle, sampling_spacing, it_seed,
                                            density=params.candidateDensity)
                candidate_total += len(cands)
                _set_stage(run_id, "GENERATING_CANDIDATES", "active",
                           f"{candidate_total} candidates")

                from shapely.geometry import Point as _P
                for c in cands:
                    if allowed_prepared is None or not allowed_prepared.contains(_P(c.x, c.y)):
                        c.valid = False
                        c.rejected_by = "OUTSIDE_ALLOWED_AREA"
                hard_filter_candidates(cands, kind, registry, rule_oracle)
                valid_n = sum(1 for c in cands if c.valid)
                per_kind_valid[kind] = per_kind_valid.get(kind, 0) + valid_n
                rejected_pts.extend([[round(c.x, 2), round(c.y, 2)] for c in cands if not c.valid][:4000])

                # --- optimize ---
                _set_stage(run_id, "GENERATING_CANDIDATES", "done",
                           f"{candidate_total} candidates, {sum(per_kind_valid.values())} valid")
                _set_stage(run_id, "OPTIMIZING", "active")
                spec = PlantSpec(speciesId=sp["id"], kind=kind,
                                 crownRadiusM=sp["parameters"]["crownRadiusM"],
                                 spacingM=spacing)
                n_kind = sum(1 for s in selection if s["kind"] == kind)
                if params.targetCount:
                    target_per_species = max(1, params.targetCount // max(1, n_kind))
                else:
                    target_per_species = max(1, auto_target(allowed_m2, spacing, valid_n)
                                             // max(1, n_kind))
                p2 = OptimizerParams(**{**params.__dict__, "targetCount": target_per_species})
                # same-kind spacing applies against existing vegetation AND
                # already-placed plantings of the same kind (other species)
                kind_pts = existing_by_kind.get(kind, []) + [
                    (p.x, p.y) for p in all_placements if p.kind == kind]
                chosen = optimize(cands, spec, p2, kind_pts, it_seed,
                                  progress=lambda n: _set_stage(
                                      run_id, "OPTIMIZING", "active",
                                      f"{len(all_placements) + n} placements"))
                for i, c in enumerate(chosen):
                    all_placements.append(Placement(
                        id=f"{sp['id']}_{it}_{i}", kind=kind, speciesId=sp["id"],
                        x=c.x, y=c.y, crownRadiusM=sp["parameters"]["crownRadiusM"],
                        score=c.score, distances={
                            "building": c.features.get("building"),
                            "road": c.features.get("road"),
                            "sidewalk": c.features.get("sidewalk"),
                            "utility": c.features.get("utility"),
                            "boundary": c.features.get("boundary"),
                            "existing_tree": c.features.get("existing_tree"),
                        }))
                    placements_pts.append((c.x, c.y))
            _set_stage(run_id, "OPTIMIZING", "done", f"{len(all_placements)} placements selected")

            # nearest-planting distance
            for p in all_placements:
                dmin = None
                for q in all_placements:
                    if q is p:
                        continue
                    d = ((p.x - q.x) ** 2 + (p.y - q.y) ** 2) ** 0.5
                    dmin = d if dmin is None or d < dmin else dmin
                p.distances["nearest_planting"] = dmin

            # --- validate ---
            _set_stage(run_id, "VALIDATING", "active")
            validation = validate_plan(all_placements, idx, registry,
                                       constraints.site_boundary, allowed)
            _set_stage(run_id, "VALIDATING", "done",
                       f"{len(validation['hardViolations'])} hard violations")

            # --- judge ---
            _set_stage(run_id, "JUDGING", "active")
            judge_report = None
            if use_judge:
                judge_report = asyncio.run(judge_iteration(
                    llm, strategy, params, validation["metrics"], validation,
                    constraint_summary, chunking.chunks_llm_summary(chunks, max_chunks=8),
                    it, max_iter))
            _set_stage(run_id, "JUDGING", "done",
                       (judge_report or {}).get("decision", "skipped"))

            iteration_id = f"{run_id}_it{it}"
            iter_record = {
                "iterationId": iteration_id, "index": it, "runId": run_id,
                "selected": False,
                "parameters": params.to_api(), "seed": it_seed,
                "metrics": {**validation["metrics"]},
                "judge": judge_report,
                "runtimeMs": int((time.time() - t0) * 1000),
                "placements": [p.to_api() for p in all_placements],
                "rejectedCandidates": rejected_pts[:4000],
                "candidateCount": candidate_total,
                "validation": validation,
                "rejectedSpecies": rejected_species,
                "observability": {
                    "algorithmVersion": config.ALGORITHM_VERSION,
                    "parserVersion": config.PARSER_VERSION,
                    "rulesetVersion": registry.version,
                    "llmModel": (judge_report or {}).get("llmModel"),
                    "judgeMode": (judge_report or {}).get("mode"),
                },
            }
            iterations.append(iter_record)
            store.save_json(pid, f"iteration_{run_id}_{it}", iter_record)

            # selection: compliance not worse AND objective better
            def obj(rec):
                m = rec["metrics"]
                return m["coverageScore"] * 0.5 + m["distributionScore"] * 0.5
            if selected_idx is None:
                selected_idx = 0
            else:
                cur = iterations[selected_idx]
                if (len(iter_record["validation"]["hardViolations"]) <=
                        len(cur["validation"]["hardViolations"]) and obj(iter_record) > obj(cur)):
                    selected_idx = it

            decision = (judge_report or {}).get("decision", "ACCEPT")
            if decision != "RERUN" or it + 1 >= max_iter:
                break

            # apply safe parameter changes and rerun
            _set_stage(run_id, "JUDGING", "done", "RERUN requested")
            changes = (judge_report or {}).get("recommendedParameterChanges") or {}
            for k, v in changes.items():
                if hasattr(params, k) and isinstance(v, (int, float)):
                    setattr(params, k, float(v))
            params = params.clamped()

        for i, rec in enumerate(iterations):
            rec["selected"] = (i == selected_idx)
            store.save_json(pid, f"iteration_{rec['runId']}_{rec['index']}", rec)

        st = _run_status.get(run_id)
        if st:
            st["state"] = "COMPLETED"
            st["_version"] += 1
        store.finish_run(run_id, "COMPLETED")
        store.update_project(pid, state="COMPLETED")
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        st = _run_status.get(run_id)
        if st:
            st["state"] = "FAILED"
            st["error"] = str(exc)
            st["_version"] += 1
        store.finish_run(run_id, "FAILED", str(exc))
