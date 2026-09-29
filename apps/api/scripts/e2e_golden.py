"""Golden metrics of end-to-end runs: per object, the numbers that describe the
result (what was read, areas, counts, verdict, main rejection reasons, the
comparison with the designers' plan). A run is compared with the recorded
golden file; a change beyond the tolerances is reported as drift, so every
change of results shows up in the diff of a commit instead of by chance.
Record a new golden with `e2e_run.py … --update-golden`.
"""
from __future__ import annotations

import json
from pathlib import Path

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "tests" / "golden" / "e2e"

# name → (kind, tolerance): exact | rel (fraction) | abs (units) | set
TOLERANCES = {
    "sources": ("exact", None),
    "sourcesNotMerged": ("exact", None),
    "status": ("exact", None),
    "blockers": ("exact", None),
    "trees": ("rel", 0.05),
    "shrubs": ("rel", 0.05),
    "allowedAreaM2": ("rel", 0.02),
    "lawnM2": ("rel", 0.02),
    "rejected": ("rel", 0.10),
    "topReasons3": ("set", None),
    "refTreesBreakingNormsPct": ("abs", 3.0),
    "refShrubsBreakingNormsPct": ("abs", 3.0),
    "refTreesInOurAllowedArea": ("rel", 0.05),
}


def metrics(row: dict) -> dict:
    ref = row.get("reference") or {}
    breaking = ref.get("referenceBreakingNorms") or {}
    m = {
        "sources": row.get("sources"),
        "sourcesNotMerged": sorted(row.get("sourcesNotMerged") or []),
        "status": row.get("status"),
        "blockers": row.get("blockers"),
        "trees": row.get("trees"),
        "shrubs": row.get("shrubs"),
        "allowedAreaM2": row.get("allowedAreaM2"),
        "lawnM2": row.get("lawnM2"),
        "rejected": row.get("rejected"),
        "topReasons3": [k for k, _ in (row.get("topReasons") or [])[:3]],
    }
    if breaking:
        m["refTreesBreakingNormsPct"] = (breaking.get("trees") or {}).get("percent")
        m["refShrubsBreakingNormsPct"] = (breaking.get("shrubs") or {}).get("percent")
        m["refTreesInOurAllowedArea"] = ref.get("referenceTreesInOurAllowedArea")
    return m


def compare(golden: dict, current: dict) -> list[str]:
    drift = []
    for name, (kind, tol) in TOLERANCES.items():
        if name not in golden:
            continue
        g, c = golden.get(name), current.get(name)
        if kind == "exact" and g != c:
            drift.append(f"{name}: {g} → {c}")
        elif kind == "set" and set(g or []) != set(c or []):
            drift.append(f"{name}: {g} → {c}")
        elif kind in ("rel", "abs"):
            if g is None or c is None:
                if g != c:
                    drift.append(f"{name}: {g} → {c}")
                continue
            delta = abs(c - g)
            limit = tol * max(abs(g), 1e-9) if kind == "rel" else tol
            if delta > limit:
                drift.append(f"{name}: {g} → {c} (tolerance {'±' + format(tol * 100, 'g') + ' %' if kind == 'rel' else '±' + format(tol, 'g')})")
    return drift


def check(name: str, row: dict, golden_dir: Path = GOLDEN_DIR, update: bool = False) -> tuple[list[str], str]:
    """(drift, note). With update=True the current metrics become the golden."""
    path = golden_dir / f"{name}.json"
    current = metrics(row)
    if update:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(current, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        return [], f"golden recorded: {path.name}"
    if not path.exists():
        return [], "no golden yet (record with --update-golden)"
    return compare(json.loads(path.read_text(encoding="utf-8")), current), ""
