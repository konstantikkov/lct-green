"""Shared offline pipeline invocation for evals and root-level tests.

Mirrors the exact invocation pattern of apps/api/tests/test_integration.py
(_run_pipeline) but is parameterized by DXF path + per-case rules.json.

IMPORTANT: imports only stable service APIs from apps/api/app — never
app.pipeline / app.services.judge* (owned by a concurrent workstream).
"""
from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
API_DIR = REPO_ROOT / "apps" / "api"
for p in (str(API_DIR), str(REPO_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from shapely.geometry import Point  # noqa: E402
from shapely.prepared import prep  # noqa: E402

from app.services import classification  # noqa: E402
from app.services.candidates import DistanceOracle, generate_candidates  # noqa: E402
from app.services.constraints import build_constraints  # noqa: E402
from app.services.dxf_export import export_greening_dxf  # noqa: E402
from app.services.dxf_parser import parse_dxf  # noqa: E402
from app.services.geometry import as_polygon_list, build_geometry_index  # noqa: E402
from app.services.optimizer import (OptimizerParams, PlantSpec, auto_target,  # noqa: E402
                                    hard_filter_candidates, optimize)
from app.services.rules import RuleRegistry, load_registry  # noqa: E402
from app.services.validator import Placement, RuleDistanceOracle, validate_plan  # noqa: E402

DATASET_DIR = REPO_ROOT / "evals" / "dataset"
DEFAULT_SEED = 42
DEFAULT_SPECIES = {"speciesId": "tree_linden", "kind": "TREE",
                   "crownRadiusM": 3.5, "spacingM": 5.0}


@dataclass
class PipelineResult:
    case_id: str
    dxf_path: Path
    seed: int
    doc: Any
    idx: Any
    registry: RuleRegistry
    cons: Any
    placements: list[Placement]
    validation: dict
    species: dict = field(default_factory=lambda: dict(DEFAULT_SPECIES))
    rules_config: dict = field(default_factory=dict)
    _export_path: Optional[Path] = None

    @property
    def metrics(self) -> dict:
        return self.validation["metrics"]

    def export(self) -> Path:
        """Export the greening DXF once per result (lazily, to a temp dir)."""
        if self._export_path is None or not self._export_path.exists():
            out_dir = Path(tempfile.mkdtemp(prefix=f"greenplan_eval_{self.case_id}_"))
            out = out_dir / f"{self.dxf_path.stem}_greening.dxf"
            export_greening_dxf(self.dxf_path, out, self.placements,
                                self.cons.allowed_area, self.cons.exclusion_zones,
                                self.doc.unitsScaleToMeters, 0, self.registry.version)
            self._export_path = out
        return self._export_path


def apply_rules_config(registry: RuleRegistry, cfg: dict) -> RuleRegistry:
    """Apply a case rules.json (subset + overrides) to a loaded registry.

    cfg schema:
      restrictToRules: null | [rule ids]  — keep only these rules
      overrides: {ruleId: {"enabled": bool, "assumption":
                           {"enabled": bool, "assumedDistanceM": float}}}
    Unknown rule ids raise (dataset must reference real registry rules).
    """
    known = {r.id for r in registry.rules}
    restrict = cfg.get("restrictToRules")
    if restrict:
        missing = set(restrict) - known
        if missing:
            raise ValueError(f"rules.json references unknown rule ids: {sorted(missing)}")
        registry.rules = [r for r in registry.rules if r.id in set(restrict)]
    for rid, patch in (cfg.get("overrides") or {}).items():
        rule = registry.get(rid)
        if rule is None:
            raise ValueError(f"rules.json override references unknown rule id: {rid}")
        if "enabled" in patch:
            rule.enabled = bool(patch["enabled"])
        ap = patch.get("assumption")
        if ap and rule.assumption:
            if ap.get("enabled") is not None:
                rule.assumption.enabled = bool(ap["enabled"])
            if ap.get("assumedDistanceM") is not None:
                rule.assumption.assumedDistanceM = float(ap["assumedDistanceM"])
    return registry


def run_pipeline(dxf_path: Path, rules_config: dict | None = None,
                 seed: int | None = None, case_id: str = "adhoc") -> PipelineResult:
    """Offline pipeline: parse -> classify -> constraints -> candidates ->
    optimize -> validate. Same steps/order as the integration test."""
    cfg = dict(rules_config or {})
    seed = int(seed if seed is not None else cfg.get("seed", DEFAULT_SEED))
    species = dict(DEFAULT_SPECIES)
    species.update(cfg.get("species") or {})

    doc = parse_dxf(dxf_path, f"eval-{case_id}", dxf_path.name)
    classification.classify_document(doc)
    idx = build_geometry_index(doc)
    registry = apply_rules_config(load_registry(), cfg)
    cons = build_constraints(doc, idx, registry)
    allowed = cons.allowed_area
    oracle = DistanceOracle(idx, cons.site_boundary)
    rule_oracle = RuleDistanceOracle(idx)

    spacing = float(species["spacingM"])
    cands = generate_candidates(cons.site_boundary, oracle, spacing, seed)
    prepared = prep(allowed)
    for c in cands:
        if not prepared.contains(Point(c.x, c.y)):
            c.valid = False
            c.rejected_by = "OUTSIDE_ALLOWED_AREA"
    hard_filter_candidates(cands, species["kind"], registry, rule_oracle)

    params = OptimizerParams.for_strategy("balanced")
    area = sum(p.area for p in as_polygon_list(allowed))
    params.targetCount = auto_target(area, spacing, sum(1 for c in cands if c.valid))
    spec = PlantSpec(speciesId=species["speciesId"], kind=species["kind"],
                     crownRadiusM=float(species["crownRadiusM"]), spacingM=spacing)
    chosen = optimize(cands, spec, params, [], seed)
    placements = [Placement(id=f"t{i}", kind=species["kind"], speciesId=species["speciesId"],
                            x=c.x, y=c.y, crownRadiusM=float(species["crownRadiusM"]),
                            score=c.score)
                  for i, c in enumerate(chosen)]
    validation = validate_plan(placements, idx, registry, cons.site_boundary, allowed)
    return PipelineResult(case_id=case_id, dxf_path=dxf_path, seed=seed, doc=doc,
                          idx=idx, registry=registry, cons=cons, placements=placements,
                          validation=validation, species=species, rules_config=cfg)


def list_cases(dataset_dir: Path = DATASET_DIR) -> list[Path]:
    return sorted(d for d in dataset_dir.iterdir()
                  if d.is_dir() and d.name.startswith("case_"))


def load_case_files(case_dir: Path) -> tuple[Path, dict, dict]:
    dxf = case_dir / "input.dxf"
    rules_cfg = json.loads((case_dir / "rules.json").read_text(encoding="utf-8"))
    expected_path = case_dir / "expected.json"
    expected = (json.loads(expected_path.read_text(encoding="utf-8"))
                if expected_path.exists() else {})
    return dxf, rules_cfg, expected


def run_case_uncached(case_dir: Path, seed: int | None = None) -> PipelineResult:
    dxf, rules_cfg, _ = load_case_files(case_dir)
    if not dxf.exists():  # regenerate deterministically
        from evals.dataset.generate import build_case
        build_case(case_dir.name, case_dir)
    return run_pipeline(dxf, rules_cfg, seed=seed, case_id=case_dir.name)


@lru_cache(maxsize=64)
def _run_case_cached(case_dir_str: str, seed: Optional[int]) -> PipelineResult:
    return run_case_uncached(Path(case_dir_str), seed)


def run_case(case_dir: Path, seed: int | None = None) -> PipelineResult:
    """Cached per-process case run (tests + runner share one execution)."""
    return _run_case_cached(str(case_dir), seed)
