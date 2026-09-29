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
from .services.judge import judge_iteration, should_select_rerun
from .services.judge_packet import build_judge_packet
from .services.llm import LocalLLM
from .services.optimizer import (OptimizerParams, PlantSpec, auto_target,
                                 hard_filter_candidates, optimize)
from .services.rules import RuleRegistry, load_registry, load_species, blacklist_check
from .services.rejections import explain_rejections
from .services.validator import Placement, RuleDistanceOracle, validate_plan

STAGES = ["GENERATING_CANDIDATES", "OPTIMIZING", "VALIDATING", "JUDGING"]

# coarse per-iteration progress mapping (percent at stage start / end)
_STAGE_PROGRESS = {"GENERATING_CANDIDATES": (5, 40), "OPTIMIZING": (40, 72),
                   "VALIDATING": (72, 82), "JUDGING": (82, 95)}


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
        "currentIteration": 0, "error": None, "progress": 0, "_version": 0,
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
    span = _STAGE_PROGRESS.get(stage)
    if span:
        st["progress"] = span[0] if status == "active" else span[1]
    st["_version"] += 1


def get_run_status(run_id: str) -> dict | None:
    st = _run_status.get(run_id)
    if st is None:
        return None
    return {k: v for k, v in st.items() if not k.startswith("_")}


# ---------------------------------------------------------------------------
# Deterministic repair — judge precondition
# ---------------------------------------------------------------------------

def deterministic_repair(placements: list[Placement], idx: GeometryIndex,
                         registry: RuleRegistry, site_boundary, allowed_area,
                         validation: dict, max_passes: int = 3,
                         site_boundary_source: str = "detected",
                         visibility_zones: list | None = None
                         ) -> tuple[list[Placement], dict, dict]:
    """Removes placements named in hardViolations and revalidates, up to
    `max_passes` times or until valid. The judge runs only on valid plans;
    this is the deterministic path back to validity — never the LLM's job.

    Returns (placements, validation, {"removed": n, "passes": k})."""
    removed = 0
    passes = 0
    while validation["hardViolations"] and passes < max_passes:
        bad = {v["plantingId"] for v in validation["hardViolations"]}
        before = len(placements)
        placements = [p for p in placements if p.id not in bad]
        removed += before - len(placements)
        passes += 1
        validation = validate_plan(placements, idx, registry, site_boundary,
                                   allowed_area,
                                   site_boundary_source=site_boundary_source,
                                   visibility_zones=visibility_zones)
    return placements, validation, {"removed": removed, "passes": passes}


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

        # classification uncertainty surfaced to the judge (deterministic list)
        uncertain_layers = [
            {"name": l.name, "category": l.classification.category,
             "confidence": l.classification.confidence}
            for l in rt.doc.layers if l.classification.requiresReview]

        iterations: list[dict] = []
        selected_idx: int | None = None

        for it in range(max_iter):
            t0 = time.time()
            _reset_stages(run_id, it)
            it_seed = seed + it  # deterministic per-iteration seed
            it_min_spacing: float | None = None  # cluster-link scale for diagnostics

            # --- candidates ---
            _set_stage(run_id, "GENERATING_CANDIDATES", "active")
            all_placements: list[Placement] = []
            rejected_pts: list[list[float]] = []
            rejected_all: list = []  # every rejected candidate → reasons with norms
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
                it_min_spacing = spacing if it_min_spacing is None else min(it_min_spacing, spacing)
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
                hard_filter_candidates(cands, kind, registry, rule_oracle, species_id=sp["id"])
                valid_n = sum(1 for c in cands if c.valid)
                per_kind_valid[kind] = per_kind_valid.get(kind, 0) + valid_n
                rejected_pts.extend([[round(c.x, 2), round(c.y, 2)] for c in cands if not c.valid][:4000])
                from .services.rejections import Rejected
                rejected_all.extend(Rejected(c.x, c.y, sp["id"], kind, c.rejected_by)
                                    for c in cands if not c.valid)

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
            vis_zones = constraints.visibility_zones()
            validation = validate_plan(all_placements, idx, registry,
                                       constraints.site_boundary, allowed,
                                       site_boundary_source=constraints.site_boundary_kind,
                                       visibility_zones=vis_zones)
            # precondition for the judge: valid plans only. Hard violations are
            # repaired deterministically (drop violators, revalidate, ≤3 passes).
            repair_info = None
            if validation["hardViolations"]:
                all_placements, validation, repair_info = deterministic_repair(
                    all_placements, idx, registry, constraints.site_boundary,
                    allowed, validation,
                    site_boundary_source=constraints.site_boundary_kind,
                    visibility_zones=vis_zones)
            _set_stage(run_id, "VALIDATING", "done",
                       f"{len(validation['hardViolations'])} hard violations"
                       + (f" (repair removed {repair_info['removed']})" if repair_info else ""))

            # --- judge (only on deterministically valid plans) ---
            _set_stage(run_id, "JUDGING", "active")
            judge_report = None
            judge_skipped = None
            if not validation["valid"]:
                judge_skipped = "hard_violations"
            elif use_judge:
                prev_metrics = iterations[-1]["metrics"] if iterations else None
                packet = build_judge_packet(
                    objective=strategy, iteration=it, max_iterations=max_iter,
                    params=params, validation=validation, allowed_area=allowed,
                    placements=all_placements,
                    spacing_m=it_min_spacing or 5.0,
                    classification_uncertainty=uncertain_layers,
                    previous_metrics=prev_metrics)
                judge_report = asyncio.run(judge_iteration(llm, packet, it, max_iter))
            _set_stage(run_id, "JUDGING", "done",
                       (judge_report or {}).get("decision", "skipped"))

            # lawn (ТЗ §2 п. 3): the unpaved domain outside the shrub beds
            from .services.lawn import LAWN_RULE_ID, recommend_lawn
            lawn_rule = registry.get(LAWN_RULE_ID)
            _, lawn_summary = recommend_lawn(
                constraints, idx, all_placements,
                min_width_m=float((lawn_rule.constraint if lawn_rule else {}).get("min_width_m", 1.0)))

            decision = (judge_report or {}).get("decision", "ACCEPT")
            iteration_id = f"{run_id}_it{it}"
            iter_record = {
                "iterationId": iteration_id, "index": it, "runId": run_id,
                "selected": False,
                "parameters": params.to_api(), "seed": it_seed,
                "metrics": {**validation["metrics"]},
                # deterministic regulatory verdict (validator) — the judge can
                # never change it; UI/report labels derive from this, not from
                # the judge summary
                "regulatory": validation.get("regulatory"),
                "judge": judge_report,
                "judgeSkipped": judge_skipped,
                "deterministicRepair": repair_info,
                "needsManualReview": decision == "MANUAL_REVIEW",
                "runtimeMs": int((time.time() - t0) * 1000),
                "placements": [p.to_api() for p in all_placements],
                "rejectedCandidates": rejected_pts[:4000],
                # ТЗ п. 8: why each rejected candidate may not be planted, with
                # the norm (counts per reason + evenly spaced samples)
                "rejections": explain_rejections(rejected_all, constraints, registry, idx),
                "lawn": lawn_summary,
                "candidateCount": candidate_total,
                "validation": validation,
                "rejectedSpecies": rejected_species,
                "observability": {
                    "algorithmVersion": config.ALGORITHM_VERSION,
                    "parserVersion": config.PARSER_VERSION,
                    "rulesetVersion": registry.version,
                    "llmModel": (judge_report or {}).get("llmModel"),
                    "judgeMode": (judge_report or {}).get("mode"),
                    "judgePromptVersion": (judge_report or {}).get("promptVersion"),
                    "judgeInputHash": (judge_report or {}).get("judgeInputHash"),
                },
            }
            iterations.append(iter_record)
            store.save_json(pid, f"iteration_{run_id}_{it}", iter_record)

            # retention rule: a rerun replaces the selection ONLY if it is valid,
            # the objective improves ≥ 0.02 and no component drops > 0.05.
            if selected_idx is None:
                selected_idx = 0
            else:
                cur = iterations[selected_idx]
                if should_select_rerun(cur["metrics"], cur["validation"]["valid"],
                                       iter_record["metrics"],
                                       iter_record["validation"]["valid"]):
                    selected_idx = it

            if decision == "MANUAL_REVIEW":
                # abstention is terminal: flag for a human, never rerun
                _set_stage(run_id, "JUDGING", "done", "MANUAL_REVIEW — human look needed")
                break
            if decision != "RERUN" or it + 1 >= max_iter:
                break

            # apply safe parameter changes (already whitelist/range/delta-clamped
            # by the judge post-validation) and rerun
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
            st["progress"] = 100
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
